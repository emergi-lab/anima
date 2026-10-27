"""Persona storage (own database). Versions are append-only; entries stored as validated JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool, StaticPool


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PersonaRow(Base):
    __tablename__ = "personas"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    active_version_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BeliefVersionRow(Base):
    __tablename__ = "belief_versions"
    __table_args__ = (UniqueConstraint("namespace", "persona_id", "k", name="uq_version_k"),)

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(60), index=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    k: Mapped[int] = mapped_column(Integer)
    base_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(20))
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BeliefEntryRow(Base):
    __tablename__ = "belief_entries"
    __table_args__ = (UniqueConstraint("version_id", "entry_id", name="uq_entry_in_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("belief_versions.version_id"), index=True)
    entry_id: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)


class MaterialRow(Base):
    """Source material (inference evidence) supplied at construction; never edited."""

    __tablename__ = "persona_materials"
    __table_args__ = (UniqueConstraint("namespace", "persona_id", "ref", name="uq_material_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60), index=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    ref: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)


def make_engine(url: str) -> AsyncEngine:
    if url.endswith("://") or ":memory:" in url:  # one shared in-memory database
        return create_async_engine(url, poolclass=StaticPool,
                                   connect_args={"check_same_thread": False})
    # A file database: a connection per session, so background jobs run concurrently
    # with requests. Create the parent folder on first start.
    _, _, path = url.partition(":///")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 30})


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn) -> None:
    """Columns added after a database was created (nullable or with a scalar default)."""
    have = inspect(conn)
    for table in Base.metadata.sorted_tables:
        present = {col["name"] for col in have.get_columns(table.name)}
        for col in table.columns:
            if col.name in present:
                continue
            kind = col.type.compile(dialect=conn.dialect)
            default = col.default.arg if col.default is not None and col.default.is_scalar \
                else None
            fill = f" DEFAULT '{default}'" if isinstance(default, str) else ""
            conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {kind}{fill}'))


def sessions(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


# ---- situational adaptation (S2) ------------------------------------------------------------

class EpisodeRow(Base):
    __tablename__ = "episodes"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    situation: Mapped[dict] = mapped_column(JSON)
    participants: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="open")
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reflect_no_later_than: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                   nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EpisodePersonaRow(Base):
    """Which personas take part; at most one open episode per persona (checked in code)."""

    __tablename__ = "episode_personas"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), primary_key=True, index=True)
    participant_id: Mapped[str] = mapped_column(String(120))
    open: Mapped[bool] = mapped_column(default=True)


class ObservationRow(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("namespace", "episode_id", "seq", name="uq_obs_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60))
    episode_id: Mapped[str] = mapped_column(String(120), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    action_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ActionRow(Base):
    __tablename__ = "actions"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), index=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    participant_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12))
    request_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSON)
    generation: Mapped[dict] = mapped_column(JSON)  # draft, review, basis: proposal record
    belief_version_id: Mapped[str] = mapped_column(String(36))
    state_id: Mapped[str] = mapped_column(String(36))
    usage: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonaStateRow(Base):
    """State snapshot Z_t. `final` once its action is committed; only final states feed Z_{t-1}."""

    __tablename__ = "persona_states"

    state_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(60))
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    episode_id: Mapped[str] = mapped_column(String(120))
    action_id: Mapped[str] = mapped_column(String(120))
    version_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    final: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelCallRow(Base):
    """One row per model call attempt (paper appendix H.1); no prompt or response text."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60))
    action_id: Mapped[str] = mapped_column(String(120), index=True)
    call: Mapped[str] = mapped_column(String(30))
    attempt: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool]
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- after-episode jobs, memory, monitoring, outbox (S3) ----------------------------------------

