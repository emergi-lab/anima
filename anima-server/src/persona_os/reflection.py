"""Self-reflection (Anima): the persona looks back at its chats since the active version, writes a
diary and its findings, and proposes evolution versions of different degree — A 微调, B 适度,
C 明显 — that the owner approves (one of them) or declines. Nothing changes without approval.

Checks done by the program, not the model: a finding must cite at least one owner line or a
deviation seen in two or more chats (its own lines may help it remember but never justify a
change alone); every change cites valid findings and stays within the evolution limits (values
move at most TERM_CAP, L3 only gains appended sentences, no new L3 entries). The cited lines are
saved with the new version as its material.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.application.evolution import L3_ADDITIONS, MIN_MONITORED_EPISODES
from persona_os.chat.service import OWNER, PREFIX, _aware
from persona_os.distill import evolve as ev
from persona_os.distill import text as tx
from persona_os.distill.ports import Revision
from persona_os.distill.service import KNOWN_IDS, LAYER_OF, _construct_id
from persona_os.domain import contract as c
from persona_os.domain.contract import StrictModel
from persona_os.domain.errors import ServiceError
from persona_os.infra.db import (
    BeliefEntryRow,
    BeliefVersionRow,
    EpisodePersonaRow,
    EpisodeRow,
    MemoryRow,
    ModelCallRow,
    MonitoringRow,
    ObservationRow,
    PersonaRow,
    SelfReflectionRow,
    now,
)
from persona_os.reflection_ports import ReflectSelfInput, ReflectVersionsInput

log = logging.getLogger(__name__)

MAX_LINES = 240  # the most recent chat lines it reads
VARIANTS = [
    {"key": "A", "title": "微调",
     "rule": "只落实最有把握的一两条发现，每处改动尽量小：强弱只动一点点，"
             "叙事身份最多追加一句很短的话。"},
    {"key": "B", "title": "适度",
     "rule": "落实所有发现，每处都在上限以内，叙事身份最多追加两句。不新增条目。"},
    {"key": "C", "title": "明显",
     "rule": "落实所有发现并用足上限，可以新增条目，叙事身份最多追加两句。"},
]
MAX_CHANGES = {"A": 2, "B": 6, "C": 8}
L3_LEFT = {"A": 1, "B": L3_ADDITIONS, "C": L3_ADDITIONS}
ADDS = {"C"}  # only the boldest version may add entries


class ReflectionChange(StrictModel):
    entry_id: str
    before: c.BeliefEntry | None
    after: c.BeliefEntry
    reason: str
    evidence: list[dict[str, Any]]  # {id, who, text}


class ReflectionVersion(StrictModel):
    key: str
    title: str
    summary: str
    changes: list[ReflectionChange]


class Reflection(StrictModel):
    reflection_id: str
    persona_id: str
    base_version_id: str
    status: Literal["running", "ready", "approved", "declined", "failed"]
    diary: str | None
    findings: list[dict[str, Any]]
    versions: list[ReflectionVersion]
    chosen: str | None
    committed_version_id: str | None
    error: str | None
    created_at: datetime
    decided_at: datetime | None


class ReflectionPreview(StrictModel):
    since_version: int
    since: datetime
    sessions: int
    owner_lines: int
    deviations: int
    enough: bool
    open: str | None  # an unfinished reflection (running or waiting for approval)


class ReflectionStart(StrictModel):
    persona_name: str


class ReflectionDecision(StrictModel):
    version: str


class Reflections:
    def __init__(self, sessions: async_sessionmaker, model, runner, store, chat) -> None:
        self.sessions = sessions
        self.model = model
        self.runner = runner  # Engine: attempts, call records, the frozen-copy try-out
        self.store = store  # PersonaStore: the new version
        self.chat = chat  # Chat: its session ends before beliefs change
        self._tasks: set[asyncio.Task] = set()

    # ---- what it looks back at ------------------------------------------------------------------
    async def _active(self, s, ns: str, persona_id: str):
        persona = await s.get(PersonaRow, (ns, persona_id))
        if persona is None:
            raise ServiceError(404, "persona_not_found", persona_id)
        version = await s.get(BeliefVersionRow, persona.active_version_id)
        entries = list((await s.scalars(select(BeliefEntryRow.payload).where(
            BeliefEntryRow.version_id == version.version_id).order_by(BeliefEntryRow.id))).all())
        return version, entries

    async def _lived(self, s, ns: str, persona_id: str, name: str, since: datetime,
                     entries: list[dict[str, Any]]):
        """Numbered chat lines since `since`, repeated deviations, and recent memories."""
        episodes = (await s.scalars(select(EpisodeRow.episode_id).join(
            EpisodePersonaRow, (EpisodePersonaRow.namespace == EpisodeRow.namespace)
            & (EpisodePersonaRow.episode_id == EpisodeRow.episode_id)).where(
            EpisodeRow.namespace == ns, EpisodePersonaRow.persona_id == persona_id,
            EpisodeRow.episode_id.startswith(PREFIX), EpisodeRow.created_at >= since)
            .order_by(EpisodeRow.created_at))).all()
        rows = (await s.scalars(select(ObservationRow).where(
            ObservationRow.namespace == ns, ObservationRow.episode_id.in_(episodes))
            .order_by(ObservationRow.id))).all() if episodes else []
        rows = rows[-MAX_LINES:]
        lines = [{"id": f"L{i + 1}", "who": "主人" if o.payload.get("actor") == OWNER else name,
                  "owner": o.payload.get("actor") == OWNER,
                  "text": " ".join(str(o.payload["content"]).split())}
                 for i, o in enumerate(rows)]
        texts = {e["entry_id"]: e.get("text") or e["construct_id"] for e in entries}
        groups: dict[tuple[str, str], list[MonitoringRow]] = defaultdict(list)
        for m in (await s.scalars(select(MonitoringRow).where(
                MonitoringRow.namespace == ns, MonitoringRow.persona_id == persona_id,
                MonitoringRow.created_at >= since))).all():
            if m.entry_id in texts:
                groups[(m.entry_id, m.direction)].append(m)
        deviations = [{"id": f"D{i + 1}", "entry_id": k[0],
                       "direction": ev.DIRECTION.get(k[1], k[1]),
                       "note": "；".join(" ".join(m.note.split()) for m in g)[:400],
                       "sessions": len({m.episode_id for m in g})}
                      for i, (k, g) in enumerate(groups.items())
                      if len({m.episode_id for m in g}) >= MIN_MONITORED_EPISODES]
        memories = list((await s.scalars(select(MemoryRow.text).where(
            MemoryRow.namespace == ns, MemoryRow.persona_id == persona_id,
            MemoryRow.kind == "episodic", MemoryRow.created_at >= since)
            .order_by(MemoryRow.id.desc()).limit(8))).all())
        return lines, deviations, memories, len(episodes)

    async def preview(self, ns: str, persona_id: str, name: str = "它") -> ReflectionPreview:
        async with self.sessions() as s:
            version, entries = await self._active(s, ns, persona_id)
            lines, deviations, _, sessions = await self._lived(
                s, ns, persona_id, name, version.committed_at, entries)
            open_id = await self._open(s, ns, persona_id)
        owner = sum(1 for ln in lines if ln["owner"])
        return ReflectionPreview(since_version=version.k, since=_aware(version.committed_at),
                                 sessions=sessions, owner_lines=owner, deviations=len(deviations),
                                 enough=bool(owner or deviations), open=open_id)

    @staticmethod
    async def _open(s, ns: str, persona_id: str) -> str | None:
        return await s.scalar(select(SelfReflectionRow.reflection_id).where(
            SelfReflectionRow.namespace == ns, SelfReflectionRow.persona_id == persona_id,
            SelfReflectionRow.status.in_(("running", "ready"))))

    # ---- reflecting -----------------------------------------------------------------------------
    async def start(self, ns: str, persona_id: str, body: ReflectionStart) -> Reflection:
        async with self.sessions() as s, s.begin():
            version, entries = await self._active(s, ns, persona_id)
            if open_id := await self._open(s, ns, persona_id):
                raise ServiceError(409, "reflection_open", open_id)
            lines, deviations, _, _ = await self._lived(s, ns, persona_id, body.persona_name,
                                                        version.committed_at, entries)
            if not any(ln["owner"] for ln in lines) and not deviations:
                raise ServiceError(422, "nothing_to_reflect",
                                   "no chats with the owner since the active version")
            reflection_id = str(uuid.uuid4())
            s.add(SelfReflectionRow(namespace=ns, reflection_id=reflection_id,
                                    persona_id=persona_id, persona_name=body.persona_name,
                                    base_version_id=version.version_id))
        task = asyncio.create_task(self._run(ns, reflection_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.get(ns, reflection_id)

    async def idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def fail_unfinished(self) -> None:
        """At startup: a reflection cut off by a restart is marked failed (start another)."""
        async with self.sessions() as s, s.begin():
            await s.execute(update(SelfReflectionRow).where(
                SelfReflectionRow.status == "running").values(
                status="failed", error="服务重启，反思中断了"))

    async def _run(self, ns: str, reflection_id: str) -> None:
        try:
            await self._reflect(ns, reflection_id)
        except Exception as exc:  # a failed job must never take the service down
            if not isinstance(exc, ServiceError):
                log.exception("reflection crashed: %s", reflection_id)
            async with self.sessions() as s, s.begin():
                row = await s.get(SelfReflectionRow, (ns, reflection_id))
                row.status, row.error = "failed", str(exc)[:300]

    async def _reflect(self, ns: str, reflection_id: str) -> None:
        async with self.sessions() as s:
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            base = await s.get(BeliefVersionRow, row.base_version_id)
            entries = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == base.version_id).order_by(BeliefEntryRow.id))).all())
            lines, deviations, memories, _ = await self._lived(
                s, ns, row.persona_id, row.persona_name, base.committed_at, entries)
        beliefs = [{"entry_id": e["entry_id"], "layer": e["layer"],
                    "construct_id": e["construct_id"], "text": e.get("text") or "",
                    "value": e.get("value"), "scope": e.get("scope") or []}
                   for e in entries if e.get("epistemic_status") == "supported"]
        calls: list[ModelCallRow] = []
        diary_in = ReflectSelfInput(name=row.persona_name, language="zh", beliefs=beliefs,
                                    lines=lines, deviations=deviations, memories=memories)
        first = await self.runner._attempts(ns, f"reflect:{reflection_id}/self", "reflect_self",
                                            calls, lambda _: self.model.reflect_self(diary_in))
        by_id = {x["id"]: x for x in lines + deviations}
        weighty = {x["id"] for x in lines if x["owner"]} | {d["id"] for d in deviations}
        known = {e["entry_id"] for e in beliefs}
        findings = []
        for f in first.value.findings:
            refs = [r for r in dict.fromkeys(f.refs) if r in by_id]
            if not weighty & set(refs):  # its own lines alone never justify a change
                continue
            findings.append({"id": f"F{len(findings) + 1}", "what": tx.mask(f.what),
                             "meaning": tx.mask(f.meaning), "layer": f.layer,
                             "entry_id": f.entry_id if f.entry_id in known else None,
                             "refs": refs})
        versions: list[dict[str, Any]] = []
        if findings:
            ask = ReflectVersionsInput(
                name=row.persona_name, language="zh", beliefs=beliefs,
                findings=[{k: f[k] for k in ("id", "what", "meaning", "layer", "entry_id")}
                          for f in findings],
                limits={layer: ev.limits(layer) for layer in ("L1", "L2", "L3")},
                variants=VARIANTS)
            second = await self.runner._attempts(
                ns, f"reflect:{reflection_id}/versions", "reflect_versions", calls,
                lambda _: self.model.reflect_versions(ask))
            versions = self._check_versions(reflection_id, second.value.versions, findings,
                                            entries, by_id)
        async with self.sessions() as s, s.begin():
            s.add_all(calls)
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            row.lines, row.diary, row.findings = lines, tx.mask(first.value.diary), findings
            row.versions, row.status = versions, "ready"

    def _check_versions(self, reflection_id: str, proposed, findings, entries, by_id):
        """Each version within the limits; changes without valid findings are dropped."""
        refs_of = {f["id"]: f["refs"] for f in findings}
        by_entry = {e["entry_id"]: e for e in entries}
        out = []
        for v in proposed:
            spec = next((x for x in VARIANTS if x["key"] == v.key), None)
            if spec is None or any(o["key"] == v.key for o in out):
                continue
            used, l3_left, changes = set(by_entry), L3_LEFT[v.key], []
            for ch in v.changes:
                refs = [r for f in ch.findings if f in refs_of for r in refs_of[f]]
                allowed = {f"rf:{reflection_id[:8]}:{r}" for r in refs}
                base = by_entry.get(ch.entry_id or "") if ch.action == "revise" else None
                if ch.action == "revise" and (base is None or any(
                        x["entry_id"] == base["entry_id"] for x in changes)):
                    continue
                category = base["construct"] if base else ch.construct_id.split(".")[0]
                if category not in LAYER_OF:
                    continue
                layer = LAYER_OF[category]
                cid = base["construct_id"] if base else _construct_id(category, ch.construct_id)
                if cid is None or (base is None and (layer == "L3" or v.key not in ADDS)):
                    continue
                entry_id = base["entry_id"] if base else \
                    _new_id(layer, cid, used)
                after = ev.apply(Revision(
                    action=ch.action, entry_id=entry_id, construct_id=cid, text=ch.text,
                    value=ch.value, scope=ch.scope, reason=ch.reason, evidence=sorted(allowed)),
                    layer, category, base, entry_id, allowed, l3_left)
                if after is None:
                    continue
                l3_left -= base is not None and layer == "L3"
                changes.append({"entry_id": entry_id, "before": base, "after": after,
                                "reason": tx.mask(ch.reason),
                                "evidence": [{"id": r, "who": by_id[r].get("who", "反复的偏差"),
                                              "text": by_id[r].get("text") or by_id[r].get("note")}
                                             for r in dict.fromkeys(refs)]})
                if len(changes) >= MAX_CHANGES[v.key]:
                    break
            if changes:
                out.append({"key": v.key, "title": spec["title"], "summary": tx.mask(v.summary),
                            "changes": changes})
        return sorted(out, key=lambda x: x["key"])

    # ---- the owner decides ----------------------------------------------------------------------
    async def get(self, ns: str, reflection_id: str) -> Reflection:
        async with self.sessions() as s:
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            if row is None:
                raise ServiceError(404, "reflection_not_found", reflection_id)
            return self._view(row)

    async def list(self, ns: str, persona_id: str, limit: int = 20) -> list[Reflection]:
        async with self.sessions() as s:
            rows = (await s.scalars(select(SelfReflectionRow).where(
                SelfReflectionRow.namespace == ns, SelfReflectionRow.persona_id == persona_id)
                .order_by(SelfReflectionRow.created_at.desc()).limit(limit))).all()
            return [self._view(r) for r in rows]

    async def approve(self, ns: str, reflection_id: str, body: ReflectionDecision) -> Reflection:
        async with self.sessions() as s:
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            if row is None:
                raise ServiceError(404, "reflection_not_found", reflection_id)
            if row.status != "ready":
                raise ServiceError(409, "reflection_decided", f"status is {row.status}")
            version = next((v for v in row.versions if v["key"] == body.version), None)
            if version is None:
                raise ServiceError(422, "invalid_request", f"no version {body.version}")
            base = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.base_version_id)
                .order_by(BeliefEntryRow.id))).all())
        await self.chat.end(ns, row.persona_id)  # beliefs only change between chats
        new = {ch["entry_id"]: c.BeliefEntry.model_validate(ch["after"])
               for ch in version["changes"]}
        entries = [new.pop(b["entry_id"], None) or c.BeliefEntry.model_validate(b)
                   for b in base] + list(new.values())
        material = [(f"rf:{reflection_id[:8]}:{e['id']}", f"{e['who']}：{e['text']}")
                    for ch in version["changes"] for e in ch["evidence"]]
        written = await self.store.write_next(ns, row.persona_id, row.base_version_id,
                                              entries, list(dict(material).items()), "evolution")
        async with self.sessions() as s, s.begin():
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            row.status, row.chosen, row.decided_at = "approved", body.version, now()
            row.committed_version_id = written.version_id
        return await self.get(ns, reflection_id)

    async def decline(self, ns: str, reflection_id: str) -> Reflection:
        async with self.sessions() as s, s.begin():
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            if row is None:
                raise ServiceError(404, "reflection_not_found", reflection_id)
            if row.status not in ("ready", "failed"):
                raise ServiceError(409, "reflection_decided", f"status is {row.status}")
            row.status, row.decided_at = "declined", now()
        return await self.get(ns, reflection_id)

    async def try_version(self, ns: str, reflection_id: str, key: str) -> dict[str, str]:
        """The base version and this version answer the same question from an empty state."""
        async with self.sessions() as s:
            row = await s.get(SelfReflectionRow, (ns, reflection_id))
            if row is None:
                raise ServiceError(404, "reflection_not_found", reflection_id)
            version = next((v for v in row.versions if v["key"] == key), None)
            if version is None:
                raise ServiceError(404, "version_not_found", key)
            base = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.base_version_id)
                .order_by(BeliefEntryRow.id))).all())
        new = {ch["entry_id"]: ch["after"] for ch in version["changes"]}
        changed = [new.pop(b["entry_id"], b) for b in base] + list(new.values())
        situation = "你在主人的电脑桌面上陪主人。主人问你：「最近跟我相处得怎么样？你有什么想法？」"
        task = "用你平时说话的方式，回答主人。"
        calls: list[ModelCallRow] = []
        try:
            before, after = await asyncio.gather(*(
                self.runner.simulate(ns, f"reflect:{reflection_id}/try/{key}/{side}", beliefs,
                                     situation, task, "zh",
                                     {"unit": "chars", "soft": [20, 80], "hard": [5, 200]}, calls)
                for side, beliefs in (("before", base), ("after", changed))))
        finally:
            async with self.sessions() as s, s.begin():
                s.add_all(calls)
        return {"situation": situation, "before": before, "after": after}

    @staticmethod
    def _view(row: SelfReflectionRow) -> Reflection:
        return Reflection(
            reflection_id=row.reflection_id, persona_id=row.persona_id,
            base_version_id=row.base_version_id, status=row.status, diary=row.diary,
            findings=row.findings or [], versions=[ReflectionVersion.model_validate(v)
                                                   for v in row.versions or []],
            chosen=row.chosen, committed_version_id=row.committed_version_id, error=row.error,
            created_at=_aware(row.created_at),
            decided_at=_aware(row.decided_at) if row.decided_at else None)


def _new_id(layer: str, construct_id: str, used: set[str]) -> str:
    base = f"{layer}_{construct_id.replace('.', '_')}"[:110]
    entry_id, n = base, 2
    while entry_id in used:
        entry_id, n = f"{base}_{n}", n + 1
    used.add(entry_id)
    return entry_id


__all__ = ["KNOWN_IDS", "Reflections"]
