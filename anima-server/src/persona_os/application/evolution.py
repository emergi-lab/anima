"""Controlled evolution (paper §3.4, algorithm 2; contract §5).

After the owner completes an episode's feedback: gather and count evidence (program) → one
reflection call proposes minimal revisions or defers → constraint review (entries, fields,
per-term caps) → frozen-copy tests (same start, before vs after, 6 situations) judged by one call
→ candidates that pass wait for the owner. Approval commits a new version before the persona's
next episode. Test outputs, execution advice and the owner's free-text notes are never evidence.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError
from persona_os.domain.ports import (
    EvidenceGroup,
    JudgeInput,
    PersonaModel,
    Proposal,
    ReflectionInput,
)
from persona_os.infra.db import (
    ActionRow,
    BeliefEntryRow,
    BeliefVersionRow,
    CandidateRow,
    EpisodePersonaRow,
    EpisodeRow,
    FeedbackCompletionRow,
    FeedbackRow,
    ModelCallRow,
    MonitoringRow,
    OutboxRow,
    PersonaRow,
    ReflectionRow,
    now,
)

# Evolution rules (contract §5), one set for the namespace.
MIN_FEEDBACK = 3            # same reason, same persona, not used before
MIN_MONITORED_EPISODES = 2  # the same deviation in at least this many episodes
TERM_CAP = {"L1": 0.10, "L2": 0.20}
L3_ADDITIONS = 2
MIN_STEP = 0.01
# Feedback targets that may change beliefs; wording / systemic / fact_check / none never do.
PERSONALITY_TARGETS = {"L1", "L2.strategy", "L2.need", "L2.value", "L2.goal", "L3"}
TEST_LENGTH = {"unit": "words", "soft": [20, 50], "hard": [5, 100]}


def _emit(s, ns: str, type_: str, persona_id: str, episode_id: str | None,
          payload: dict[str, Any]) -> None:
    s.add(OutboxRow(namespace=ns, type=type_, persona_id=persona_id, episode_id=episode_id,
                    payload=payload))


def _value(entry: dict[str, Any], field: str) -> float | None:
    v = (entry.get("value") or {}).get(field.split(".", 1)[1])
    return float(v) if isinstance(v, int | float) else None


def apply_edits(payloads: list[dict[str, Any]], edits: list[dict[str, Any]],
                *, relative: bool = False) -> list[dict[str, Any]]:
    """New entry payloads with `edits` applied. `relative` re-bases numeric edits on the given
    values (committing on top of a version newer than the candidate's base)."""
    out = copy.deepcopy(payloads)
    by_id = {e["entry_id"]: e for e in out}
    for edit in edits:
        entry = by_id[edit["entry_id"]]
        if edit["field"] == "text.append":
            entry["text"] = (entry.get("text") or "").rstrip() + "\n" + str(edit["to"])
            continue
        key = edit["field"].split(".", 1)[1]
        target = float(edit["to"])
        if relative:
            target = float(entry["value"][key]) + target - float(edit["from"])
        entry["value"][key] = round(min(1.0, max(0.0, target)), 3)
    return out


class Evolution:
    def __init__(self, sessions: async_sessionmaker, model: PersonaModel, runner) -> None:
        self.sessions = sessions
        self.model = model
        self.runner = runner  # Engine: attempt policy, call records, frozen-copy simulation

    # ---- evidence (program side) ----------------------------------------------------------------
    async def _evidence(self, s, ns: str, persona_id: str,
                        entries: dict[str, dict[str, Any]]) -> tuple[list[EvidenceGroup], dict]:
        used: set[str] = set()
        for row in (await s.scalars(select(CandidateRow).where(
                CandidateRow.namespace == ns, CandidateRow.persona_id == persona_id))).all():
            used.update(item["ref"] for item in row.evidence_for)
        completed = set((await s.scalars(select(FeedbackCompletionRow.episode_id).where(
            FeedbackCompletionRow.namespace == ns,
            FeedbackCompletionRow.persona_id == persona_id))).all())
        feedback = [f for f in (await s.scalars(select(FeedbackRow).where(
            FeedbackRow.namespace == ns, FeedbackRow.persona_id == persona_id))).all()
            if f.episode_id in completed]
        texts = {a.action_id: a.generation.get("published_text") or a.output.get("text", "")
                 for a in (await s.scalars(select(ActionRow).where(ActionRow.action_id.in_(
                     [f.action_id for f in feedback])))).all()} if feedback else {}

        counts: dict[str, int] = defaultdict(int)
        by_reason: dict[str, list[FeedbackRow]] = defaultdict(list)
        for f in feedback:
            counts[f.reason_code or f.judgment] += 1
            if f.target in PERSONALITY_TARGETS and f"feedback:{f.feedback_id}" not in used:
                by_reason[f.reason_code].append(f)
        groups = [EvidenceGroup(
            key=f"feedback:{code}", kind="feedback", target=rows[0].target or "",
            label=rows[0].reason_code or "",
            items=[{"ref": f"feedback:{r.feedback_id}", "episode_id": r.episode_id,
                    "judgment": r.judgment, "quote": texts.get(r.action_id, "")} for r in rows])
            for code, rows in by_reason.items() if len(rows) >= MIN_FEEDBACK]

        by_entry: dict[tuple[str, str], list[MonitoringRow]] = defaultdict(list)
        for m in (await s.scalars(select(MonitoringRow).where(
                MonitoringRow.namespace == ns, MonitoringRow.persona_id == persona_id))).all():
            if f"monitoring:{m.id}" not in used and m.entry_id in entries:
                by_entry[(m.entry_id, m.direction)].append(m)
        for (entry_id, direction), rows in by_entry.items():
            if len({r.episode_id for r in rows}) >= MIN_MONITORED_EPISODES:
                groups.append(EvidenceGroup(
                    key=f"monitoring:{entry_id}:{direction}", kind="monitoring", target=entry_id,
                    label=f"acted {direction} this belief",
                    items=[{"ref": f"monitoring:{r.id}", "episode_id": r.episode_id,
                            "quote": r.note} for r in rows]))
        return groups, {"feedback_counts": dict(counts), "evidence_groups": len(groups)}

    async def _remaining(self, s, ns: str, persona_id: str,
                         entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
        moved: dict[str, float] = defaultdict(float)
        added = 0
        for row in (await s.scalars(select(CandidateRow).where(
                CandidateRow.namespace == ns, CandidateRow.persona_id == persona_id,
                CandidateRow.status == "committed"))).all():
            for e in row.edits:
                if e["field"] == "text.append":
                    added += 1
                else:
                    moved[e["entry_id"]] += abs(float(e["to"]) - float(e["from"]))
        left = {}
        for entry_id, entry in entries.items():
            cap = TERM_CAP.get(entry["layer"])
            if cap is not None:
                left[entry_id] = round(max(0.0, cap - moved[entry_id]), 3)
        return {"entries": left, "l3_additions_left": max(0, L3_ADDITIONS - added)}

    # ---- reflection ---------------------------------------------------------------------------
    async def reflect(self, ns: str, episode_id: str, persona_id: str) -> None:
        async with self.sessions() as s:
            persona = await s.get(PersonaRow, (ns, persona_id))
            version_id = persona.active_version_id
            payloads = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == version_id).order_by(BeliefEntryRow.id))).all())
            entries = {p["entry_id"]: p for p in payloads
                       if p.get("epistemic_status") == "supported" and p.get("modifiable", True)}
            groups, detail = await self._evidence(s, ns, persona_id, entries)
            remaining = await self._remaining(s, ns, persona_id, entries)
            episode = await s.get(EpisodeRow, (ns, episode_id))

        candidates: list[CandidateRow] = []
        calls: list[ModelCallRow] = []
        deferred: list[dict[str, str]] = []
        if groups:
            data = ReflectionInput(beliefs=list(entries.values()), groups=groups,
                                   remaining=remaining, situation=episode.situation)
            result = await self.runner._attempts(
                ns, f"reflect/{episode_id}/{persona_id}"[:120], "reflect", calls,
                lambda _: self.model.reflect(data))
            deferred = [d.model_dump() for d in result.value.deferred]
            by_key = {g.key: g for g in groups}
            seen: set[str] = set()
            for proposal in result.value.proposals:
                group = by_key.get(proposal.group_key)
                if group is None or proposal.group_key in seen:
                    continue
                seen.add(proposal.group_key)
                edits, why = self._review(proposal, entries, remaining)
                if not edits:
                    deferred.append({"group_key": proposal.group_key, "why": why})
                    continue
                candidates.append(await self._tested(
                    ns, episode_id, persona_id, version_id, payloads, proposal, edits, group,
                    episode.situation.get("language", "en"), calls))

        async with self.sessions() as s, s.begin():
            s.add_all(calls)
            s.add_all(candidates)
            waiting = [x for x in candidates if x.status == "awaiting_owner"]
            outcome = "candidates" if waiting else "defer"
            detail |= {"deferred": deferred,
                       "candidates": {x.candidate_id: x.status for x in candidates}}
            existing = await s.get(ReflectionRow, (ns, episode_id, persona_id))
            if existing is None:
                s.add(ReflectionRow(namespace=ns, episode_id=episode_id, persona_id=persona_id,
                                    result=outcome, detail=detail))
            else:
                existing.result, existing.detail, existing.created_at = outcome, detail, now()
            for x in waiting:
                _emit(s, ns, "candidate.awaiting_owner", persona_id, episode_id,
                      {"candidate_id": x.candidate_id, "layer": x.layer})
            _emit(s, ns, "reflection.completed", persona_id, episode_id,
                  {"result": outcome, "candidates": len(waiting)})

    @staticmethod
    def _review(proposal: Proposal, entries: dict[str, dict[str, Any]],
                remaining: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        """Constraint review: real, modifiable entries; allowed fields; per-term caps."""
        edits: list[dict[str, Any]] = []
        additions = remaining["l3_additions_left"]
        for e in proposal.edits:
            entry = entries.get(e.entry_id)
            if entry is None or entry["layer"] != proposal.layer:
                continue
            if e.field == "text.append":
                if entry["layer"] == "L3" and e.text and additions > 0:
                    additions -= 1
                    edits.append({"entry_id": e.entry_id, "field": e.field, "from": None,
                                  "to": e.text.strip()})
                continue
            before = _value(entry, e.field)
            if before is None or e.to is None or entry["layer"] == "L3":
                continue
            if entry["layer"] == "L1" and e.field != "value.baseline":
                continue
            cap = remaining["entries"].get(e.entry_id, 0.0)
            step = max(-cap, min(cap, float(e.to) - before))
            if abs(step) < MIN_STEP:
                continue
            edits.append({"entry_id": e.entry_id, "field": e.field, "from": before,
                          "to": round(before + step, 3)})
        return edits, "" if edits else "no edit within the allowed entries and term limits"

    async def _tested(self, ns, episode_id, persona_id, version_id, payloads, proposal, edits,
                      group, language, calls) -> CandidateRow:
        """Frozen-copy test: before and after versions, same start, same six situations."""
        candidate_id = str(uuid.uuid4())
        after = apply_edits(payloads, edits)

        async def speak(beliefs, i, side):
            t = proposal.tests[i]
            return await self.runner.simulate(ns, f"test/{candidate_id}/{i}/{side}", beliefs,
                                              t.situation, t.task, language, TEST_LENGTH, calls)

        outputs = await asyncio.gather(*(speak(b, i, side) for i in range(len(proposal.tests))
                                         for side, b in (("before", payloads), ("after", after))))
        cases = [{"index": i, "type": t.type, "situation": t.situation,
                  "before": outputs[2 * i], "after": outputs[2 * i + 1]}
                 for i, t in enumerate(proposal.tests)]
        judged = await self.runner._attempts(
            ns, f"judge/{candidate_id}", "judge_tests", calls,
            lambda _: self.model.judge_tests(JudgeInput(summary=proposal.summary,
                                                        scope=proposal.scope, cases=cases)))
        verdicts = {v.index: v.verdict for v in judged.value.verdicts}
        for case in cases:
            case["verdict"] = verdicts.get(case["index"], "not_realized" if case["type"] ==
                                           "target" else "changed")
        # Fixed before testing: a target situation shows the change, no out-of-scope one does.
        realized = any(x["verdict"] == "realized" for x in cases if x["type"] == "target")
        leaked = any(x["verdict"] != "unchanged" for x in cases if x["type"] == "out_of_scope")
        status = "awaiting_owner" if realized and not leaked else \
            ("pending_realization" if not realized else "no_commit")
        episodes = {item["episode_id"] for item in group.items}
        return CandidateRow(
            candidate_id=candidate_id, namespace=ns, persona_id=persona_id,
            episode_id=episode_id, base_version_id=version_id, layer=proposal.layer,
            summary=proposal.summary, edits=edits, scope=proposal.scope,
            evidence_for=[{"kind": group.kind, "ref": item["ref"], "quote": item.get("quote")}
                          for item in group.items],
            evidence_against=[], strength="strong" if len(episodes) > 1 else "limited",
            test=[{k: x[k] for k in ("type", "situation", "before", "after", "verdict")}
                  for x in cases],
            status=status)

    # ---- owner review and commit --------------------------------------------------------------
    async def candidates(self, ns: str, persona_id: str,
                         status: str | None) -> list[c.Candidate]:
        async with self.sessions() as s:
            if await s.get(PersonaRow, (ns, persona_id)) is None:
                raise ServiceError(404, "persona_not_found", persona_id)
            query = select(CandidateRow).where(CandidateRow.namespace == ns,
                                               CandidateRow.persona_id == persona_id)
            if status:
                query = query.where(CandidateRow.status == status)
            rows = (await s.scalars(query.order_by(CandidateRow.created_at.desc()))).all()
        return [_candidate(r) for r in rows]

    async def decide(self, ns: str, candidate_id: str,
                     body: c.CandidateDecision) -> c.Candidate:
        async with self.sessions() as s, s.begin():
            row = await s.get(CandidateRow, candidate_id)
            if row is None or row.namespace != ns:
                raise ServiceError(404, "candidate_not_found", candidate_id)
            target = {"approve": "approved", "reject": "rejected", "defer": "deferred"}[
                body.decision]
            if row.status not in ("awaiting_owner", "deferred"):
                if row.status == target or (row.status == "committed" and target == "approved"):
                    return _candidate(row)  # the same decision again
                raise ServiceError(409, "candidate_decided", f"{candidate_id} is {row.status}")
            row.status, row.decision_note, row.decided_at = target, body.note, now()
            if target == "approved":
                busy = await s.scalar(select(EpisodePersonaRow.episode_id).where(
                    EpisodePersonaRow.namespace == ns,
                    EpisodePersonaRow.persona_id == row.persona_id,
                    EpisodePersonaRow.open.is_(True)))
                if busy is None:  # otherwise committed before the persona's next episode
                    await commit_approved(s, ns, row.persona_id)
        return _candidate(row)


async def commit_approved(s, ns: str, persona_id: str) -> None:
    """Commit approved candidates as new versions, oldest first (called inside a transaction)."""
    rows = (await s.scalars(select(CandidateRow).where(
        CandidateRow.namespace == ns, CandidateRow.persona_id == persona_id,
        CandidateRow.status == "approved").order_by(CandidateRow.decided_at))).all()
    for row in rows:
        persona = await s.get(PersonaRow, (ns, persona_id))
        active = await s.get(BeliefVersionRow, persona.active_version_id)
        payloads = list((await s.scalars(select(BeliefEntryRow.payload).where(
            BeliefEntryRow.version_id == active.version_id).order_by(BeliefEntryRow.id))).all())
        if any(e["entry_id"] not in {p["entry_id"] for p in payloads} for e in row.edits):
            row.status = "no_commit"  # an entry vanished since the candidate was made
            continue
        entries = apply_edits(payloads, row.edits,
                              relative=active.version_id != row.base_version_id)
        for entry in entries:
            if entry["entry_id"] in {e["entry_id"] for e in row.edits}:
                entry["evidence_refs"] = (entry.get("evidence_refs") or [])[-199:] + [
                    f"candidate:{row.candidate_id}"]
        version_id = str(uuid.uuid4())
        s.add(BeliefVersionRow(version_id=version_id, namespace=ns, persona_id=persona_id,
                               k=active.k + 1, base_version_id=active.version_id,
                               origin="evolution", candidate_id=row.candidate_id))
        await s.flush()
        s.add_all([BeliefEntryRow(version_id=version_id, entry_id=e["entry_id"],
                                  payload=c.BeliefEntry.model_validate(e).model_dump(mode="json"))
                   for e in entries])
        persona.active_version_id = version_id
        row.status, row.committed_version_id = "committed", version_id
        _emit(s, ns, "version.committed", persona_id, None,
              {"version_id": version_id, "k": active.k + 1, "candidate_id": row.candidate_id})


def _candidate(row: CandidateRow) -> c.Candidate:
    return c.Candidate(
        candidate_id=row.candidate_id, persona_id=row.persona_id,
        base_version_id=row.base_version_id, layer=row.layer, summary=row.summary,
        edits=[c.Edit.model_validate({"entry_id": e["entry_id"], "field": e["field"],
                                      "from": e["from"], "to": e["to"]}) for e in row.edits],
        scope=row.scope,
        evidence_for=[c.EvidenceItem.model_validate(x) for x in row.evidence_for],
        evidence_against=[c.EvidenceItem.model_validate(x) for x in row.evidence_against],
        strength=row.strength, test=[c.TestCase.model_validate(x) for x in row.test],
        status=row.status, episode_id=row.episode_id, created_at=row.created_at,
        decided_at=row.decided_at)
