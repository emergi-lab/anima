"""Persona execution engine — situational adaptation (paper §3.3.2, algorithm 1).

Each action: fix B_k → assemble evidence → retrieve memories → call 1 (state) → call 2
(generate + analytic review) → programmatic checks → store a *prepared* action. With
`calls=1` the state and the reviewed reply come from one `respond` call instead. Only commit
turns it into behaviour and makes its state the next Z_{t-1}.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.application.aftercare import queue_jobs, recall
from persona_os.application.evolution import commit_approved
from persona_os.domain import contract as c
from persona_os.domain.errors import REJECTED, ServiceError, model_not_configured, model_rejected
from persona_os.domain.ports import (
    GenerationDraft,
    GenerationInput,
    ModelResult,
    PersonaModel,
    RespondInput,
    Response,
    StateInput,
)
from persona_os.infra.db import (
    ActionRow,
    BeliefEntryRow,
    EpisodePersonaRow,
    EpisodeRow,
    ModelCallRow,
    ObservationRow,
    PersonaRow,
    PersonaStateRow,
    now,
)
from persona_os.infra.openai_model import ModelCallError

MAX_ATTEMPTS = 2  # per call, including the redo after failed programmatic checks (rules §4)
CJK = re.compile(r"[\u3400-\u9fff]")


def _hash(model: c.StrictModel) -> str:
    return hashlib.sha256(json.dumps(model.model_dump(mode="json"), sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _length(text: str, unit: str) -> int:
    return len(re.findall(r"\S+", text)) if unit == "words" else len(re.sub(r"\s", "", text))


def _walk_refs(node: Any, key: str, found: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, list):
                found.update(x for x in v if isinstance(x, str))
            else:
                _walk_refs(v, key, found)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, key, found)


def _filter_refs(node: Any, entries: set[str], evidence: set[str], removed: list[str]) -> Any:
    """Drop references to entries/evidence that do not exist (unsupported activations)."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("entries", "evidence") and isinstance(v, list):
                allowed = entries if k == "entries" else evidence
                keep = [x for x in v if x in allowed]
                removed.extend(x for x in v if x not in allowed)
                out[k] = keep
            else:
                out[k] = _filter_refs(v, entries, evidence, removed)
        return out
    if isinstance(node, list):
        return [_filter_refs(v, entries, evidence, removed) for v in node]
    return node


