"""After-episode jobs (paper §3.3.3 memory + state–behaviour monitoring), memory recall, outbox.

Closing an episode queues one `consolidate` job per persona; the owner completing their feedback
queues a `reflect` job (S4, application/evolution.py), which waits for that persona's consolidate
job. Jobs run in the background and can be re-run for a closed episode.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError
from persona_os.domain.ports import Consolidation, ConsolidationInput, PersonaModel
from persona_os.infra.db import (
    ActionRow,
    BeliefEntryRow,
    EpisodeJobRow,
    EpisodePersonaRow,
    EpisodeRow,
    FeedbackCompletionRow,
    MemoryRow,
    ModelCallRow,
    MonitoringRow,
    ObservationRow,
    OutboxRow,
    PersonaRow,
    PersonaStateRow,
    now,
)

log = logging.getLogger(__name__)

RECALL_EPISODES = 3  # most recent episodic memories from earlier episodes
CONSOLIDATE, REFLECT = "consolidate", "reflect"


def emit(s, ns: str, type_: str, *, persona_id: str | None = None,
         episode_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
    s.add(OutboxRow(namespace=ns, type=type_, persona_id=persona_id, episode_id=episode_id,
                    payload=payload or {}))


async def recall(s, ns: str, persona_id: str, episode_id: str,
                 others: set[str]) -> list[dict[str, Any]]:
    """Memories for the next action: the latest earlier episodes, plus what this persona remembers
    about the other personas present now (latest memory per person)."""
    episodic = (await s.scalars(select(MemoryRow).where(
        MemoryRow.namespace == ns, MemoryRow.persona_id == persona_id,
        MemoryRow.kind == "episodic", MemoryRow.episode_id != episode_id)
        .order_by(MemoryRow.id.desc()).limit(RECALL_EPISODES))).all()
    relational: list[MemoryRow] = []
    if others:
        rows = (await s.scalars(select(MemoryRow).where(
            MemoryRow.namespace == ns, MemoryRow.persona_id == persona_id,
            MemoryRow.kind == "relational", MemoryRow.episode_id != episode_id,
            MemoryRow.about_persona_id.in_(others)).order_by(MemoryRow.id.desc()))).all()
        seen: set[str | None] = set()
        for row in rows:
            if row.about_persona_id not in seen:
                seen.add(row.about_persona_id)
                relational.append(row)
    return [{"id": f"mem:{m.id}", "kind": m.kind, "about": m.about_name, "text": m.text,
             "when": m.created_at.date().isoformat() if m.created_at else None}
            for m in list(reversed(episodic)) + relational]


class Aftercare:
    def __init__(self, sessions: async_sessionmaker, model: PersonaModel, runner) -> None:
        self.sessions = sessions
        self.model = model
        self.runner = runner  # Engine: shares the attempt/recording policy for model calls
        self.evolution = None  # set by the app: runs `reflect` jobs
        self._tasks: set[asyncio.Task] = set()
        # One runner per episode at a time: close and feedback completion can both schedule it.
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- scheduling -----------------------------------------------------------------------------
    def schedule(self, ns: str, episode_id: str) -> None:
        task = asyncio.create_task(self.run_episode(ns, episode_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume(self) -> None:
        """At startup: jobs left running by a stopped process go back to pending and run again;
        completed feedback without a reflect job (completed before S4) gets one."""
        async with self.sessions() as s, s.begin():
            await s.execute(update(EpisodeJobRow).where(EpisodeJobRow.status == "running")
                            .values(status="pending", updated_at=now()))
            for done in (await s.scalars(select(FeedbackCompletionRow))).all():
                await queue_reflect(s, done.namespace, done.episode_id, done.persona_id)
            pending = (await s.execute(select(EpisodeJobRow.namespace, EpisodeJobRow.episode_id)
                                       .where(EpisodeJobRow.status == "pending").distinct())).all()
        for ns, episode_id in pending:
            self.schedule(ns, episode_id)

    async def idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def rerun(self, ns: str, episode_id: str) -> list[c.EpisodeJob]:
        """Queue the jobs a closed episode is missing, and failed ones again."""
        async with self.sessions() as s, s.begin():
            episode = await s.get(EpisodeRow, (ns, episode_id))
            if episode is None:
                raise ServiceError(404, "episode_not_found", episode_id)
            if episode.status != "closed":
                raise ServiceError(409, "episode_open", f"{episode_id} is not closed yet")
            await queue_jobs(s, ns, episode_id)
            await s.execute(update(EpisodeJobRow).where(
                EpisodeJobRow.namespace == ns, EpisodeJobRow.episode_id == episode_id,
                EpisodeJobRow.status == "failed").values(status="pending", updated_at=now()))
        self.schedule(ns, episode_id)
        return await self.jobs(ns, episode_id)

    async def jobs(self, ns: str, episode_id: str) -> list[c.EpisodeJob]:
        async with self.sessions() as s:
            if await s.get(EpisodeRow, (ns, episode_id)) is None:
                raise ServiceError(404, "episode_not_found", episode_id)
            rows = (await s.scalars(select(EpisodeJobRow).where(
                EpisodeJobRow.namespace == ns, EpisodeJobRow.episode_id == episode_id)
                .order_by(EpisodeJobRow.persona_id, EpisodeJobRow.job))).all()
        return [c.EpisodeJob(persona_id=r.persona_id, job=r.job, status=r.status, runs=r.runs,
                             error=r.error, updated_at=r.updated_at) for r in rows]

    # ---- running --------------------------------------------------------------------------------
    async def run_episode(self, ns: str, episode_id: str) -> None:
        """Consolidate jobs first; a reflect job runs once its persona's consolidate job is no
        longer pending or running (its monitoring is part of the evidence)."""
        async with self._locks[(ns, episode_id)]:
            await self._run_pending(ns, episode_id)

    async def _run_pending(self, ns: str, episode_id: str) -> None:
        for job in (CONSOLIDATE, REFLECT):
            async with self.sessions() as s:
                jobs = (await s.scalars(select(EpisodeJobRow).where(
                    EpisodeJobRow.namespace == ns,
                    EpisodeJobRow.episode_id == episode_id))).all()
            busy = {j.persona_id for j in jobs if j.job == CONSOLIDATE
                    and j.status in ("pending", "running")}
            ready = [j for j in jobs if j.job == job and j.status == "pending"
                     and (job == CONSOLIDATE or j.persona_id not in busy)]
            await asyncio.gather(*(self._run(ns, episode_id, j.persona_id, j.job)
                                   for j in ready))

    async def _run(self, ns: str, episode_id: str, persona_id: str, job: str) -> None:
        key = (EpisodeJobRow.namespace == ns, EpisodeJobRow.episode_id == episode_id,
               EpisodeJobRow.persona_id == persona_id, EpisodeJobRow.job == job)
        async with self.sessions() as s, s.begin():
            claimed = await s.execute(update(EpisodeJobRow).where(
                *key, EpisodeJobRow.status == "pending").values(
                status="running", runs=EpisodeJobRow.runs + 1, updated_at=now()))
        if claimed.rowcount != 1:
            return  # another runner has it
        try:
            if job == REFLECT:
                await self.evolution.reflect(ns, episode_id, persona_id)
                async with self.sessions() as s, s.begin():
                    await s.execute(update(EpisodeJobRow).where(*key).values(
                        status="done", error=None, updated_at=now()))
            else:
                await self._consolidate(ns, episode_id, persona_id)
        except Exception as exc:  # a failed job must never take the service down
            if not isinstance(exc, ServiceError):
                log.exception("after-episode job crashed: %s %s %s", episode_id, persona_id, job)
            async with self.sessions() as s, s.begin():
                await s.execute(update(EpisodeJobRow).where(*key).values(
                    status="failed", error=str(exc)[:200], updated_at=now()))
                emit(s, ns, "episode.jobs_failed", persona_id=persona_id, episode_id=episode_id,
                     payload={"job": job, "error": str(exc)[:200]})

    async def _consolidate(self, ns: str, episode_id: str, persona_id: str) -> None:
        async with self.sessions() as s:
            episode = await s.get(EpisodeRow, (ns, episode_id))
            link = await s.get(EpisodePersonaRow, (ns, episode_id, persona_id))
            observations = (await s.scalars(select(ObservationRow).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id == episode_id)
                .order_by(ObservationRow.seq))).all()
            actions = (await s.scalars(select(ActionRow).where(
                ActionRow.namespace == ns, ActionRow.episode_id == episode_id,
                ActionRow.persona_id == persona_id, ActionRow.status == "committed"))).all()
            states = {r.state_id: r.state for r in (await s.scalars(select(PersonaStateRow).where(
                PersonaStateRow.state_id.in_([a.state_id for a in actions])))).all()} \
                if actions else {}
            version_id = actions[0].belief_version_id if actions else \
                (await s.get(PersonaRow, (ns, persona_id))).active_version_id
            beliefs = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == version_id).order_by(BeliefEntryRow.id))).all())

        me = link.participant_id
        seq_of = {o.action_id: o.seq for o in observations if o.action_id}
        own_actions = sorted(
            ({"seq": seq_of[a.action_id], "text": a.output.get("text", ""),
              "state": _state_digest(states.get(a.state_id))}
             for a in actions if a.action_id in seq_of), key=lambda x: x["seq"])
        participant = next(p for p in episode.participants if p["participant_id"] == me)
        # Relations are kept only with other personas: they are the ones met again later.
        others = {p["participant_id"]: p for p in episode.participants
                  if p["participant_id"] != me and p.get("persona_id")}
        data = ConsolidationInput(
            beliefs=beliefs, situation=episode.situation, self_participant_id=me,
            participants=[{"participant_id": p["participant_id"],
                           "display_name": p["display_name"], "position": p.get("position"),
                           "remember": p["participant_id"] in others}
                          for p in episode.participants],
            position=participant.get("position"),
            observations=[o.payload for o in observations], own_actions=own_actions,
            outcome=episode.outcome)

        calls: list[ModelCallRow] = []
        call_key = f"{episode_id}/{persona_id}"[:120]
        result = await self.runner._attempts(ns, call_key, CONSOLIDATE, calls,
                                             lambda _: self.model.consolidate(data))
        valid = _validated(result.value, {f"obs:{o.seq}" for o in observations},
                           {f"obs:{a['seq']}" for a in own_actions}, set(others),
                           {b["entry_id"] for b in beliefs
                            if b.get("epistemic_status") == "supported"})

        async with self.sessions() as s, s.begin():
            for table in (MemoryRow, MonitoringRow):  # a re-run replaces this episode's rows
                for row in (await s.scalars(select(table).where(
                        table.namespace == ns, table.persona_id == persona_id,
                        table.episode_id == episode_id))).all():
                    await s.delete(row)
            s.add(MemoryRow(namespace=ns, persona_id=persona_id, episode_id=episode_id,
                            kind="episodic", text=valid.episodic.summary,
                            evidence=valid.episodic.evidence))
            for r in valid.relational:
                other = others[r.participant_id]
                s.add(MemoryRow(namespace=ns, persona_id=persona_id, episode_id=episode_id,
                                kind="relational", about_persona_id=other.get("persona_id"),
                                about_name=other["display_name"], text=r.summary,
                                evidence=r.evidence))
            for m in valid.monitoring:
                s.add(MonitoringRow(namespace=ns, persona_id=persona_id, episode_id=episode_id,
                                    version_id=version_id, entry_id=m.entry_id,
                                    direction=m.direction, note=m.note, evidence=m.evidence))
            s.add_all(calls)
            await s.execute(update(EpisodeJobRow).where(
                EpisodeJobRow.namespace == ns, EpisodeJobRow.episode_id == episode_id,
                EpisodeJobRow.persona_id == persona_id, EpisodeJobRow.job == CONSOLIDATE)
                .values(status="done", error=None, updated_at=now()))

    # ---- reading --------------------------------------------------------------------------------
    async def memories(self, ns: str, persona_id: str, limit: int) -> list[c.Memory]:
        async with self.sessions() as s:
            if await s.get(PersonaRow, (ns, persona_id)) is None:
                raise ServiceError(404, "persona_not_found", persona_id)
            rows = (await s.scalars(select(MemoryRow).where(
                MemoryRow.namespace == ns, MemoryRow.persona_id == persona_id)
                .order_by(MemoryRow.id.desc()).limit(limit))).all()
        return [c.Memory(memory_id=f"mem:{m.id}", episode_id=m.episode_id, kind=m.kind,
                         about_persona_id=m.about_persona_id, about_name=m.about_name,
                         text=m.text, evidence=m.evidence, created_at=m.created_at)
                for m in rows]

    async def events(self, ns: str, after: int, limit: int) -> c.EventPage:
        async with self.sessions() as s:
            rows = (await s.scalars(select(OutboxRow).where(
                OutboxRow.namespace == ns, OutboxRow.seq > after)
                .order_by(OutboxRow.seq).limit(limit))).all()
        items = [c.OutboxEvent(seq=r.seq, type=r.type, persona_id=r.persona_id,
                               episode_id=r.episode_id, payload=r.payload, at=r.at)
                 for r in rows]
        return c.EventPage(items=items, next_after=items[-1].seq if items else after)


async def queue_reflect(s, ns: str, episode_id: str, persona_id: str) -> bool:
    """Queue reflection for one persona; False if it was already queued or done."""
    if await s.get(EpisodeJobRow, (ns, episode_id, persona_id, REFLECT)) is not None:
        return False
    s.add(EpisodeJobRow(namespace=ns, episode_id=episode_id, persona_id=persona_id, job=REFLECT))
    return True


async def queue_jobs(s, ns: str, episode_id: str) -> None:
    """One consolidate job per persona of the episode; existing jobs are left as they are."""
    links = (await s.scalars(select(EpisodePersonaRow).where(
        EpisodePersonaRow.namespace == ns, EpisodePersonaRow.episode_id == episode_id))).all()
    for link in links:
        if await s.get(EpisodeJobRow, (ns, episode_id, link.persona_id, CONSOLIDATE)) is None:
            s.add(EpisodeJobRow(namespace=ns, episode_id=episode_id,
                                persona_id=link.persona_id, job=CONSOLIDATE))


def _state_digest(state: dict[str, Any] | None) -> dict[str, Any]:
    """What monitoring needs from a state: the chosen strategy and how traits showed."""
    if not state:
        return {}
    return {"strategy": (state.get("L2") or {}).get("strategy"),
            "expressions": (state.get("L1") or {}).get("expressions", [])}


def _validated(value: Consolidation, evidence: set[str], own: set[str], others: set[str],
               entries: set[str]) -> Consolidation:
    """Keep only references that exist: evidence from this episode, monitoring only on supported
    entries and only from the persona's own actions, relations only with other participants."""
    def refs(items: list[str], allowed: set[str]) -> list[str]:
        return [x for x in items if x in allowed]

    episodic = value.episodic.model_copy(update={"evidence": refs(value.episodic.evidence,
                                                                  evidence)})
    relational = [r.model_copy(update={"evidence": refs(r.evidence, evidence)})
                  for r in value.relational if r.participant_id in others]
    monitoring = []
    for m in value.monitoring:
        own_refs = refs(m.evidence, own)
        if m.entry_id in entries and own_refs:  # a deviation needs the persona's own words
            monitoring.append(m.model_copy(update={"evidence": own_refs}))
    return Consolidation(episodic=episodic, relational=relational, monitoring=monitoring)
