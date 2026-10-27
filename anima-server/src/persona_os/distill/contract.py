"""Wire models of the distillation API (docs/接口契约.md §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from persona_os.domain.contract import BeliefEntry, Construct, Id, Layer, StrictModel

Stage = Literal["draft", "running", "review", "failed", "committed"]
Step = Literal["extracting", "inducing", "reviewing"]
EntryStatus = Literal["proposed", "accepted", "rejected", "edited", "added"]
Kind = Literal["initial", "evolution"]


class Target(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class DistillationCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class SourceUpload(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)


class SourceInfo(StrictModel):
    source_id: int
    name: str
    chars: int
    segments: int
    kind: Literal["material", "chat", "monitoring"] = "material"


class Character(StrictModel):
    name: str
    lines: int


class Preview(StrictModel):
    """What a start with this target would send: relevant segments and a token estimate."""

    relevant_segments: int
    relevant_chars: int
    mentions: int
    estimated_calls: int
    estimated_prompt_tokens: int


class DistillationStart(StrictModel):
    target: Target
    persona_id: str = Id
    language: str = Field(default="zh-CN", min_length=2, max_length=20)


class Progress(StrictModel):
    segments: int
    relevant: int
    extracted: int
    failed: int
    evidence: int
    entries: int


class Usage(StrictModel):
    model_calls: int
    prompt_tokens: int
    completion_tokens: int


class Distillation(StrictModel):
    distillation_id: str
    title: str
    kind: Kind = "initial"
    base_version_id: str | None = None
    stage: Stage
    step: Step | None
    target: Target | None
    persona_id: str | None
    language: str
    error: str | None
    sources: list[SourceInfo]
    progress: Progress
    usage: Usage
    created_at: datetime
    updated_at: datetime


class EvidenceBrief(StrictModel):
    evidence_id: str
    quote: str


class ReviewedEntry(StrictModel):
    entry: BeliefEntry
    status: EntryStatus
    original_text: str | None
    review_note: str | None
    evidence: list[EvidenceBrief]
    # evolution only: the entry in the base version (None for a new entry) and why it changes
    before: BeliefEntry | None = None
    reason: str | None = None


class Evidence(StrictModel):
    evidence_id: str
    quote: str
    note: str
    category: str = Field(alias="construct")
    source: str
    segment_text: str
    quote_offset: int


class EntryUpdate(StrictModel):
    """Accept or reject an entry, or rewrite it (a rewrite counts as accepted)."""

    status: Literal["accepted", "rejected", "proposed"] | None = None
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    scope: list[str] | None = Field(default=None, max_length=50)


class EntryAdd(StrictModel):
    layer: Layer
    category: Construct = Field(alias="construct")  # "construct" shadows BaseModel.construct
    construct_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z_]+(\.[a-z0-9_]+)+$")
    text: str = Field(min_length=1, max_length=4000)
    scope: list[str] = Field(default_factory=list, max_length=50)


class CommitResult(StrictModel):
    persona_id: str
    version_id: str
    entries: int
    unknown_constructs: list[str]
    k: int = 0


# ---- evolution (Anima A4): recent chats → revisions of the active version ----

class EvolutionStart(StrictModel):
    persona_name: str = Field(min_length=1, max_length=60)


class EvolutionPreview(StrictModel):
    """What an evolution would read: chats since the active version and repeated deviations."""

    since_version: int
    since: datetime
    sessions: int
    owner_lines: int
    persona_lines: int
    deviations: int  # monitoring groups seen in ≥2 sessions
    estimated_calls: int
    enough: bool  # at least one owner line or one repeated deviation
    running: str | None  # an unfinished evolution of this persona, if any


class TryResult(StrictModel):
    """The same situation answered by the base version and by it with this one change."""

    situation: str
    before: str
    after: str