class Engine:
    def __init__(self, sessions: async_sessionmaker, model: PersonaModel, model_name: str) -> None:
        self.sessions = sessions
        self.model = model
        self.model_name = model_name

    # ---- episodes and observations -------------------------------------------------------------
    async def create_episode(self, ns: str, body: c.EpisodeCreate) -> c.Episode:
        async with self.sessions() as s, s.begin():
            row = await s.get(EpisodeRow, (ns, body.episode_id))
            if row is not None:
                same = (row.situation == body.situation.model_dump(mode="json") and
                        row.participants == [p.model_dump(mode="json") for p in body.participants])
                if not same:
                    raise ServiceError(409, "episode_conflict",
                                       f"{body.episode_id} exists with different content")
                return self._episode(row)
            for p in body.participants:
                if not p.persona_id:
                    continue
                if await s.get(PersonaRow, (ns, p.persona_id)) is None:
                    raise ServiceError(404, "persona_not_found", p.persona_id)
                busy = await s.scalar(select(EpisodePersonaRow.episode_id).where(
                    EpisodePersonaRow.namespace == ns, EpisodePersonaRow.persona_id == p.persona_id,
                    EpisodePersonaRow.open.is_(True)))
                if busy:
                    raise ServiceError(409, "persona_busy",
                                       f"{p.persona_id} is still in open episode {busy}")
            for p in body.participants:  # approved revisions take effect before the next episode
                if p.persona_id:
                    await commit_approved(s, ns, p.persona_id)
            row = EpisodeRow(namespace=ns, episode_id=body.episode_id,
                             situation=body.situation.model_dump(mode="json"),
                             participants=[p.model_dump(mode="json") for p in body.participants])
            s.add(row)
            s.add_all([EpisodePersonaRow(namespace=ns, episode_id=body.episode_id,
                                         persona_id=p.persona_id, participant_id=p.participant_id)
                       for p in body.participants if p.persona_id])
        return self._episode(row)

    def _episode(self, row: EpisodeRow) -> c.Episode:
        return c.Episode(episode_id=row.episode_id, situation=row.situation,
                         participants=row.participants, status=row.status,
                         created_at=row.created_at or now(), closed_at=row.closed_at)

    async def _open_episode(self, s, ns: str, episode_id: str) -> EpisodeRow:
        row = await s.get(EpisodeRow, (ns, episode_id))
        if row is None:
            raise ServiceError(404, "episode_not_found", episode_id)
        if row.status != "open":
            raise ServiceError(409, "episode_closed", episode_id)
        return row

    async def add_observations(self, ns: str, episode_id: str,
                               batch: c.ObservationBatch) -> dict[str, int]:
        accepted = 0
        async with self.sessions() as s, s.begin():
            await self._open_episode(s, ns, episode_id)
            existing = {r.seq: r.payload for r in (await s.scalars(select(ObservationRow).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id == episode_id))).all()}
            last = max(existing, default=-1)
            for item in batch.items:
                payload = item.model_dump(mode="json")
                if item.seq in existing:
                    if existing[item.seq] != payload:
                        raise ServiceError(409, "observation_conflict", f"seq {item.seq}")
                    continue
                if item.seq <= last:
                    raise ServiceError(409, "observation_out_of_order",
                                       f"seq {item.seq} is not after {last}")
                s.add(ObservationRow(namespace=ns, episode_id=episode_id, seq=item.seq,
                                     payload=payload))
                last, accepted = item.seq, accepted + 1
        return {"accepted": accepted, "last_seq": last}

    async def close_episode(self, ns: str, episode_id: str, body: c.EpisodeClose) -> c.Episode:
        async with self.sessions() as s, s.begin():
            row = await s.get(EpisodeRow, (ns, episode_id))
            if row is None:
                raise ServiceError(404, "episode_not_found", episode_id)
            if row.status == "open":
                row.status, row.closed_at = "closed", now()
                row.outcome = body.outcome
                row.reflect_no_later_than = body.reflect_no_later_than
                for link in (await s.scalars(select(EpisodePersonaRow).where(
                        EpisodePersonaRow.namespace == ns,
                        EpisodePersonaRow.episode_id == episode_id))).all():
                    link.open = False
                for action in (await s.scalars(select(ActionRow).where(
                        ActionRow.namespace == ns, ActionRow.episode_id == episode_id,
                        ActionRow.status == "prepared"))).all():
                    action.status, action.settled_at = "discarded", now()
                await queue_jobs(s, ns, episode_id)  # the caller schedules them after commit
        return self._episode(row)

    # ---- actions -------------------------------------------------------------------------------
    async def prepare(self, ns: str, episode_id: str, body: c.ActionPrepare,
                      early: Callable[[c.ActionOutput], Awaitable[None]] | None = None
                      ) -> c.PreparedAction:
        """`early` (one-call actions only) receives the spoken line as soon as the model has
        written it and it passed the checks, before the state and basis are complete; from then
        on that line is what gets stored, even if the rest of the output fails."""
        request_hash = _hash(body)
        async with self.sessions() as s:
            existing = await s.get(ActionRow, (ns, body.action_id))
            if existing is not None:
                if existing.request_hash != request_hash or existing.episode_id != episode_id:
                    raise ServiceError(409, "action_conflict",
                                       f"{body.action_id} was prepared with a different request")
                return self._prepared(existing)
            episode = await self._open_episode(s, ns, episode_id)
            participant = next((p for p in episode.participants
                                if p["participant_id"] == body.participant_id), None)
            if participant is None or not participant.get("persona_id"):
                raise ServiceError(422, "not_a_persona",
                                   f"{body.participant_id} is not a persona participant")
            persona_id = participant["persona_id"]
            persona = await s.get(PersonaRow, (ns, persona_id))
            beliefs = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == persona.active_version_id)
                .order_by(BeliefEntryRow.id))).all())
            observations = [r.payload for r in (await s.scalars(select(ObservationRow).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id == episode_id,
                ObservationRow.seq <= body.upto_seq).order_by(ObservationRow.seq))).all()]
            previous = await s.scalar(select(PersonaStateRow).where(
                PersonaStateRow.namespace == ns, PersonaStateRow.persona_id == persona_id,
                PersonaStateRow.final.is_(True)).order_by(PersonaStateRow.finalized_at.desc(),
                                                         PersonaStateRow.created_at.desc()))
            memories = await recall(s, ns, persona_id, episode_id, {
                p["persona_id"] for p in episode.participants
                if p.get("persona_id") and p["persona_id"] != persona_id})

        version_id = persona.active_version_id
        participants = [{"participant_id": p["participant_id"], "display_name": p["display_name"],
                         "position": p.get("position")} for p in episode.participants]
        memory_ids = [m["id"] for m in memories]
        evidence_ids = {f"obs:{o['seq']}" for o in observations} | set(memory_ids)
        entry_ids = {b["entry_id"] for b in beliefs}
        calls: list[ModelCallRow] = []

        state_input = StateInput(
            beliefs=beliefs, previous_state=c.PersonaState(**previous.state) if previous else None,
            situation=episode.situation, self_participant_id=body.participant_id,
            participants=participants, position=participant.get("position"),
            observations=observations, memories=memories, task=body.task.model_dump(mode="json"))
        others = {p["participant_id"] for p in participants} - {body.participant_id}
        removed: list[str] = []
        language = episode.situation.get("language", "")

        if body.calls == 1:
            # one call: state and reviewed reply together, redone once if checks fail
            delivered: list[c.ActionOutput] = []

            async def on_final(final: c.ActionOutput) -> None:
                if delivered or self._check_final(final, body.output_contract, others, language):
                    return  # already shown, or the full check will fail and redo it
                delivered.append(final)
                await early(final)

            async def respond(feedback: str | None):
                try:
                    result = await self.model.respond(RespondInput(
                        **state_input.model_dump(),
                        output_contract=body.output_contract.model_dump(mode="json"),
                        repair_feedback=feedback), **({"on_final": on_final} if early else {}))
                except ModelCallError as exc:
                    if not delivered:
                        raise
                    # the line is already on screen: keep it, without a state or basis
                    return ModelResult(Response(
                        state=c.PersonaState(L3={}, L2={}, L1={}, unknown=["状态未生成"]),
                        final=delivered[0], action_basis={},
                        state_issue=f"台词已显示，其余输出未完成：{exc.kind}"),
                        self.model_name, latency_ms=exc.latency_ms,
                        prompt_tokens=exc.prompt_tokens,
                        completion_tokens=exc.completion_tokens,
                        prompt_version=exc.prompt_version, fingerprint=exc.fingerprint)
                if delivered:
                    result.value.final = delivered[0]
                    return result
                self._raise_problems(result, self._check(result.value, body.output_contract,
                                                         others, language))
                return result

            draft = (await self._attempts(ns, body.action_id, "respond", calls, respond)).value
            state = c.PersonaState.model_validate(_filter_refs(
                draft.state.model_dump(mode="json"), entry_ids, evidence_ids, removed))
        else:
            # call 1: three-layer state inference
            state_result = await self._attempts(ns, body.action_id, "infer_state", calls,
                                                lambda _: self.model.infer_state(state_input))
            state = c.PersonaState.model_validate(_filter_refs(
                state_result.value.model_dump(mode="json"), entry_ids, evidence_ids, removed))

            # call 2: generation + analytic review, redone once if programmatic checks fail
            used: set[str] = set()
            _walk_refs(state.model_dump(), "entries", used)
            subset = [b for b in beliefs if b["entry_id"] in used or b["layer"] == "L3"
                      or (b["layer"] == "L1" and b["construct_id"].count(".") == 1)]

            def gen_input(feedback: str | None) -> GenerationInput:
                return GenerationInput(
                    state=state, beliefs=subset, situation=episode.situation,
                    self_participant_id=body.participant_id, participants=participants,
                    observations=observations, task=body.task.model_dump(mode="json"),
                    output_contract=body.output_contract.model_dump(mode="json"),
                    repair_feedback=feedback)

            async def generate(feedback: str | None):
                result = await self.model.generate_and_review(gen_input(feedback))
                self._raise_problems(result, self._check(result.value, body.output_contract,
                                                         others, language))
                return result

            gen_result = await self._attempts(ns, body.action_id, "generate_and_review", calls,
                                              generate)
            draft = gen_result.value

        basis = dict(draft.action_basis)
        basis["entries"] = [e for e in basis.get("entries", []) if e in entry_ids]

        state_id = str(uuid.uuid4())
        usage = c.Usage(model_calls=body.calls, attempts=len(calls),
                        prompt_tokens=sum(x.prompt_tokens for x in calls),
                        completion_tokens=sum(x.completion_tokens for x in calls),
                        latency_ms=sum(x.latency_ms for x in calls))
        action = ActionRow(
            namespace=ns, action_id=body.action_id, episode_id=episode_id, persona_id=persona_id,
            participant_id=body.participant_id, status="prepared", request_hash=request_hash,
            output=draft.final.model_dump(mode="json"),
            generation={"draft": getattr(draft, "draft", draft.final.text),
                        "review": [r.model_dump(mode="json") for r in draft.review],
                        "unresolved": draft.unresolved, "state_issue": draft.state_issue,
                        "action_basis": basis},
            belief_version_id=version_id, state_id=state_id, usage=usage.model_dump())
        try:
            async with self.sessions() as s, s.begin():
                s.add(PersonaStateRow(
                    state_id=state_id, namespace=ns, persona_id=persona_id, episode_id=episode_id,
                    action_id=body.action_id, version_id=version_id,
                    state=state.model_dump(mode="json"),
                    evidence={"observations": sorted(evidence_ids - set(memory_ids)),
                              "memories": memory_ids,
                              "removed_refs": removed, "upto_seq": body.upto_seq}))
                s.add(action)
                s.add_all(calls)
        except IntegrityError as exc:  # a concurrent prepare of the same action won
            raise ServiceError(409, "action_conflict", f"{body.action_id} prepared concurrently",
                               retryable=True) from exc
        return self._prepared(action)

    async def simulate(self, ns: str, key: str, beliefs: list[dict[str, Any]], situation: str,
                       task: str, language: str, length: dict[str, Any],
                       calls: list[ModelCallRow]) -> str:
        """One action of a frozen copy (evolution tests): the same two calls as `prepare`, from
        an empty state and no memories, nothing stored but the call records."""
        participants = [{"participant_id": "self", "display_name": "Me", "position": None},
                        {"participant_id": "other", "display_name": "Alex", "position": None}]
        observations = [{"seq": 0, "kind": "event", "actor": None, "content": situation,
                         "addressed_to": [], "at": None}]
        setting = {"title": "test", "description": situation, "language": language}
        contract = c.OutputContract.model_validate({"length": length})
        state_input = StateInput(beliefs=beliefs, previous_state=None, situation=setting,
                                 self_participant_id="self", participants=participants,
                                 position=None, observations=observations, memories=[],
                                 task={"instruction": task})
        result = await self._attempts(ns, key, "infer_state", calls,
                                      lambda _: self.model.infer_state(state_input))
        state = c.PersonaState.model_validate(_filter_refs(
            result.value.model_dump(mode="json"), {b["entry_id"] for b in beliefs}, {"obs:0"},
            []))
        used: set[str] = set()
        _walk_refs(state.model_dump(), "entries", used)
        subset = [b for b in beliefs if b["entry_id"] in used or b["layer"] == "L3"
                  or (b["layer"] == "L1" and b["construct_id"].count(".") == 1)]

        async def generate(feedback: str | None):
            out = await self.model.generate_and_review(GenerationInput(
                state=state, beliefs=subset, situation=setting, self_participant_id="self",
                participants=participants, observations=observations,
                task={"instruction": task}, output_contract=contract.model_dump(mode="json"),
                repair_feedback=feedback))
            problems = self._check(out.value, contract, {"other"}, language)
            if problems:
                raise ModelCallError("check_failed", "; ".join(problems), True,
                                     prompt_version=out.prompt_version,
                                     fingerprint=out.fingerprint, latency_ms=out.latency_ms,
                                     prompt_tokens=out.prompt_tokens,
                                     completion_tokens=out.completion_tokens)
            return out

        draft = await self._attempts(ns, key, "generate_and_review", calls, generate)
        return draft.value.final.text

    async def _attempts(self, ns, action_id, call, calls, run):
        feedback, last = None, None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await run(feedback)
            except ModelCallError as exc:
                last = exc
                calls.append(ModelCallRow(namespace=ns, action_id=action_id, call=call,
                                          attempt=attempt, ok=False, error=str(exc)[:200],
                                          model=self.model_name, prompt_version=exc.prompt_version,
                                          fingerprint=exc.fingerprint, latency_ms=exc.latency_ms,
                                          prompt_tokens=exc.prompt_tokens,
                                          completion_tokens=exc.completion_tokens))
                if not exc.retryable:
                    break
                if exc.kind in ("check_failed", "invalid_output"):
                    feedback = f"上一次输出未通过检查：{exc}。请修正后重新输出完整 JSON。"
                continue
            calls.append(ModelCallRow(namespace=ns, action_id=action_id, call=call,
                                      attempt=attempt, ok=True, model=self.model_name,
                                      prompt_version=result.prompt_version,
                                      fingerprint=result.fingerprint,
                                      prompt_tokens=result.prompt_tokens,
                                      completion_tokens=result.completion_tokens,
                                      latency_ms=result.latency_ms))
            return result
        await self._record_failure(calls)
        assert last is not None
        if last.kind == "not_configured":
            raise model_not_configured() from last
        if last.kind in REJECTED:
            raise model_rejected(last.kind, str(last).partition(": ")[2]) from last
        if last.kind in ("check_failed", "invalid_output"):
            raise ServiceError(502, "generation_failed", f"{call}: {last}") from last
        raise ServiceError(503, "model_unavailable", f"{call}: {last}",
                           retryable=last.retryable) from last

    @staticmethod
    def _raise_problems(result, problems: list[str]) -> None:
        """Failed programmatic checks count as a failed attempt, redone with the problems."""
        if problems:
            raise ModelCallError("check_failed", "; ".join(problems), True,
                                 prompt_version=result.prompt_version,
                                 fingerprint=result.fingerprint, latency_ms=result.latency_ms,
                                 prompt_tokens=result.prompt_tokens,
                                 completion_tokens=result.completion_tokens)

    async def _record_failure(self, calls: list[ModelCallRow]) -> None:
        async with self.sessions() as s, s.begin():
            s.add_all(calls)

    @staticmethod
    def _check(draft: GenerationDraft | Response, contract: c.OutputContract, others: set[str],
               language: str) -> list[str]:
        return Engine._check_final(draft.final, contract, others, language)

    @staticmethod
    def _check_final(final: c.ActionOutput, contract: c.OutputContract, others: set[str],
                     language: str) -> list[str]:
        problems = []
        # Only the spoken line must be in the situation's language. action_basis is the owner's
        # own explanation of the action and may be in whatever language they think in (a
        # zh-survey student's basis came back in Chinese and failed two prepares in a row).
        if not language.startswith(("zh", "ja")) and CJK.search(final.text):
            problems.append(f"final.text must be written in {language}")
        text = final.text.strip()
        n = _length(text, contract.length.unit)
        lo, hi = contract.length.hard
        if not text:
            problems.append("final.text is empty")
        elif not lo <= n <= hi:
            problems.append(f"final.text has {n} {contract.length.unit}, allowed {lo}-{hi}")
        bad = [x for x in final.addressed_to if x not in others]
        if bad:
            problems.append(f"addressed_to has unknown participants {bad}")
        if contract.extra_schema:
            try:
                jsonschema.validate(final.extra, contract.extra_schema)
            except jsonschema.ValidationError as exc:
                problems.append(f"final.extra: {exc.message[:150]}")
        return problems

    def _prepared(self, row: ActionRow) -> c.PreparedAction:
        return c.PreparedAction(action_id=row.action_id, status=row.status,
                                output=c.ActionOutput.model_validate(row.output),
                                belief_version_id=row.belief_version_id, state_id=row.state_id,
                                usage=c.Usage.model_validate(row.usage))

    async def _action(self, s, ns: str, episode_id: str, action_id: str) -> ActionRow:
        row = await s.get(ActionRow, (ns, action_id))
        if row is None or row.episode_id != episode_id:
            raise ServiceError(404, "action_not_found", action_id)
        return row

    async def commit(self, ns: str, episode_id: str, action_id: str,
                     body: c.ActionCommit) -> c.PreparedAction:
        async with self.sessions() as s, s.begin():
            row = await self._action(s, ns, episode_id, action_id)
            if row.status == "committed":
                seq = await s.scalar(select(ObservationRow.seq).where(
                    ObservationRow.namespace == ns, ObservationRow.action_id == action_id))
                if seq != body.seq:
                    raise ServiceError(409, "action_committed", f"committed at seq {seq}")
                return self._prepared(row)
            if row.status == "discarded":
                raise ServiceError(409, "action_discarded", action_id)
            await self._open_episode(s, ns, episode_id)
            last = await s.scalar(select(func.max(ObservationRow.seq)).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id == episode_id))
            if last is not None and body.seq <= last:
                raise ServiceError(409, "observation_out_of_order",
                                   f"seq {body.seq} is not after {last}")
            text = body.published_text or row.output["text"]
            s.add(ObservationRow(namespace=ns, episode_id=episode_id, seq=body.seq,
                                 action_id=action_id, payload=c.Observation(
                                     seq=body.seq, kind="utterance", actor=row.participant_id,
                                     content=text, addressed_to=row.output.get("addressed_to", []),
                                     at=body.at).model_dump(mode="json")))
            state = await s.get(PersonaStateRow, row.state_id)
            state.final, state.finalized_at = True, now()
            row.status, row.settled_at = "committed", now()
            if body.published_text and body.published_text != row.output["text"]:
                row.generation = {**row.generation, "published_text": body.published_text}
        return self._prepared(row)

    async def discard(self, ns: str, episode_id: str, action_id: str) -> c.PreparedAction:
        async with self.sessions() as s, s.begin():
            row = await self._action(s, ns, episode_id, action_id)
            if row.status == "committed":
                raise ServiceError(409, "action_committed", action_id)
            if row.status == "prepared":
                row.status, row.settled_at = "discarded", now()
        return self._prepared(row)

    async def state_of(self, ns: str, action_id: str) -> c.ActionState:
        """The state Z_t the action was taken from (404 until the action is stored)."""
        async with self.sessions() as s:
            row = await s.get(ActionRow, (ns, action_id))
            if row is None or row.status == "discarded":
                raise ServiceError(404, "action_not_found", action_id)
            state = await s.get(PersonaStateRow, row.state_id)
        return c.ActionState(action_id=action_id, persona_id=row.persona_id,
                             state=c.PersonaState.model_validate(state.state),
                             state_issue=row.generation.get("state_issue"))

    async def explain(self, ns: str, action_id: str) -> c.Explanation:
        async with self.sessions() as s:
            row = await s.get(ActionRow, (ns, action_id))
            if row is None or row.status == "discarded":
                raise ServiceError(404, "action_not_found", action_id)
            basis = row.generation.get("action_basis", {})
            ids = basis.get("entries", [])
            payloads = (await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.belief_version_id,
                BeliefEntryRow.entry_id.in_(ids)))).all() if ids else []
        layers = {k: str(basis.get(k, "")) for k in ("L3", "L2", "L1") if basis.get(k)}
        return c.Explanation(
            action_id=action_id, persona_id=row.persona_id,
            belief_version_id=row.belief_version_id, summary=" ".join(layers.values()),
            basis=layers, entries=[{"entry_id": p["entry_id"], "text": p.get("text", "")}
                                   for p in payloads])
