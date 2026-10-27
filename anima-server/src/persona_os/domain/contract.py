"""Wire models for the public API (docs/接口契约.md). Pure persona vocabulary only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


# ---- persona beliefs (paper appendix C) -------------------------------------------------------

Layer = Literal["L1", "L2", "L3"]
Construct = Literal["overall_identity", "imago", "life_course", "need", "value", "goal", "strategy",
                    "trait"]
# Fixed taxonomy (paper table C.1): which construct categories belong to which layer.
LAYER_CONSTRUCTS: dict[str, set[str]] = {
    "L3": {"overall_identity", "imago", "life_course"},
    "L2": {"need", "value", "goal", "strategy"},
    "L1": {"trait"},
}
Source = Literal["measurement", "inference", "configured", "generated"]
EpistemicStatus = Literal["supported", "unknown", "pending"]


class SourceRef(StrictModel):
    instrument: str = Field(min_length=1, max_length=200)
    administered_at: datetime | None = None


class BeliefEntry(StrictModel):
    entry_id: str = Id
    layer: Layer
    category: Construct = Field(alias="construct")  # "construct" would shadow BaseModel.construct
    construct_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z_]+(\.[a-z0-9_]+)+$")
    value: dict[str, Any] | None = None
    text: str = Field(default="", max_length=4000)
    source: Source
    source_ref: SourceRef | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=200)
    confidence: float | None = Field(default=None, ge=0, le=1)
    scope: list[str] = Field(default_factory=list, max_length=50)
    modifiable: bool = True
    epistemic_status: EpistemicStatus = "supported"
    runtime_policies: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def check(self) -> BeliefEntry:
        if self.category not in LAYER_CONSTRUCTS[self.layer]:
            raise ValueError(f"construct {self.category} does not belong to layer {self.layer}")
        if not self.construct_id.startswith(self.category + "."):
            raise ValueError("construct_id must start with '<construct>.'")
        if self.source == "measurement" and self.source_ref is None:
            raise ValueError("measurement entries need source_ref (instrument and administration)")
        if self.epistemic_status == "unknown" and self.value is not None:
            raise ValueError("unknown entries must have value null")
        return self


class BeliefVersion(StrictModel):
    version_id: str
    persona_id: str
    k: int = Field(ge=0)
    base_version_id: str | None
    origin: Literal["construction", "evolution", "correction"]
    candidate_id: str | None = None
    committed_at: datetime
    entries: list[BeliefEntry]


class EntryChange(StrictModel):
    entry_id: str
    layer: Layer
    construct_id: str
    change: Literal["added", "changed", "removed"]
    before: BeliefEntry | None
    after: BeliefEntry | None


class VersionHistory(StrictModel):
    """One version and what it changed against the version it was based on."""

    version_id: str
    k: int
    base_version_id: str | None
    origin: Literal["construction", "evolution", "correction"]
    committed_at: datetime
    entries: int
    changes: list[EntryChange]


class MaterialItem(StrictModel):
    ref: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=8000)


class PersonaImportItem(StrictModel):
    persona_id: str = Id
    entries: list[BeliefEntry] = Field(min_length=1, max_length=500)
    material: list[MaterialItem] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_entries(self) -> PersonaImportItem:
        ids = [e.entry_id for e in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("entry_id must be unique within a persona")
        return self


class PersonaImportRequest(StrictModel):
    mode: Literal["create_only", "replace_initial"] = "create_only"
    personas: list[PersonaImportItem] = Field(min_length=1, max_length=1000)


class PersonaImportResult(StrictModel):
    persona_id: str
    status: Literal["created", "replaced", "skipped"]
    version_id: str
    unknown_constructs: list[str] = Field(default_factory=list)


class PersonaImportResponse(StrictModel):
    results: list[PersonaImportResult]


class PersonaSummary(StrictModel):
    persona_id: str
    active_version_id: str
    k: int
    entry_count: int
    unknown_constructs: list[str]
    open_episode_id: str | None
    candidates_awaiting_owner: int


# ---- persona state (one snapshot per action) -------------------------------------------------

class Refs(StrictModel):
    entries: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class PersonaState(StrictModel):
    L3: dict[str, Any]
    L2: dict[str, Any]
    L1: dict[str, Any]
    cross_check: str = ""
    dropped: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


# ---- episodes, observations, actions -----------------------------------------------------------

class Situation(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="zh", min_length=2, max_length=10)


class Position(StrictModel):
    """Situational identity and responsibilities. Goes into the state, never into beliefs."""

    identity: str = Field(min_length=1, max_length=100)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)


class Participant(StrictModel):
    participant_id: str = Id
    display_name: str = Field(min_length=1, max_length=60)
    persona_id: str | None = Field(default=None, min_length=1, max_length=120)
    position: Position | None = None


class EpisodeCreate(StrictModel):
    episode_id: str = Id
    situation: Situation
    participants: list[Participant] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def check(self) -> EpisodeCreate:
        pids = [p.participant_id for p in self.participants]
        personas = [p.persona_id for p in self.participants if p.persona_id]
        if len(pids) != len(set(pids)) or len(personas) != len(set(personas)):
            raise ValueError("participant_id and persona_id must be unique within an episode")
        if not personas:
            raise ValueError("an episode needs at least one persona participant")
        return self


class Episode(EpisodeCreate):
    status: Literal["open", "closed"]
    created_at: datetime
    closed_at: datetime | None = None


class Observation(StrictModel):
    seq: int = Field(ge=0)
    kind: Literal["utterance", "event"]
    actor: str | None = Field(default=None, max_length=120)
    content: str = Field(min_length=1, max_length=8000)
    addressed_to: list[str] = Field(default_factory=list, max_length=50)
    at: datetime | None = None


class ObservationBatch(StrictModel):
    items: list[Observation] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def increasing(self) -> ObservationBatch:
        seqs = [o.seq for o in self.items]
        if seqs != sorted(seqs) or len(seqs) != len(set(seqs)):
            raise ValueError("seq must be strictly increasing")
        return self


class LengthContract(StrictModel):
    unit: Literal["words", "chars"]
    soft: tuple[int, int]
    hard: tuple[int, int]

    @model_validator(mode="after")
    def check(self) -> LengthContract:
        if not (0 < self.hard[0] <= self.soft[0] <= self.soft[1] <= self.hard[1]):
            raise ValueError("need 0 < hard_min <= soft_min <= soft_max <= hard_max")
        return self


class OutputContract(StrictModel):
    length: LengthContract
    extra_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema of caller-specific fields filled in call 2"
    )


class Task(StrictModel):
    instruction: str = Field(min_length=1, max_length=4000)
    intent: str | None = Field(default=None, max_length=2000)


class ActionPrepare(StrictModel):
    action_id: str = Id
    participant_id: str = Id
    upto_seq: int = Field(ge=0)
    task: Task
    output_contract: OutputContract
    # 2: state inference, then generation + review (the paper's flow). 1: both in one call,
    # about half the latency, for live chat where the owner is waiting on every line.
    calls: Literal[1, 2] = 2


class ActionOutput(StrictModel):
    text: str
    addressed_to: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ActionState(StrictModel):
    action_id: str
    persona_id: str
    state: PersonaState
    state_issue: str | None = None  # e.g. the line was shown but the rest of the output failed


class Usage(StrictModel):
    model_calls: int
    attempts: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class PreparedAction(StrictModel):
    action_id: str
    status: Literal["prepared", "committed", "discarded"]
    output: ActionOutput
    belief_version_id: str
    state_id: str
    usage: Usage


class ActionCommit(StrictModel):
    seq: int = Field(ge=0)
    published_text: str | None = Field(default=None, max_length=8000)
    at: datetime | None = None


class EpisodeClose(StrictModel):
    outcome: dict[str, Any] | None = None
    reflect_no_later_than: datetime | None = None


class Explanation(StrictModel):
    action_id: str
    persona_id: str
    belief_version_id: str
    summary: str
    basis: dict[str, str]
    entries: list[dict[str, str]]


class EpisodeJob(StrictModel):
    """After-episode job of one persona (S3: `consolidate` = memories + monitoring)."""

    persona_id: str
    job: str
    status: Literal["pending", "running", "done", "failed"]
    runs: int
    error: str | None = None
    updated_at: datetime


class Memory(StrictModel):
    memory_id: str
    episode_id: str
    kind: Literal["episodic", "relational"]
    about_persona_id: str | None = None
    about_name: str | None = None
    text: str
    evidence: list[str]
    created_at: datetime


# ---- owner feedback ---------------------------------------------------------------------------

FeedbackTarget = Literal["L1", "L2.strategy", "L2.need", "L2.value", "L2.goal", "L3", "wording",
                         "systemic", "fact_check", "none"]


class FeedbackReason(StrictModel):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=200)
    target: FeedbackTarget


class FeedbackTaxonomy(StrictModel):
    judgments: list[str] = Field(min_length=2, max_length=10)
    reasons: list[FeedbackReason] = Field(min_length=1, max_length=50)


class FeedbackCreate(StrictModel):
    feedback_id: str = Id
    persona_id: str = Id
    action_id: str = Id
    taxonomy: str = Field(min_length=1, max_length=60)
    judgment: str = Field(min_length=1, max_length=40)
    reason_code: str | None = Field(default=None, max_length=60)
    note: str | None = Field(
        default=None, max_length=2000, description="Archived only; never evidence for evolution"
    )
    submitted_at: datetime | None = None


class FeedbackComplete(StrictModel):
    persona_id: str = Id


class EpisodeFeedback(StrictModel):
    """One owner's feedback on one episode, and whether they have finished."""

    episode_id: str
    persona_id: str
    completed_at: datetime | None = None
    items: list[FeedbackCreate]


