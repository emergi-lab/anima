"""The owner's personas (Anima): list them for switching, and delete one with everything it
lived through. The engine keeps no display names, so a persona is named after the character of
the distillation that imported it. At most `PersonaStore.max_personas` per owner.

Deleting removes the persona's versions, material, actions, states, memories, monitoring,
feedback, evolution candidates and evolutions, and every episode it was the only persona in
(its chats). Its initial distillations stay, so the material can be imported again — without the
chats and memories. Explicit all-data deletion also removes those initial distillations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.chat.service import PREFIX, _aware
from persona_os.domain.contract import StrictModel
from persona_os.domain.errors import ServiceError
from persona_os.infra.db import (
    ActionRow,
    BeliefEntryRow,
    BeliefVersionRow,
    CandidateRow,
    DistillationRow,
    DistillEntryRow,
    DistillEvidenceRow,
    DistillSegmentRow,
    DistillSourceRow,
    EpisodeJobRow,
    EpisodePersonaRow,
    EpisodeRow,
    FeedbackCompletionRow,
    FeedbackRow,
    MaterialRow,
    MemoryRow,
    ModelCallRow,
    MonitoringRow,
    ObservationRow,
    OutboxRow,
    PersonaRow,
    PersonaStateRow,
    ReflectionRow,
    SelfReflectionRow,
)


class PersonaCard(StrictModel):
    persona_id: str
    name: str
    k: int
    entries: int
    created_at: datetime
    chats: int
    last_chat_at: datetime | None


class Roster(StrictModel):
    items: list[PersonaCard]  # most recently chatted first
    max: int | None


class Personas:
    def __init__(self, sessions: async_sessionmaker, store, chat, aftercare) -> None:
        self.sessions = sessions
        self.store = store  # PersonaStore: the limit
        self.chat = chat  # Chat: its per-persona lock (no reply half stored while deleting)
        self.aftercare = aftercare  # memory jobs must not write after the delete

    async def list(self, ns: str) -> Roster:
        async with self.sessions() as s:
            personas = (await s.scalars(select(PersonaRow).where(
                PersonaRow.namespace == ns))).all()
            versions = {v.version_id: v for v in (await s.scalars(select(BeliefVersionRow).where(
                BeliefVersionRow.version_id.in_([p.active_version_id for p in personas])))).all()}
            counts = dict((await s.execute(select(
                BeliefEntryRow.version_id, func.count()).where(
                BeliefEntryRow.version_id.in_(list(versions)),
                func.json_extract(BeliefEntryRow.payload, "$.epistemic_status") != "unknown")
                .group_by(BeliefEntryRow.version_id))).all())
            names = {}
            for pid, target in (await s.execute(select(
                    DistillationRow.persona_id, DistillationRow.target).where(
                    DistillationRow.namespace == ns, DistillationRow.kind == "initial",
                    DistillationRow.stage == "committed")
                    .order_by(DistillationRow.updated_at))).all():
                if target and target.get("name"):
                    names[pid] = target["name"]  # the latest import wins
            chats = {pid: (n, last) for pid, n, last in (await s.execute(select(
                EpisodePersonaRow.persona_id, func.count(),
                func.max(func.coalesce(EpisodeRow.closed_at, EpisodeRow.created_at))).join(
                EpisodeRow, (EpisodeRow.namespace == EpisodePersonaRow.namespace)
                & (EpisodeRow.episode_id == EpisodePersonaRow.episode_id)).where(
                EpisodePersonaRow.namespace == ns,
                EpisodePersonaRow.episode_id.startswith(PREFIX))
                .group_by(EpisodePersonaRow.persona_id))).all()}
        items = []
        for p in personas:
            n, last = chats.get(p.persona_id, (0, None))
            items.append(PersonaCard(
                persona_id=p.persona_id, name=names.get(p.persona_id, p.persona_id),
                k=versions[p.active_version_id].k, entries=counts.get(p.active_version_id, 0),
                created_at=_aware(p.created_at), chats=n,
                last_chat_at=_aware(last) if last else None))
        items.sort(key=lambda c: (c.last_chat_at or c.created_at), reverse=True)
        return Roster(items=items, max=self.store.max_personas)

    async def delete(self, ns: str, persona_id: str, *,
                     include_distillations: bool = False) -> None:
        async with self.chat.lock(ns, persona_id):  # a reply still being stored finishes first
            await self.aftercare.idle()  # and so do memory jobs that would write afterwards
            async with self.sessions() as s, s.begin():
                linked = await s.scalar(select(DistillationRow.distillation_id).where(
                    DistillationRow.namespace == ns, DistillationRow.persona_id == persona_id))
                if (await s.get(PersonaRow, (ns, persona_id)) is None
                        and not (include_distillations and linked)):
                    raise ServiceError(404, "persona_not_found", persona_id)
                if await s.scalar(select(DistillationRow.distillation_id).where(
                        DistillationRow.namespace == ns, DistillationRow.persona_id == persona_id,
                        DistillationRow.stage == "running")):
                    raise ServiceError(409, "persona_busy",
                                       f"{persona_id} has a distillation or evolution running")
                await self._purge(s, ns, persona_id, include_distillations=include_distillations)

    @staticmethod
    async def _purge(s, ns: str, persona_id: str, *, include_distillations: bool = False) -> None:
        mine = (EpisodePersonaRow.namespace == ns) & (EpisodePersonaRow.persona_id == persona_id)
        joined = set((await s.scalars(select(EpisodePersonaRow.episode_id).where(mine))).all())
        shared = set((await s.scalars(select(EpisodePersonaRow.episode_id).where(
            EpisodePersonaRow.namespace == ns, EpisodePersonaRow.episode_id.in_(joined),
            EpisodePersonaRow.persona_id != persona_id))).all())
        alone = list(joined - shared)  # its chats; episodes shared with others stay
        actions = list((await s.scalars(select(ActionRow.action_id).where(
            ActionRow.namespace == ns, or_(ActionRow.persona_id == persona_id,
                                           ActionRow.episode_id.in_(alone))))).all())
        distill_query = select(DistillationRow.distillation_id).where(
            DistillationRow.namespace == ns, DistillationRow.persona_id == persona_id)
        if not include_distillations:
            distill_query = distill_query.where(DistillationRow.kind == "evolution")
        distillations = list((await s.scalars(distill_query)).all())
        versions = list((await s.scalars(select(BeliefVersionRow.version_id).where(
            BeliefVersionRow.namespace == ns, BeliefVersionRow.persona_id == persona_id))).all())

        reflections = list((await s.scalars(select(SelfReflectionRow.reflection_id).where(
            SelfReflectionRow.namespace == ns, SelfReflectionRow.persona_id == persona_id))).all())
        await s.execute(delete(ModelCallRow).where(ModelCallRow.namespace == ns, or_(
            ModelCallRow.action_id.in_(actions),
            *[ModelCallRow.action_id.startswith(f"distill:{d}/") for d in distillations],
            *[ModelCallRow.action_id.startswith(f"reflect:{r}/") for r in reflections])))
        for row in (ObservationRow, EpisodeJobRow):
            await s.execute(delete(row).where(row.namespace == ns, row.episode_id.in_(alone)))
        for row in (ActionRow, PersonaStateRow, EpisodeJobRow, MonitoringRow, OutboxRow,
                    FeedbackRow, FeedbackCompletionRow, ReflectionRow, CandidateRow,
                    MaterialRow, SelfReflectionRow):
            await s.execute(delete(row).where(row.namespace == ns,
                                              row.persona_id == persona_id))
        await s.execute(delete(MemoryRow).where(MemoryRow.namespace == ns, or_(
            MemoryRow.persona_id == persona_id, MemoryRow.about_persona_id == persona_id)))
        await s.execute(delete(EpisodePersonaRow).where(mine))
        await s.execute(delete(EpisodePersonaRow).where(
            EpisodePersonaRow.namespace == ns, EpisodePersonaRow.episode_id.in_(alone)))
        await s.execute(delete(EpisodeRow).where(EpisodeRow.namespace == ns,
                                                 EpisodeRow.episode_id.in_(alone)))
        if distillations:
            await s.execute(delete(DistillEntryRow).where(
                DistillEntryRow.distillation_id.in_(distillations)))
            await s.execute(delete(DistillEvidenceRow).where(
                DistillEvidenceRow.distillation_id.in_(distillations)))
            await s.execute(delete(DistillSegmentRow).where(
                DistillSegmentRow.distillation_id.in_(distillations)))
            await s.execute(delete(DistillSourceRow).where(
                DistillSourceRow.distillation_id.in_(distillations)))
            await s.execute(delete(DistillationRow).where(
                DistillationRow.namespace == ns,
                DistillationRow.distillation_id.in_(distillations)))
        await s.execute(delete(PersonaRow).where(PersonaRow.namespace == ns,
                                                 PersonaRow.persona_id == persona_id))
        await s.execute(delete(BeliefEntryRow).where(BeliefEntryRow.version_id.in_(versions)))
        await s.execute(delete(BeliefVersionRow).where(BeliefVersionRow.version_id.in_(versions)))
