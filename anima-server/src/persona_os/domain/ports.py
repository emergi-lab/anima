"""Model capability port. An action uses two calls (rules §4), or one `respond` call that infers
the state and writes the reply together when the caller asks for speed (the desktop pet's chat);
after an episode closes, each persona gets one more call that writes its memories and
monitoring (S3)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, Protocol, TypeVar

from pydantic import Field, model_validator

from persona_os.domain.contract import ActionOutput, PersonaState, StrictModel

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ModelResult(Generic[T]):
    value: T
    model_name: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_version: str = ""
    fingerprint: str = ""


class ReviewCheck(StrictModel):
    check: str
    passed: bool = Field(alias="pass")
    note: str | None = None


class GenerationDraft(StrictModel):
    """Output of call 2: initial response, analytic review, final action and its basis.
    Only `final` becomes behaviour; the rest is kept as generation record (paper §3.3.2)."""

    draft: str
    review: list[ReviewCheck]
    unresolved: list[str] = Field(default_factory=list)
    state_issue: str | None = None
    final: ActionOutput
    action_basis: dict[str, Any]


class StateInput(StrictModel):
    """Everything call 1 sees: beliefs B_k, previous state Z_{t-1}, evidence E_t."""

    beliefs: list[dict[str, Any]]
    previous_state: PersonaState | None
    situation: dict[str, Any]
    self_participant_id: str
    participants: list[dict[str, Any]]
    position: dict[str, Any] | None
    observations: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    task: dict[str, Any]


class GenerationInput(StrictModel):
    state: PersonaState
    beliefs: list[dict[str, Any]]
    situation: dict[str, Any]
    self_participant_id: str
    participants: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    task: dict[str, Any]
    output_contract: dict[str, Any]
    repair_feedback: str | None = None


class RespondInput(StateInput):
    """The one-call action: what call 1 sees plus what call 2 needs to write the reply."""

    output_contract: dict[str, Any]
    repair_feedback: str | None = None


class Response(StrictModel):
    """Output of the one-call action, written in this order so the line can be shown before the
    rest is done: the reply, then the state Z_t, the review and the basis."""

    final: ActionOutput
    state: PersonaState

    @model_validator(mode="before")
    @classmethod
    def _state_fields_inside_state(cls, data: Any) -> Any:
        """The model sometimes closes `state` early and writes its last fields beside it."""
        if isinstance(data, dict) and isinstance(data.get("state"), dict):
            stray = {k: data.pop(k) for k in ("L1", "L2", "L3", "cross_check", "dropped",
                                              "unknown") if k in data}
            data["state"] = {**stray, **data["state"]}
        return data
    review: list[ReviewCheck] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    state_issue: str | None = None
    action_basis: dict[str, Any]


OnFinal = Callable[[ActionOutput], Awaitable[None]]


class ConsolidationInput(StrictModel):
    """After-episode call: the whole episode as this persona lived it."""

    beliefs: list[dict[str, Any]]
    situation: dict[str, Any]
    self_participant_id: str
    participants: list[dict[str, Any]]
    position: dict[str, Any] | None
    observations: list[dict[str, Any]]
    # This persona's committed actions: observation seq, text, and the state it acted from.
    own_actions: list[dict[str, Any]]
    outcome: dict[str, Any] | None


class EpisodicMemory(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list)


class RelationalMemory(StrictModel):
    participant_id: str
    summary: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(default_factory=list)


class MonitoringItem(StrictModel):
    entry_id: str
    direction: Literal["above", "below", "contrary"]
    note: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(default_factory=list)


class Consolidation(StrictModel):
    episodic: EpisodicMemory
    relational: list[RelationalMemory] = Field(default_factory=list)
    monitoring: list[MonitoringItem] = Field(default_factory=list)


# ---- controlled evolution (S4): one reflection call, then one call judging the frozen tests ----

class EvidenceGroup(StrictModel):
    """Evidence the program gathered and counted; the model only interprets it."""

    key: str
    kind: Literal["feedback", "monitoring"]
    target: str  # feedback target (L1 / L2.strategy / L3) or the monitored entry_id
    label: str
    items: list[dict[str, Any]]


class ReflectionInput(StrictModel):
    beliefs: list[dict[str, Any]]
    groups: list[EvidenceGroup]
    # How much each entry may still move this term (numbers) and L3 additions left.
    remaining: dict[str, Any]
    situation: dict[str, Any]  # the episode just reflected on: language and kind of setting


class ProposedEdit(StrictModel):
    entry_id: str
    field: Literal["value.baseline", "value.satisfaction", "value.frustration", "text.append"]
    to: float | None = None
    text: str | None = Field(default=None, max_length=300)


class TestSituation(StrictModel):
    type: Literal["target", "similar", "out_of_scope"]
    situation: str = Field(min_length=1, max_length=1500)
    task: str = Field(min_length=1, max_length=500)


class Proposal(StrictModel):
    group_key: str
    layer: Literal["L1", "L2", "L3"]
    summary: str = Field(min_length=1, max_length=400)
    edits: list[ProposedEdit] = Field(min_length=1, max_length=4)
    scope: list[str] = Field(default_factory=list, max_length=5)
    tests: list[TestSituation] = Field(min_length=6, max_length=6)


class DeferredGroup(StrictModel):
    group_key: str
    why: str


class ReflectionOutput(StrictModel):
    proposals: list[Proposal] = Field(default_factory=list)
    deferred: list[DeferredGroup] = Field(default_factory=list)


class JudgeInput(StrictModel):
    summary: str
    scope: list[str]
    cases: list[dict[str, Any]]  # {index, type, situation, before, after}


class Verdict(StrictModel):
    index: int
    verdict: Literal["realized", "not_realized", "unchanged", "changed"]
    note: str = ""


class JudgeOutput(StrictModel):
    verdicts: list[Verdict]


class PersonaModel(Protocol):
    async def infer_state(self, data: StateInput) -> ModelResult[PersonaState]: ...

    async def generate_and_review(self, data: GenerationInput) -> ModelResult[GenerationDraft]: ...

    async def respond(self, data: RespondInput,
                      on_final: OnFinal | None = None) -> ModelResult[Response]:
        """`on_final` gets the reply as soon as it is complete in the output, if given."""
        ...

    async def consolidate(self, data: ConsolidationInput) -> ModelResult[Consolidation]: ...

    async def reflect(self, data: ReflectionInput) -> ModelResult[ReflectionOutput]: ...

    async def judge_tests(self, data: JudgeInput) -> ModelResult[JudgeOutput]: ...
