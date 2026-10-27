"""The owner's own model service (Anima settings page): an OpenAI-compatible endpoint, key and
model, saved next to the database and used at once without a restart.

Every component holds the same `ModelSwitch`; saving swaps the client inside it. The key is
written to a file only the owner can read (mode 600, under the git-ignored data folder), and is
never sent back: reads show whether a key is set and its last four characters.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from openai import APIStatusError, AsyncOpenAI
from pydantic import Field, SecretStr

from persona_os.config import Settings
from persona_os.domain.contract import StrictModel
from persona_os.domain.errors import REJECTED
from persona_os.infra.fake_model import FakePersonaModel
from persona_os.infra.openai_model import OpenAIPersonaModel, http_client, rejected


class ModelSwitch:
    """Stands in for the model; `swap` replaces the client behind it for every holder."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def swap(self, inner: Any) -> None:
        old, self.inner = self.inner, inner
        if hasattr(old, "close") and old is not inner:
            await old.close()

    async def close(self) -> None:
        if hasattr(self.inner, "close"):
            await self.inner.close()


class ModelSettingsView(StrictModel):
    backend: Literal["openai", "fake"]
    base_url: str
    model: str
    disable_thinking: bool
    key_set: bool
    ready: bool  # a key is set, or this is the offline fake used in development
    key_hint: str  # "…abcd", or "" when no key
    source: Literal["env", "saved"]  # .env / environment, or this settings page


class ModelSettingsUpdate(StrictModel):
    base_url: str = Field(min_length=8, max_length=300, pattern=r"^https?://")
    model: str = Field(min_length=1, max_length=120)
    # None keeps the saved key; "" removes it (model calls are then refused as not configured)
    api_key: SecretStr | None = None
    disable_thinking: bool = True


class ModelTestResult(StrictModel):
    ok: bool
    latency_ms: int
    message: str


class ModelSettings:
    def __init__(self, settings: Settings, switch: ModelSwitch, runner) -> None:
        self.base = settings  # the environment's settings, under any saved ones
        self.switch = switch
        self.runner = runner  # Engine: records the model name of each call
        self.path = Path(settings.model_file)
        self.saved: dict[str, Any] | None = self._read()
        self._lock = asyncio.Lock()

    def _read(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def effective(self) -> Settings:
        """The environment's settings with the saved model service on top."""
        if not self.saved:
            return self.base
        key = self.saved.get("api_key", "")
        return self.base.model_copy(update={
            "openai_base_url": self.saved["base_url"], "model": self.saved["model"],
            "openai_api_key": SecretStr(key), "disable_thinking": self.saved["disable_thinking"],
            "model_backend": "openai" if key else self.base.model_backend})

    def build(self) -> Any:
        s = self.effective()
        return OpenAIPersonaModel(s) if s.model_backend == "openai" else FakePersonaModel()

    def view(self) -> ModelSettingsView:
        s = self.effective()
        key = s.openai_api_key.get_secret_value()
        return ModelSettingsView(
            backend=s.model_backend, base_url=s.openai_base_url, model=s.model,
            disable_thinking=s.disable_thinking, key_set=bool(key), ready=self.ready(),
            key_hint=f"…{key[-4:]}" if len(key) >= 8 else ("已设置" if key else ""),
            source="saved" if self.saved else "env")

    def ready(self) -> bool:
        s = self.effective()
        return s.model_backend == "fake" or bool(s.openai_api_key.get_secret_value())

    async def update(self, body: ModelSettingsUpdate) -> ModelSettingsView:
        async with self._lock:
            old_key = self.effective().openai_api_key.get_secret_value()
            key = old_key if body.api_key is None else body.api_key.get_secret_value().strip()
            saved = {"base_url": body.base_url.rstrip("/"), "model": body.model.strip(),
                     "api_key": key, "disable_thinking": body.disable_thinking}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(saved, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.saved = saved
            await self.switch.swap(self.build())
            self.runner.model_name = saved["model"]
        return self.view()

    async def test(self, body: ModelSettingsUpdate) -> ModelTestResult:
        """One tiny JSON request with these settings (the saved key if none is given)."""
        key = self.effective().openai_api_key.get_secret_value() if body.api_key is None \
            else body.api_key.get_secret_value().strip()
        if not key:
            return ModelTestResult(ok=False, latency_ms=0, message="还没有填 API Key")
        client = AsyncOpenAI(api_key=key, base_url=body.base_url.rstrip("/"), max_retries=0,
                             http_client=http_client(self.base))
        started = time.perf_counter()
        try:
            await asyncio.wait_for(client.chat.completions.create(
                model=body.model, temperature=0, max_tokens=20,
                messages=[{"role": "user", "content": '只回复 JSON：{"ok": true}'}],
                response_format={"type": "json_object"},
                **({"extra_body": {"thinking": {"type": "disabled"}}}
                   if body.disable_thinking else {})), timeout=30)
        except TimeoutError:
            return self._failed(started, "30 秒没有回应：检查接口地址和网络")
        except APIStatusError as exc:
            kind = rejected(exc)
            hint = REJECTED[kind] if kind else \
                {429: "请求太频繁"}.get(exc.status_code, "服务返回错误")
            return self._failed(started, f"{hint}（HTTP {exc.status_code}）")
        except Exception as exc:  # connection errors differ between providers
            return self._failed(started, f"连不上：{type(exc).__name__}")
        finally:
            await client.close()
        return ModelTestResult(ok=True, latency_ms=round((time.perf_counter() - started) * 1000),
                               message="连接成功")

    @staticmethod
    def _failed(started: float, message: str) -> ModelTestResult:
        return ModelTestResult(ok=False, latency_ms=round((time.perf_counter() - started) * 1000),
                               message=message)