# ---- controlled evolution ---------------------------------------------------------------------

CandidateStatus = Literal["awaiting_owner", "approved", "committed", "rejected", "deferred",
                          "pending_verification", "pending_realization", "no_commit"]


class Edit(StrictModel):
    entry_id: str
    field: str
    before: Any = Field(alias="from")
    after: Any = Field(alias="to")


class EvidenceItem(StrictModel):
    kind: Literal["action", "feedback", "memory", "observation", "monitoring"]
    ref: str
    quote: str | None = None


class TestCase(StrictModel):
    type: Literal["target", "similar", "out_of_scope"]
    situation: str
    before: str
    after: str
    verdict: str


class Candidate(StrictModel):
    candidate_id: str
    persona_id: str
    base_version_id: str
    layer: Layer
    summary: str
    edits: list[Edit]
    scope: list[str]
    evidence_for: list[EvidenceItem]
    evidence_against: list[EvidenceItem]
    strength: Literal["strong", "limited"]
    test: list[TestCase]
    status: CandidateStatus
    episode_id: str | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None


class CandidateDecision(StrictModel):
    decision: Literal["approve", "reject", "defer"]
    decided_by: str = Field(default="owner", min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=2000)


# ---- events, errors ---------------------------------------------------------------------------

class OutboxEvent(StrictModel):
    seq: int
    type: Literal["candidate.awaiting_owner", "version.committed", "reflection.completed",
                  "episode.jobs_failed"]
    persona_id: str | None = None
    episode_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime


class EventPage(StrictModel):
    items: list[OutboxEvent]
    next_after: int


class ErrorBody(StrictModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(StrictModel):
    error: ErrorBody
