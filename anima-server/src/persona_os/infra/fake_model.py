"""Deterministic model for tests and local contract demos; makes no network calls."""

from __future__ import annotations

from persona_os.distill.ports import (
    ExtractedEvidence,
    ExtractInput,
    ExtractOutput,
    InducedEntry,
    InduceInput,
    InduceOutput,
    ReviewInput,
    ReviewNote,
    ReviewOutput,
    ReviseInput,
    ReviseOutput,
    Revision,
)
from persona_os.domain.contract import ActionOutput, PersonaState
from persona_os.domain.ports import (
    Consolidation,
    ConsolidationInput,
    DeferredGroup,
    EpisodicMemory,
    GenerationDraft,
    GenerationInput,
    JudgeInput,
    JudgeOutput,
    ModelResult,
    MonitoringItem,
    Proposal,
    ProposedEdit,
    ReflectionInput,
    ReflectionOutput,
    RelationalMemory,
    RespondInput,
    Response,
    ReviewCheck,
    StateInput,
    TestSituation,
    Verdict,
)
from persona_os.reflection_ports import (
    Finding,
    ProposedVersion,
    ReflectSelfInput,
    ReflectSelfOutput,
    ReflectVersionsInput,
    ReflectVersionsOutput,
    VersionChange,
)


class FakePersonaModel:
    name = "fake-persona-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def infer_state(self, data: StateInput) -> ModelResult[PersonaState]:
        self.calls.append("infer_state")
        entry = data.beliefs[0]["entry_id"] if data.beliefs else None
        evidence = [f"obs:{o['seq']}" for o in data.observations[-2:]]
        position = (data.position or {}).get("identity")
        state = PersonaState(
            L3={"identity_position": position or "participant", "responsibilities":
                (data.position or {}).get("responsibilities", []),
                "refs": {"entries": [entry] if entry else [], "evidence": evidence}},
            L2={"strategy": {"chosen": "respond to the latest point", "why": "fake",
                             "refs": {"entries": [], "evidence": evidence}}},
            L1={"expressions": []},
            cross_check="fake: consistent",
        )
        return ModelResult(state, self.name, latency_ms=1)

    async def generate_and_review(self, data: GenerationInput) -> ModelResult[GenerationDraft]:
        self.calls.append("generate_and_review")
        unit = data.output_contract["length"]["unit"]
        lo = data.output_contract["length"]["soft"][0]
        text = ("我" * lo) if unit == "chars" else " ".join(["word"] * lo)
        draft = GenerationDraft(
            draft=text,
            review=[ReviewCheck(check="consistent with state", passed=True)],
            final=ActionOutput(text=text, extra=_fill(data.output_contract.get("extra_schema"))),
            action_basis={"L3": data.state.L3.get("identity_position", ""), "entries": []},
        )
        return ModelResult(draft, self.name, latency_ms=1)

    async def respond(self, data: RespondInput, on_final=None) -> ModelResult[Response]:
        self.calls.append("respond")
        fields = data.model_dump(exclude={"output_contract", "repair_feedback"})
        state = (await FakePersonaModel.infer_state(self, StateInput(**fields))).value
        self.calls.pop()
        draft = (await FakePersonaModel.generate_and_review(self, GenerationInput(
            state=state, beliefs=data.beliefs, situation=data.situation,
            self_participant_id=data.self_participant_id, participants=data.participants,
            observations=data.observations, task=data.task,
            output_contract=data.output_contract))).value
        self.calls.pop()
        if on_final is not None:
            await on_final(draft.final)
        return ModelResult(Response(state=state, review=draft.review, final=draft.final,
                                    action_basis=draft.action_basis), self.name, latency_ms=1)

    async def consolidate(self, data: ConsolidationInput) -> ModelResult[Consolidation]:
        self.calls.append("consolidate")
        own = [f"obs:{a['seq']}" for a in data.own_actions]
        others = [p["participant_id"] for p in data.participants
                  if p["participant_id"] != data.self_participant_id and p.get("remember")]
        supported = [b["entry_id"] for b in data.beliefs
                     if b.get("epistemic_status") == "supported"]
        result = Consolidation(
            episodic=EpisodicMemory(summary=f"took part in {data.situation['title']}",
                                    evidence=own[:2]),
            relational=[RelationalMemory(participant_id=p, summary=f"talked with {p}",
                                         evidence=own[:1]) for p in others],
            monitoring=[MonitoringItem(entry_id=supported[0], direction="above",
                                       note="spoke more than usual", evidence=own)]
            if own and supported else [],
        )
        return ModelResult(result, self.name, latency_ms=1)

    async def reflect(self, data: ReflectionInput) -> ModelResult[ReflectionOutput]:
        """Proposes lowering the first numeric L1 entry for every L1 group; defers the rest."""
        self.calls.append("reflect")
        traits = [b for b in data.beliefs if b["layer"] == "L1" and (b.get("value") or {})
                  .get("baseline") is not None]
        proposals, deferred = [], []
        for g in data.groups:
            if g.target == "L1" and traits:
                entry = traits[0]
                tests = [TestSituation(type=t, situation=f"{t} situation", task="respond")
                         for t in ("target", "target", "similar", "similar", "out_of_scope",
                                   "out_of_scope")]
                proposals.append(Proposal(
                    group_key=g.key, layer="L1", summary="被当面否定时，你会更收着一些",
                    edits=[ProposedEdit(entry_id=entry["entry_id"], field="value.baseline",
                                        to=round(entry["value"]["baseline"] - 0.3, 2))],
                    scope=["被当面否定时"], tests=tests))
            else:
                deferred.append(DeferredGroup(group_key=g.key, why="fake: not enough"))
        return ModelResult(ReflectionOutput(proposals=proposals, deferred=deferred), self.name,
                           latency_ms=1)

    async def judge_tests(self, data: JudgeInput) -> ModelResult[JudgeOutput]:
        self.calls.append("judge_tests")
        verdicts = [Verdict(index=c["index"], verdict="realized" if c["type"] == "target"
                            else "unchanged") for c in data.cases]
        return ModelResult(JudgeOutput(verdicts=verdicts), self.name, latency_ms=1)

    async def extract_evidence(self, data: ExtractInput) -> ModelResult[ExtractOutput]:
        """Every line naming the target becomes `value` evidence; one invented quote is added so
        tests can see it dropped by the verbatim check."""
        names = [data.target["name"], *data.target.get("aliases", [])]
        lines = [ln.strip() for ln in data.segment.splitlines()
                 if ln.strip() and any(n in ln for n in names)]
        if data.focus:  # a focused look: the same lines, as trait evidence of that domain
            self.calls.append("extract_focus")
            return ModelResult(ExtractOutput(evidence=[
                ExtractedEvidence(quote=ln[:200], note="fake: focused", construct="trait",
                                  facet=data.focus["facets"][0]) for ln in lines[:2]]),
                self.name, latency_ms=1)
        self.calls.append("extract_evidence")
        evidence = [ExtractedEvidence(quote=ln[:200], note="fake: says something",
                                      construct="value") for ln in lines[:5]]
        evidence.append(ExtractedEvidence(quote="这句话素材里没有", note="fake", construct="trait"))
        return ModelResult(ExtractOutput(evidence=evidence), self.name, latency_ms=1)

    async def induce_beliefs(self, data: InduceInput) -> ModelResult[InduceOutput]:
        """One entry citing every evidence item, plus one citing nothing (dropped)."""
        self.calls.append("induce_beliefs")
        ids = [e["id"] for e in data.evidence]
        entries = [
            InducedEntry(construct_id=data.known_ids[0], text="我很看重身边的人。",
                         value={"baseline": 0.8, "noise": 3}, confidence=0.7,
                         evidence=ids[:20] + ["ev:999999"]),
            InducedEntry(construct_id=f"{data.category}.unfounded", text="没有证据的结论。",
                         confidence=0.4, evidence=[]),
        ]
        return ModelResult(InduceOutput(entries=entries), self.name, latency_ms=1)

    async def review_beliefs(self, data: ReviewInput) -> ModelResult[ReviewOutput]:
        self.calls.append("review_beliefs")
        notes = [ReviewNote(entry_id=data.entries[0]["entry_id"], issue="fake: 可能过度概括",
                            suggestion="加上适用情境")] if data.entries else []
        return ModelResult(ReviewOutput(notes=notes), self.name, latency_ms=1)

    async def review_changes(self, data: ReviewInput) -> ModelResult[ReviewOutput]:
        self.calls.append("review_changes")
        return await FakePersonaModel.review_beliefs(self, data) if data.entries else \
            ModelResult(ReviewOutput(), self.name, latency_ms=1)

    async def extract_changes(self, data: ExtractInput) -> ModelResult[ExtractOutput]:
        """Owner lines become `value` evidence and deviation notes `trait` evidence; the first
        line of anyone else is quoted too, so tests can see the persona's own words dropped."""
        self.calls.append("extract_changes")
        evidence = []
        for ln in data.segment.splitlines():
            if ln.startswith("主人："):
                evidence.append(ExtractedEvidence(quote=ln[3:], note="fake: owner says",
                                                  construct="value"))
            elif ln.startswith("关于"):
                evidence.append(ExtractedEvidence(quote=ln, note="fake: deviation",
                                                  construct="trait"))
        others = [ln.split("：", 1)[1] for ln in data.segment.splitlines()
                  if "：" in ln and not ln.startswith(("主人：", "关于"))]
        if others:
            evidence.append(ExtractedEvidence(quote=others[0], note="fake: self",
                                              construct="value"))
        return ModelResult(ExtractOutput(evidence=evidence[:30]), self.name, latency_ms=1)

    async def revise_beliefs(self, data: ReviseInput) -> ModelResult[ReviseOutput]:
        """Revise the first entry (value pushed past the cap, to be clamped), add one entry, and
        revise an entry that does not exist (dropped)."""
        self.calls.append("revise_beliefs")
        ids = [e["id"] for e in data.evidence]
        out = []
        if data.entries:
            first = data.entries[0]
            base = (first.get("value") or {}).get("baseline")
            out.append(Revision(
                action="revise", entry_id=first["entry_id"],
                text="和主人相处时，我会收着点。" if data.limits["text"] == "append"
                else first["text"] + "不过和主人说话时我会收着点。",
                value={"baseline": base - 0.5} if isinstance(base, float) else None,
                reason="主人说他太凶", evidence=ids))
        if data.limits["allow_add"]:
            out.append(Revision(action="add", construct_id=data.known_ids[-1],
                                text="我在主人面前更放得开。", value={"baseline": 0.6},
                                reason="主人喜欢他放松", evidence=ids[:1]))
        out.append(Revision(action="revise", entry_id="nobody", text="x", reason="x",
                            evidence=ids[:1]))
        return ModelResult(ReviseOutput(revisions=out), self.name, latency_ms=1)

    async def reflect_self(self, data: ReflectSelfInput) -> ModelResult[ReflectSelfOutput]:
        """A finding per owner line (on the first L2 entry), and one resting only on the
        persona's own line (dropped by the service)."""
        self.calls.append("reflect_self")
        l2 = next((b["entry_id"] for b in data.beliefs if b["layer"] == "L2"), None)
        findings = [Finding(what=ln["text"], meaning="fake: 主人在意这个", layer="L2",
                            entry_id=l2, refs=[ln["id"]])
                    for ln in data.lines if ln["owner"]][:2]
        own = [ln["id"] for ln in data.lines if not ln["owner"]]
        if own:
            findings.append(Finding(what="我自己说的", meaning="fake", layer="L1", refs=own[:1]))
        return ModelResult(ReflectSelfOutput(diary="fake: 我回想了最近的事。", findings=findings),
                           self.name, latency_ms=1)

    async def reflect_versions(self, data: ReflectVersionsInput
                               ) -> ModelResult[ReflectVersionsOutput]:
        """Each variant revises the first finding's entry (value pushed past the cap); the last
        also adds an entry."""
        self.calls.append("reflect_versions")
        if not data.findings:
            return ModelResult(ReflectVersionsOutput(), self.name, latency_ms=1)
        f = data.findings[0]
        base = next((b for b in data.beliefs if b["entry_id"] == f.get("entry_id")), None)
        versions = []
        for v in data.variants:
            changes = []
            if base:
                old = (base.get("value") or {}).get("baseline")
                changes.append(VersionChange(
                    action="revise", entry_id=base["entry_id"],
                    text=base["text"] + f"（{v['key']}）",
                    value={"baseline": old - 0.5} if isinstance(old, float) else None,
                    reason="你说过的话让我想变一变", findings=[f["id"]]))
            if v is data.variants[-1]:
                changes.append(VersionChange(action="add", construct_id="value.stimulation",
                                             text="我愿意试试新鲜事。", reason="新的我",
                                             findings=[f["id"]]))
            versions.append(ProposedVersion(key=v["key"], summary=f"fake: 版本{v['key']}",
                                            changes=changes))
        return ModelResult(ReflectVersionsOutput(versions=versions), self.name, latency_ms=1)

def _fill(schema: dict | None) -> dict:
    """Every required field: an enum's first value, an empty string for a string field."""
    if not schema:
        return {}
    props = schema.get("properties", {})
    out = {}
    for k in schema.get("required", []):
        spec = props.get(k, {})
        if "enum" in spec:
            out[k] = spec["enum"][0]
        elif spec.get("type") == "string":
            out[k] = ""
    return out
