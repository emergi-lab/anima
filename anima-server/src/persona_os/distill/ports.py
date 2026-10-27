"""Model capability for distillation: one extraction call per segment, one induction call per
construct category, one review call over the whole draft. An evolution extracts from recent chats
instead and revises the existing entries of each category rather than inducing new ones."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from persona_os.domain.contract import Construct, StrictModel
from persona_os.domain.ports import ModelResult


class ExtractInput(StrictModel):
    target: dict[str, Any]  # {name, aliases}
    language: str
    context: str  # tail of the previous segment; not quotable
    segment: str
    # A second, targeted pass: only evidence for this Big Five domain (name, facets, cues).
    focus: dict[str, Any] | None = None


class ExtractedEvidence(StrictModel):
    quote: str = Field(min_length=1, max_length=600)
    note: str = Field(min_length=1, max_length=400)
    category: Construct = Field(alias="construct")  # "construct" shadows BaseModel.construct
    facet: str | None = Field(default=None, max_length=120)  # trait evidence: its construct_id


class ExtractOutput(StrictModel):
    evidence: list[ExtractedEvidence] = Field(default_factory=list, max_length=30)


class InduceInput(StrictModel):
    target: dict[str, Any]
    language: str
    layer: str
    category: str = Field(alias="construct")
    known_ids: list[str]  # construct_id suggestions for this category
    evidence: list[dict[str, Any]]  # {id: "ev:<n>", quote, note}


class InducedEntry(StrictModel):
    construct_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1500)
    value: dict[str, Any] | None = None
    scope: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=200)


class InduceOutput(StrictModel):
    entries: list[InducedEntry] = Field(default_factory=list, max_length=20)


class ReviewInput(StrictModel):
    target: dict[str, Any]
    language: str
    entries: list[dict[str, Any]]  # {entry_id, construct_id, text, scope, confidence, quotes}


class ReviewNote(StrictModel):
    entry_id: str
    issue: str = Field(min_length=1, max_length=400)
    suggestion: str = Field(default="", max_length=600)


class ReviewOutput(StrictModel):
    notes: list[ReviewNote] = Field(default_factory=list, max_length=100)


class ReviseInput(StrictModel):
    target: dict[str, Any]
    language: str
    layer: str
    category: str = Field(alias="construct")
    known_ids: list[str]
    entries: list[dict[str, Any]]  # this category in the base version
    evidence: list[dict[str, Any]]  # {id, quote, note, source}
    limits: dict[str, Any]


class Revision(StrictModel):
    action: Literal["revise", "add"]
    entry_id: str | None = None  # revise: an entry of `entries`
    construct_id: str = Field(default="", max_length=120)  # add
    # revise: the whole new text (L1/L2) or the sentence to append (L3); add: the new entry
    text: str = Field(min_length=1, max_length=1500)
    value: dict[str, Any] | None = None
    scope: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=400)
    evidence: list[str] = Field(default_factory=list, max_length=50)


class ReviseOutput(StrictModel):
    revisions: list[Revision] = Field(default_factory=list, max_length=20)


class DistillModel(Protocol):
    async def extract_evidence(self, data: ExtractInput) -> ModelResult[ExtractOutput]: ...

    async def induce_beliefs(self, data: InduceInput) -> ModelResult[InduceOutput]: ...

    async def review_beliefs(self, data: ReviewInput) -> ModelResult[ReviewOutput]: ...

    async def extract_changes(self, data: ExtractInput) -> ModelResult[ExtractOutput]: ...

    async def revise_beliefs(self, data: ReviseInput) -> ModelResult[ReviseOutput]: ...

    async def review_changes(self, data: ReviewInput) -> ModelResult[ReviewOutput]: ...
