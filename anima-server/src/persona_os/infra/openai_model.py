"""OpenAI-compatible model adapter for the two persona calls. One request per attempt; the
engine owns retries (rules §4) and records every attempt."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from persona_os.config import Settings
from persona_os.distill.ports import (
    ExtractInput,
    ExtractOutput,
    InduceInput,
    InduceOutput,
    ReviewInput,
    ReviewOutput,
    ReviseInput,
    ReviseOutput,
)
from persona_os.domain.contract import ActionOutput, PersonaState
from persona_os.domain.ports import (
    Consolidation,
    ConsolidationInput,
    GenerationDraft,
    GenerationInput,
    JudgeInput,
    JudgeOutput,
    ModelResult,
    OnFinal,
    ReflectionInput,
    ReflectionOutput,
    RespondInput,
    Response,
    StateInput,
)
from persona_os.reflection_ports import (
    ReflectSelfInput,
    ReflectSelfOutput,
    ReflectVersionsInput,
    ReflectVersionsOutput,
)

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


class ModelCallError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool, *, prompt_version: str = "",
                 fingerprint: str = "", latency_ms: int = 0, prompt_tokens: int = 0,
                 completion_tokens: int = 0) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.retryable = retryable
        self.prompt_version = prompt_version
        self.fingerprint = fingerprint
        self.latency_ms = latency_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def rejected(exc: APIStatusError) -> str | None:
    """The `REJECTED` kind of a refusal by the service, or None for a passing failure."""
    text = str(exc.message).lower()
    if exc.status_code in (401, 403):
        return "auth"
    if exc.status_code == 402 or (exc.status_code == 429 and any(
            word in text for word in ("quota", "insufficient", "balance", "余额", "欠费"))):
        return "quota"
    if exc.status_code == 404 or (exc.status_code == 400 and "model" in text and any(
            word in text for word in ("not exist", "not found", "does not", "unknown"))):
        return "not_found"  # TokenHub answers an unknown model with 400
    return None


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


_FINAL = re.compile(r'"final"\s*:\s*\{')


def _closed_object(text: str, key: re.Pattern) -> str | None:
    """The JSON object after `key` once its closing brace has arrived (strings respected)."""
    m = key.search(text)
    if not m:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(m.end() - 1, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[m.end() - 1:i + 1]
    return None


def _json_text(content: str) -> str:
    match = re.search(r"\{.*\}", content, re.S)
    return match.group(0) if match else content


def http_client(settings: Settings) -> httpx.AsyncClient:
    """Direct to the model service unless `model_proxy` says otherwise: trust_env=False keeps the
    system proxy (macOS network settings) and *_PROXY variables out of it."""
    return httpx.AsyncClient(trust_env=False, proxy=settings.model_proxy or None,
                             timeout=httpx.Timeout(settings.model_timeout_seconds, connect=10))


class OpenAIPersonaModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value() or "missing",
                                  base_url=settings.openai_base_url, max_retries=0,
                                  http_client=http_client(settings))
        self.limit = asyncio.Semaphore(settings.max_concurrent_model_calls)
        method = _prompt("persona_method.md")
        self.systems = {
            "infer_state": method + "\n\n" + _prompt("state_inference.md"),
            "generate_and_review": method + "\n\n" + _prompt("generate_review.md"),
            "respond": method + "\n\n" + _prompt("respond.md"),
            "consolidate": method + "\n\n" + _prompt("consolidate.md"),
            "reflect": method + "\n\n" + _prompt("reflect.md"),
            "judge_tests": _prompt("judge_tests.md"),
            "distill_extract": _prompt("distill_extract.md"),
            "distill_induce": _prompt("distill_induce.md"),
            "distill_review": _prompt("distill_review.md"),
            "evolve_extract": _prompt("evolve_extract.md"),
            "evolve_revise": _prompt("evolve_revise.md"),
            "evolve_review": _prompt("evolve_review.md"),
            "reflect_self": method + "\n\n" + _prompt("reflect_self.md"),
            "reflect_versions": method + "\n\n" + _prompt("reflect_versions.md"),
        }
        self.versions = {k: _version(v) for k, v in self.systems.items()}

    async def close(self) -> None:
        await self.client.close()

    async def _call(self, call: str, payload: BaseModel, schema: type[BaseModel],
                    temperature: float, on_final: OnFinal | None = None) -> ModelResult:
        """One JSON call. With `on_final` the answer is streamed and the "final" object is
        handed over as soon as it is complete, before the rest of the output arrives."""
        system = self.systems[call]
        user = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
        version = self.versions[call]
        fingerprint = hashlib.sha256(
            f"{self.settings.model}|{call}|{version}|{temperature}|{user}".encode()).hexdigest()
        started = time.perf_counter()

        def fail(kind: str, message: str, retryable: bool) -> ModelCallError:
            return ModelCallError(kind, message, retryable, prompt_version=version,
                                  fingerprint=fingerprint,
                                  latency_ms=round((time.perf_counter() - started) * 1000))

        if not self.settings.openai_api_key.get_secret_value():
            raise fail("not_configured", "PERSONA_OPENAI_API_KEY is empty", False)
        request = dict(
            model=self.settings.model, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            # TokenHub enables thinking by default for DeepSeek V4 models.
            **({"extra_body": {"thinking": {"type": "disabled"}}}
               if self.settings.disable_thinking else {}))

        async def streamed():
            parts, usage, model, sent = [], None, None, False
            stream = await self.client.chat.completions.create(
                **request, stream=True, stream_options={"include_usage": True})
            async for chunk in stream:
                usage, model = chunk.usage or usage, chunk.model or model
                if chunk.choices and chunk.choices[0].delta.content:
                    parts.append(chunk.choices[0].delta.content)
                    if not sent:
                        found = _closed_object("".join(parts), _FINAL)
                        if found is not None:
                            sent = True
                            try:
                                final = ActionOutput.model_validate_json(found)
                            except ValidationError:
                                continue  # the full parse below reports it
                            await on_final(final)
            return SimpleNamespace(usage=usage, model=model, choices=[SimpleNamespace(
                message=SimpleNamespace(content="".join(parts)))])

        try:
            async with self.limit:
                response = await asyncio.wait_for(
                    streamed() if on_final else self.client.chat.completions.create(**request),
                    timeout=self.settings.model_timeout_seconds)
        except TimeoutError as exc:
            raise fail("timeout", "model call timed out", True) from exc
        except APIStatusError as exc:
            if kind := rejected(exc):
                raise fail(kind, f"HTTP {exc.status_code}", False) from exc
            retryable = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            raise fail(f"http_{exc.status_code}", str(exc.message)[:150], retryable) from exc
        except Exception as exc:  # connection errors differ between providers
            raise fail("connection", type(exc).__name__, True) from exc
        content = response.choices[0].message.content if response.choices else ""
        try:
            value = schema.model_validate_json(_json_text(content or ""))
        except ValidationError as exc:
            first = exc.errors(include_input=False, include_url=False)[:3]
            raise fail("invalid_output", json.dumps(first, ensure_ascii=False)[:180], True) \
                from exc
        usage = response.usage
        return ModelResult(value, response.model or self.settings.model,
                           latency_ms=round((time.perf_counter() - started) * 1000),
                           prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                           completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                           prompt_version=version, fingerprint=fingerprint)

    async def infer_state(self, data: StateInput) -> ModelResult[PersonaState]:
        return await self._call("infer_state", data, PersonaState, self.settings.state_temperature)

    async def generate_and_review(self, data: GenerationInput) -> ModelResult[GenerationDraft]:
        return await self._call("generate_and_review", data, GenerationDraft,
                                self.settings.generation_temperature)

    async def respond(self, data: RespondInput,
                      on_final: OnFinal | None = None) -> ModelResult[Response]:
        return await self._call("respond", data, Response, self.settings.generation_temperature,
                                on_final)

    async def consolidate(self, data: ConsolidationInput) -> ModelResult[Consolidation]:
        return await self._call("consolidate", data, Consolidation,
                                self.settings.state_temperature)

    async def reflect(self, data: ReflectionInput) -> ModelResult[ReflectionOutput]:
        return await self._call("reflect", data, ReflectionOutput, self.settings.state_temperature)

    async def judge_tests(self, data: JudgeInput) -> ModelResult[JudgeOutput]:
        return await self._call("judge_tests", data, JudgeOutput, 0.0)

    async def extract_evidence(self, data: ExtractInput) -> ModelResult[ExtractOutput]:
        return await self._call("distill_extract", data, ExtractOutput,
                                self.settings.state_temperature)

    async def induce_beliefs(self, data: InduceInput) -> ModelResult[InduceOutput]:
        return await self._call("distill_induce", data, InduceOutput,
                                self.settings.state_temperature)

    async def review_beliefs(self, data: ReviewInput) -> ModelResult[ReviewOutput]:
        return await self._call("distill_review", data, ReviewOutput, 0.0)

    async def extract_changes(self, data: ExtractInput) -> ModelResult[ExtractOutput]:
        return await self._call("evolve_extract", data, ExtractOutput,
                                self.settings.state_temperature)

    async def revise_beliefs(self, data: ReviseInput) -> ModelResult[ReviseOutput]:
        return await self._call("evolve_revise", data, ReviseOutput,
                                self.settings.state_temperature)

    async def review_changes(self, data: ReviewInput) -> ModelResult[ReviewOutput]:
        return await self._call("evolve_review", data, ReviewOutput, 0.0)

    async def reflect_self(self, data: ReflectSelfInput) -> ModelResult[ReflectSelfOutput]:
        return await self._call("reflect_self", data, ReflectSelfOutput,
                                self.settings.generation_temperature)

    async def reflect_versions(self, data: ReflectVersionsInput
                               ) -> ModelResult[ReflectVersionsOutput]:
        return await self._call("reflect_versions", data, ReflectVersionsOutput,
                                self.settings.state_temperature)