class EpisodeJobRow(Base):
    """One after-episode job per persona: `consolidate` (memory + monitoring). Claimed by moving
    pending → running in one UPDATE, so a job never runs twice at the same time."""

    __tablename__ = "episode_jobs"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    job: Mapped[str] = mapped_column(String(20), primary_key=True)
    # pending | running | done | failed
    status: Mapped[str] = mapped_column(String(10), default="pending")
    runs: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryRow(Base):
    """Episodic memory of one episode, or relational memory about one other participant."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60))
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    episode_id: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))  # episodic | relational
    # Relational memories: the other participant's persona_id (when a persona) and name.
    about_persona_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    about_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MonitoringRow(Base):
    """State–behaviour monitoring: the persona acted away from a belief entry in this episode."""

    __tablename__ = "monitoring"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60))
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    episode_id: Mapped[str] = mapped_column(String(120))
    version_id: Mapped[str] = mapped_column(String(36))
    entry_id: Mapped[str] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(10))  # above | below | contrary
    note: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutboxRow(Base):
    __tablename__ = "outbox"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(60), index=True)
    type: Mapped[str] = mapped_column(String(40))
    persona_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- owner feedback (S4 input) ------------------------------------------------------------------

class FeedbackTaxonomyRow(Base):
    __tablename__ = "feedback_taxonomies"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    body: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FeedbackRow(Base):
    """The owner's judgment of one committed action; at most one per action. `note` is archived
    only and never used as evidence (rules §6)."""

    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("namespace", "action_id", name="uq_feedback_action"),)

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    episode_id: Mapped[str] = mapped_column(String(120), index=True)
    action_id: Mapped[str] = mapped_column(String(120))
    taxonomy: Mapped[str] = mapped_column(String(60))
    judgment: Mapped[str] = mapped_column(String(40))
    reason_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    target: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FeedbackCompletionRow(Base):
    """The owner finished judging this episode: feedback is locked and reflection may start."""

    __tablename__ = "feedback_completions"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- controlled evolution (S4) ------------------------------------------------------------------

class ReflectionRow(Base):
    """One reflection of one persona after the owner completed an episode's feedback."""

    __tablename__ = "reflections"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    # candidates | defer: what came out; detail keeps evidence counts and deferred reasons.
    result: Mapped[str] = mapped_column(String(12))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CandidateRow(Base):
    """A revision candidate (paper algorithm 2). Edits hold numbers for the service only; the
    owner sees `summary` and the before/after `test`."""

    __tablename__ = "candidates"

    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(60), index=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    episode_id: Mapped[str] = mapped_column(String(120))
    base_version_id: Mapped[str] = mapped_column(String(36))
    layer: Mapped[str] = mapped_column(String(4))
    summary: Mapped[str] = mapped_column(Text)
    edits: Mapped[list] = mapped_column(JSON)
    scope: Mapped[list] = mapped_column(JSON)
    evidence_for: Mapped[list] = mapped_column(JSON)
    evidence_against: Mapped[list] = mapped_column(JSON)
    strength: Mapped[str] = mapped_column(String(10))
    test: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---- distillation (Anima A1): raw material → reviewed initial beliefs B0 ----------------------

class DistillationRow(Base):
    __tablename__ = "distillations"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    distillation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # draft → running → review → committed; running → failed (start again to resume)
    stage: Mapped[str] = mapped_column(String(12), default="draft")
    # where a running job is: extracting → inducing → reviewing
    step: Mapped[str | None] = mapped_column(String(12), nullable=True)
    target: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {name, aliases}
    # initial: raw material → B0. evolution: recent chats (+ material) → revisions of
    # base_version_id, committed as the next version.
    kind: Mapped[str] = mapped_column(String(10), default="initial")
    base_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    persona_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DistillSourceRow(Base):
    """One uploaded text file; never edited."""

    __tablename__ = "distill_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distillation_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    # material: any text. chat: a chat transcript, only the owner's lines are quotable.
    # monitoring: deviations noted after chats, repeated across sessions.
    kind: Mapped[str] = mapped_column(String(10), default="material")


class DistillSegmentRow(Base):
    """A slice of a source; `relevant` once the target is known, then extracted one call each."""

    __tablename__ = "distill_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distillation_id: Mapped[str] = mapped_column(String(36), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("distill_sources.id"))
    idx: Mapped[int] = mapped_column(Integer)  # order across all sources
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    relevant: Mapped[bool] = mapped_column(default=False)
    # pending → done / failed; irrelevant segments stay pending and are never sent
    status: Mapped[str] = mapped_column(String(10), default="pending")
    runs: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DistillEvidenceRow(Base):
    """A verbatim quote about the target (checked against its segment) and what it shows."""

    __tablename__ = "distill_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distillation_id: Mapped[str] = mapped_column(String(36), index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("distill_segments.id"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    offset: Mapped[int] = mapped_column(Integer)  # position of the quote in its segment
    note: Mapped[str] = mapped_column(Text)
    construct: Mapped[str] = mapped_column(String(20))
    facet: Mapped[str | None] = mapped_column(String(120), nullable=True)  # trait: construct_id


class DistillEntryRow(Base):
    """A belief entry under review. `payload` is a BeliefEntry; status proposed / accepted /
    rejected / edited / added. `original_text` keeps the induced wording once edited. In an
    evolution, `payload` is the revised entry and `base_payload` the entry it replaces."""

    __tablename__ = "distill_entries"
    __table_args__ = (UniqueConstraint("distillation_id", "entry_id", name="uq_distill_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distillation_id: Mapped[str] = mapped_column(String(36), index=True)
    entry_id: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="proposed")
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # evolution: the entry as it is in the base version (None for a new entry)
    base_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # evolution: why it changed
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- self-reflection (Anima): the persona looks back at recent chats --------------------------

class SelfReflectionRow(Base):
    """One reflection: the persona's diary and findings, and the evolution versions it proposes
    for the owner to approve. running → ready → approved / declined; running → failed."""

    __tablename__ = "self_reflections"

    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    reflection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(120), index=True)
    persona_name: Mapped[str] = mapped_column(String(60))
    base_version_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(10), default="running")
    lines: Mapped[list] = mapped_column(JSON, default=list)  # what it looked back at, with ids
    diary: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    versions: Mapped[list] = mapped_column(JSON, default=list)
    chosen: Mapped[str | None] = mapped_column(String(4), nullable=True)
    committed_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

