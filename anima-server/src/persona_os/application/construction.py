"""Persona construction (paper §3.2.3): import initial beliefs B0 and read persona objects."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError
from persona_os.infra.db import (
    BeliefEntryRow,
    BeliefVersionRow,
    EpisodePersonaRow,
    MaterialRow,
    PersonaRow,
)

# Identity data must never enter the persona store (rules §7).
IDENTITY_PATTERNS = [re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), re.compile(r"(?<!\d)\d{8,}(?!\d)")]


def _unknown(layer: str, category: str) -> c.BeliefEntry:
    """Placeholder for a construct category without evidence (paper appendix C.2)."""
    return c.BeliefEntry.model_validate({
        "entry_id": f"{layer}_{category}_unknown", "layer": layer, "construct": category,
        "construct_id": f"{category}.unspecified", "value": None,
        "text": "暂无可靠证据", "source": "generated", "epistemic_status": "unknown",
        "modifiable": True,
    })


def complete_entries(entries: list[c.BeliefEntry]) -> list[c.BeliefEntry]:
    present = {(e.layer, e.category) for e in entries}
    missing = [_unknown(layer, cat) for layer, cats in c.LAYER_CONSTRUCTS.items()
               for cat in sorted(cats) if (layer, cat) not in present]
    return entries + missing


def _check_identity(item: c.PersonaImportItem) -> None:
    texts = [e.text for e in item.entries] + [m.text for m in item.material]
    for text in texts:
        if any(p.search(text) for p in IDENTITY_PATTERNS):
            raise ServiceError(422, "identity_data",
                               f"persona {item.persona_id}: entry or material contains an email "
                               "address or ID number; identity data must stay with the caller")


def _changes(before: list[c.BeliefEntry], after: list[c.BeliefEntry]) -> list[c.EntryChange]:
    old = {e.entry_id: e for e in before}
    new = {e.entry_id: e for e in after}
    out = []
    for e in after:
        was = old.get(e.entry_id)
        if was is None or was.model_dump() != e.model_dump():
            out.append(c.EntryChange(entry_id=e.entry_id, layer=e.layer,
                                     construct_id=e.construct_id,
                                     change="added" if was is None else "changed",
                                     before=was, after=e))
    out += [c.EntryChange(entry_id=e.entry_id, layer=e.layer, construct_id=e.construct_id,
                          change="removed", before=e, after=None)
            for e in before if e.entry_id not in new]
    return out


class PersonaStore:
    def __init__(self, sessions: async_sessionmaker, max_personas: int | None = None) -> None:
        self.sessions = sessions
        self.max_personas = max_personas  # per namespace; None = no limit

    async def ensure_room(self, ns: str, persona_id: str, s=None) -> None:
        """A new persona must fit under the namespace's limit (an existing one always may)."""
        if self.max_personas is None:
            return
        if s is None:
            async with self.sessions() as s:
                return await self.ensure_room(ns, persona_id, s)
        if await s.get(PersonaRow, (ns, persona_id)) is not None:
            return
        count = await s.scalar(select(func.count()).select_from(PersonaRow).where(
            PersonaRow.namespace == ns))
        if count >= self.max_personas:
            raise ServiceError(409, "persona_limit",
                               f"at most {self.max_personas} personas; delete one first")

    async def import_personas(
        self, ns: str, body: c.PersonaImportRequest
    ) -> c.PersonaImportResponse:
        for item in body.personas:
            _check_identity(item)
        async with self.sessions() as s, s.begin():
            results = [await self._write(s, ns, item.persona_id, list(item.entries),
                                         [(m.ref, m.text) for m in item.material], body.mode)
                       for item in body.personas]
        return c.PersonaImportResponse(results=results)

    async def write_initial(self, ns: str, persona_id: str, entries: list[c.BeliefEntry],
                            material: list[tuple[str, str]]) -> c.PersonaImportResult:
        """B0 from a reviewed distillation: replaces an existing B0 unless the persona has
        evolved. Material is not capped at 50 here; callers mask identity data first."""
        for text in [e.text for e in entries] + [t for _, t in material]:
            if any(p.search(text) for p in IDENTITY_PATTERNS):
                raise ServiceError(422, "identity_data", f"persona {persona_id}: identity data")
        async with self.sessions() as s, s.begin():
            return await self._write(s, ns, persona_id, entries, material, "replace_initial")

    async def _write(self, s, ns: str, persona_id: str, given: list[c.BeliefEntry],
                     material: list[tuple[str, str]], mode: str) -> c.PersonaImportResult:
        row = await s.get(PersonaRow, (ns, persona_id))
        if row is None:
            await self.ensure_room(ns, persona_id, s)
        entries = complete_entries(given)
        unknown = [e.construct_id for e in entries if e.epistemic_status == "unknown"]
        if row is not None and mode == "create_only":
            return c.PersonaImportResult(persona_id=persona_id, status="skipped",
                                         version_id=row.active_version_id,
                                         unknown_constructs=unknown)
        if row is not None:
            later = await s.scalar(select(func.count()).select_from(BeliefVersionRow).where(
                BeliefVersionRow.namespace == ns,
                BeliefVersionRow.persona_id == persona_id, BeliefVersionRow.k > 0))
            if later:
                raise ServiceError(409, "persona_evolved",
                                   f"{persona_id} already has evolved versions; "
                                   "replace_initial only applies before any evolution")
            await s.execute(delete(BeliefEntryRow).where(
                BeliefEntryRow.version_id == row.active_version_id))
            await s.execute(delete(BeliefVersionRow).where(
                BeliefVersionRow.version_id == row.active_version_id))
            await s.execute(delete(MaterialRow).where(
                MaterialRow.namespace == ns, MaterialRow.persona_id == persona_id))
            await s.flush()
        version_id = str(uuid.uuid4())
        s.add(BeliefVersionRow(version_id=version_id, namespace=ns, persona_id=persona_id, k=0,
                               base_version_id=None, origin="construction"))
        await s.flush()
        # Core executemany: one round trip per table instead of one per row.
        await s.execute(insert(BeliefEntryRow), [
            {"version_id": version_id, "entry_id": e.entry_id,
             "payload": e.model_dump(mode="json")} for e in entries])
        if material:
            await s.execute(insert(MaterialRow), [
                {"namespace": ns, "persona_id": persona_id, "ref": ref, "text": text}
                for ref, text in material])
        if row is None:
            s.add(PersonaRow(namespace=ns, persona_id=persona_id, active_version_id=version_id))
        else:
            row.active_version_id = version_id
        return c.PersonaImportResult(
            persona_id=persona_id, status="created" if row is None else "replaced",
            version_id=version_id, unknown_constructs=unknown)

    async def write_next(self, ns: str, persona_id: str, base_version_id: str,
                         entries: list[c.BeliefEntry], material: list[tuple[str, str]],
                         origin: str) -> c.BeliefVersion:
        """Version k+1 on top of `base_version_id`, which must still be the active one; never
        while the persona is in an open episode (beliefs stay fixed within one)."""
        for text in [e.text for e in entries] + [t for _, t in material]:
            if any(p.search(text) for p in IDENTITY_PATTERNS):
                raise ServiceError(422, "identity_data", f"persona {persona_id}: identity data")
        async with self.sessions() as s, s.begin():
            row = await self._persona(s, ns, persona_id)
            if row.active_version_id != base_version_id:
                raise ServiceError(409, "version_moved",
                                   f"{persona_id} changed since this was based on it")
            if await s.scalar(select(func.count()).select_from(EpisodePersonaRow).where(
                    EpisodePersonaRow.namespace == ns, EpisodePersonaRow.persona_id == persona_id,
                    EpisodePersonaRow.open)):
                raise ServiceError(409, "episode_open",
                                   f"{persona_id} is in an open episode; close it first")
            base = await s.get(BeliefVersionRow, base_version_id)
            version_id = str(uuid.uuid4())
            s.add(BeliefVersionRow(version_id=version_id, namespace=ns, persona_id=persona_id,
                                   k=base.k + 1, base_version_id=base_version_id, origin=origin))
            await s.flush()
            await s.execute(insert(BeliefEntryRow), [
                {"version_id": version_id, "entry_id": e.entry_id,
                 "payload": e.model_dump(mode="json")} for e in complete_entries(entries)])
            known = set((await s.scalars(select(MaterialRow.ref).where(
                MaterialRow.namespace == ns, MaterialRow.persona_id == persona_id))).all())
            fresh = [(ref, t) for ref, t in material if ref not in known]
            if fresh:
                await s.execute(insert(MaterialRow), [
                    {"namespace": ns, "persona_id": persona_id, "ref": ref, "text": t}
                    for ref, t in fresh])
            row.active_version_id = version_id
            return await self._version(s, await s.get(BeliefVersionRow, version_id))

    async def restore(self, ns: str, persona_id: str, version_id: str) -> c.BeliefVersion:
        """Back to an earlier version: its entries become a new version (nothing is deleted)."""
        old = await self.version(ns, persona_id, version_id)
        summary = await self.summary(ns, persona_id)
        if summary.active_version_id == version_id:
            raise ServiceError(409, "already_active", f"{version_id} is the active version")
        return await self.write_next(ns, persona_id, summary.active_version_id, old.entries, [],
                                     "correction")

    async def history(self, ns: str, persona_id: str) -> list[c.VersionHistory]:
        """Versions, newest first, each with what it changed against its base version."""
        versions = await self.versions(ns, persona_id)
        by_id = {v.version_id: v for v in versions}
        out = []
        for v in reversed(versions):
            base = by_id.get(v.base_version_id) if v.base_version_id else None
            out.append(c.VersionHistory(
                version_id=v.version_id, k=v.k, base_version_id=v.base_version_id,
                origin=v.origin, committed_at=v.committed_at, entries=len(v.entries),
                changes=_changes(base.entries if base else [], v.entries) if base else []))
        return out

    async def materials(self, ns: str, persona_id: str, refs: list[str]) -> dict[str, str]:
        """The quotes behind entries' evidence_refs (material saved with each version)."""
        async with self.sessions() as s:
            await self._persona(s, ns, persona_id)
            rows = (await s.execute(select(MaterialRow.ref, MaterialRow.text).where(
                MaterialRow.namespace == ns, MaterialRow.persona_id == persona_id,
                MaterialRow.ref.in_(refs[:500])))).all()
        return dict(rows)

    async def _persona(self, s, ns: str, persona_id: str) -> PersonaRow:
        row = await s.get(PersonaRow, (ns, persona_id))
        if row is None:
            raise ServiceError(404, "persona_not_found", persona_id)
        return row

    async def _version(self, s, row: BeliefVersionRow) -> c.BeliefVersion:
        payloads = (await s.scalars(select(BeliefEntryRow.payload).where(
            BeliefEntryRow.version_id == row.version_id).order_by(BeliefEntryRow.id))).all()
        return c.BeliefVersion(version_id=row.version_id, persona_id=row.persona_id, k=row.k,
                               base_version_id=row.base_version_id, origin=row.origin,
                               candidate_id=row.candidate_id, committed_at=row.committed_at,
                               entries=[c.BeliefEntry.model_validate(p) for p in payloads])

    async def summary(self, ns: str, persona_id: str) -> c.PersonaSummary:
        async with self.sessions() as s:
            row = await self._persona(s, ns, persona_id)
            version = await self._version(s, await s.get(BeliefVersionRow, row.active_version_id))
        return c.PersonaSummary(
            persona_id=persona_id, active_version_id=version.version_id, k=version.k,
            entry_count=len(version.entries),
            unknown_constructs=[e.construct_id for e in version.entries
                                if e.epistemic_status == "unknown"],
            open_episode_id=None, candidates_awaiting_owner=0)

    async def versions(self, ns: str, persona_id: str) -> list[c.BeliefVersion]:
        async with self.sessions() as s:
            await self._persona(s, ns, persona_id)
            rows = (await s.scalars(select(BeliefVersionRow).where(
                BeliefVersionRow.namespace == ns, BeliefVersionRow.persona_id == persona_id)
                .order_by(BeliefVersionRow.k))).all()
            return [await self._version(s, r) for r in rows]

    async def version(self, ns: str, persona_id: str, version_id: str) -> c.BeliefVersion:
        async with self.sessions() as s:
            row = await s.get(BeliefVersionRow, version_id)
            if row is None or row.namespace != ns or row.persona_id != persona_id:
                raise ServiceError(404, "version_not_found", version_id)
            return await self._version(s, row)
