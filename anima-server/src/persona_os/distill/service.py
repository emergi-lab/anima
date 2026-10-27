"""Distillation (Anima A1): raw text about a character → reviewed initial beliefs B0.

Steps (docs/未修复.md §2): upload and slice sources; pick the target; keep segments that mention
it; one extraction call per segment (quotes are checked verbatim against the segment); one
induction call per construct category; one review call over the draft; the owner accepts,
rejects, rewrites or adds entries; commit writes B0 through the persona store. The job runs in
the background and resumes from the segments not yet extracted.

An evolution (`kind = evolution`, see `evolve.py`) runs the same job over recent chats: extraction
keeps only the owner's lines, the induction step revises the base version's entries, and commit
writes the next version.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.distill import contract as d
from persona_os.distill import evolve as ev
from persona_os.distill import text as tx
from persona_os.distill.ports import (
    DistillModel,
    ExtractInput,
    InducedEntry,
    InduceInput,
    ReviewInput,
    ReviseInput,
)
from persona_os.domain import contract as c
from persona_os.domain.errors import REFUSALS, ServiceError
from persona_os.infra.db import (
    BeliefEntryRow,
    BeliefVersionRow,
    DistillationRow,
    DistillEntryRow,
    DistillEvidenceRow,
    DistillSegmentRow,
    DistillSourceRow,
    ModelCallRow,
    PersonaRow,
    now,
)

log = logging.getLogger(__name__)

MAX_FAILED_SHARE = 0.1  # more failed segments than this stops before induction
MAX_EVIDENCE_PER_CATEGORY = 200  # evenly sampled beyond this (one induction call per category)
TRY_LENGTH = {"unit": "chars", "soft": [20, 80], "hard": [5, 200]}
PROMPT_OVERHEAD_TOKENS = 1500  # system prompt and JSON framing per call, for the estimate
TOKENS_PER_CHAR = 0.7

# construct_id suggestions per category; the model may coin new snake_case ids.
KNOWN_IDS: dict[str, list[str]] = {
    "overall_identity": ["overall_identity.self_view"],
    "imago": [f"imago.{x}" for x in ("warrior", "traveler", "sage", "maker", "lover",
                                     "caregiver", "friend", "ritual_keeper")],
    "life_course": ["life_course.turning_point", "life_course.origin"],
    "need": ["need.autonomy", "need.competence", "need.relatedness"],
    "value": [f"value.{x}" for x in ("benevolence", "universalism", "self_direction",
                                     "achievement", "power", "security", "conformity",
                                     "tradition", "hedonism", "stimulation")],
    "goal": ["goal.current"],
    "strategy": ["strategy.active_coping", "strategy.planning", "strategy.instrumental_support",
                 "strategy.avoidance", "strategy.humor"],
    "trait": [f"trait.{domain}.{facet}" for domain, facets in {
        "openness": ("intellectual_curiosity", "aesthetic_sensitivity", "creative_imagination"),
        "conscientiousness": ("organization", "productiveness", "responsibility"),
        "extraversion": ("sociability", "assertiveness", "energy_level"),
        "agreeableness": ("compassion", "respectfulness", "trust"),
        "neuroticism": ("anxiety", "depression", "emotional_volatility"),
    }.items() for facet in facets],
}
LAYER_OF = {cat: layer for layer, cats in c.LAYER_CONSTRUCTS.items() for cat in cats}
# Traits are induced one Big Five domain at a time (a single call kept only the ~6 facets with the
# most evidence, and openness never made it), and a domain with too little evidence gets a
# second, focused look at the segments that mention the character most.
DOMAINS = {"openness": "开放性", "conscientiousness": "尽责性", "extraversion": "外向性",
           "agreeableness": "宜人性", "neuroticism": "神经质"}
DOMAIN_CUES = {
    "openness": "好奇、求知、爱学新本事新东西、想象力与点子、会变通出奇招、喜欢新鲜或守旧怕变、"
                "对美的事物有感觉",
    "conscientiousness": "做事有没有章法、勤快肯干还是懒散拖延、靠不靠得住、守不守承诺",
    "extraversion": "爱不爱热闹说话、敢不敢出头好胜、精力旺不旺",
    "agreeableness": "体不体贴同情人、尊重谦让还是冲撞刻薄、信任人还是多疑",
    "neuroticism": "爱不爱担心害怕、会不会灰心低落、脾气急不急、情绪起伏大不大",
}
TRAIT_IDS = set(KNOWN_IDS["trait"])
SWEEP_MIN = 3  # a domain with fewer trait quotes than this is looked for again
SWEEP_SEGMENTS = 6  # segments per focused look
NUMERIC = ("baseline", "satisfaction", "frustration")


def _ev(evidence_id: int) -> str:
    return f"ev:{evidence_id}"


def _sample(items: list[Any], limit: int) -> list[Any]:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def _value(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only 0–1 numbers the engine understands; inferred levels are never exact."""
    if not raw:
        return None
    kept = {k: round(float(v), 2) for k, v in raw.items()
            if k in NUMERIC and isinstance(v, int | float) and 0 <= v <= 1}
    return kept | {"scale": "0-1"} if kept else None


def _construct_id(category: str, raw: str) -> str | None:
    cid = raw.strip().lower()
    if not cid.startswith(category + "."):
        cid = f"{category}.{cid.split('.')[-1]}"
    try:
        c.BeliefEntry.model_validate({"entry_id": "x", "layer": LAYER_OF[category],
                                      "construct": category, "construct_id": cid,
                                      "source": "inference"})
    except ValueError:
        return None
    return cid


class Distiller:
    def __init__(self, sessions: async_sessionmaker, model: DistillModel, runner,
                 store) -> None:
        self.sessions = sessions
        self.model = model
        self.runner = runner  # Engine: shared attempt and recording policy for model calls
        self.store = store  # PersonaStore: writes B0 on commit
        self._tasks: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- setup -----------------------------------------------------------------------------------
    async def create(self, ns: str, body: d.DistillationCreate) -> d.Distillation:
        distillation_id = str(uuid.uuid4())
        async with self.sessions() as s, s.begin():
            s.add(DistillationRow(namespace=ns, distillation_id=distillation_id,
                                  title=body.title))
        return await self.get(ns, distillation_id)

    async def evolution_preview(self, ns: str, persona_id: str,
                                persona_name: str = "它") -> d.EvolutionPreview:
        async with self.sessions() as s:
            version, entries = await self._active(s, ns, persona_id)
            sources, n = await ev.gather(s, ns, persona_id, persona_name, version.committed_at,
                                         entries)
            running = await s.scalar(select(DistillationRow.distillation_id).where(
                DistillationRow.namespace == ns, DistillationRow.persona_id == persona_id,
                DistillationRow.kind == "evolution", DistillationRow.stage != "committed"))
        segments = sum(len(tx.split(t)) for _, t, _ in sources)
        return d.EvolutionPreview(
            since_version=version.k, since=version.committed_at, **n,
            estimated_calls=segments + (min(segments * 2, len(LAYER_OF)) + 1 if segments else 0),
            enough=bool(n["owner_lines"] or n["deviations"]), running=running)

    async def create_evolution(self, ns: str, persona_id: str,
                               body: d.EvolutionStart) -> d.Distillation:
        """A draft evolution of the active version, its sources gathered from recent chats. The
        owner may add material before starting it; one unfinished evolution per persona."""
        async with self.sessions() as s, s.begin():
            version, entries = await self._active(s, ns, persona_id)
            running = await s.scalar(select(DistillationRow.distillation_id).where(
                DistillationRow.namespace == ns, DistillationRow.persona_id == persona_id,
                DistillationRow.kind == "evolution", DistillationRow.stage != "committed"))
            if running:
                raise ServiceError(409, "evolution_open", running)
            sources, _ = await ev.gather(s, ns, persona_id, body.persona_name,
                                         version.committed_at, entries)
            distillation_id = str(uuid.uuid4())
            s.add(DistillationRow(
                namespace=ns, distillation_id=distillation_id, kind="evolution",
                title=f"演化 B{version.k} → B{version.k + 1}", persona_id=persona_id,
                base_version_id=version.version_id))  # target set by start, which selects segments
            for name, text, kind in sources:
                await self._add_source(s, distillation_id, name, text, kind)
        return await self.get(ns, distillation_id)

    async def discard(self, ns: str, distillation_id: str) -> None:
        """Drop an evolution that has not been committed, or an initial distillation that has
        not produced entries yet (draft or failed). Model-call records stay."""
        async with self.sessions() as s, s.begin():
            row = await self._row(s, ns, distillation_id)
            allowed = ("draft", "failed", "review") if row.kind == "evolution" \
                else ("draft", "failed")
            if row.stage not in allowed:
                raise ServiceError(409, "not_discardable",
                                   f"{row.kind} in stage {row.stage} cannot be discarded")
            await s.execute(delete(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id))
            await s.execute(delete(DistillEvidenceRow).where(
                DistillEvidenceRow.distillation_id == distillation_id))
            await s.execute(delete(DistillSegmentRow).where(
                DistillSegmentRow.distillation_id == distillation_id))
            await s.execute(delete(DistillSourceRow).where(
                DistillSourceRow.distillation_id == distillation_id))
            await s.delete(row)

    async def add_source(self, ns: str, distillation_id: str,
                         body: d.SourceUpload) -> d.SourceInfo:
        async with self.sessions() as s, s.begin():
            row = await self._row(s, ns, distillation_id)
            if row.stage != "draft":
                raise ServiceError(409, "distillation_started",
                                   "sources can only be added before the first start")
            source, spans = await self._add_source(s, distillation_id, body.name, body.text,
                                                   "material")
            row.updated_at = now()
        return d.SourceInfo(source_id=source.id, name=body.name, chars=len(body.text),
                            segments=spans)

    @staticmethod
    async def _add_source(s, distillation_id: str, name: str, text: str,
                          kind: str) -> tuple[DistillSourceRow, int]:
        source = DistillSourceRow(distillation_id=distillation_id, name=name, text=text,
                                  kind=kind)
        s.add(source)
        await s.flush()
        first = await s.scalar(select(func.count()).select_from(DistillSegmentRow).where(
            DistillSegmentRow.distillation_id == distillation_id))
        spans = tx.split(text)
        s.add_all(DistillSegmentRow(distillation_id=distillation_id, source_id=source.id,
                                    idx=first + i, start=sp.start, end=sp.end)
                  for i, sp in enumerate(spans))
        return source, len(spans)

    async def characters(self, ns: str, distillation_id: str) -> list[d.Character]:
        async with self.sessions() as s:
            await self._row(s, ns, distillation_id)
            texts = (await s.scalars(select(DistillSourceRow.text).where(
                DistillSourceRow.distillation_id == distillation_id))).all()
        return [d.Character(name=n, lines=k) for n, k in tx.speakers(list(texts))]

    async def preview(self, ns: str, distillation_id: str, target: d.Target) -> d.Preview:
        async with self.sessions() as s:
            await self._row(s, ns, distillation_id)
            segments = await self._segment_texts(s, distillation_id)
        names = tx.names(target.model_dump())
        hits = [(text, tx.mentions(text, names)) for _, text in segments]
        relevant = [(t, n) for t, n in hits if n]
        chars = sum(len(t) for t, _ in relevant)
        # + induce (traits once per domain) and review; focused looks are not counted
        calls = len(relevant) + (len(LAYER_OF) + len(DOMAINS) - 1 + 1 if relevant else 0)
        return d.Preview(
            relevant_segments=len(relevant), relevant_chars=chars,
            mentions=sum(n for _, n in relevant), estimated_calls=calls,
            estimated_prompt_tokens=round(
                (chars + len(relevant) * tx.CONTEXT_CHARS) * TOKENS_PER_CHAR * 1.3)
            + calls * PROMPT_OVERHEAD_TOKENS)

    async def start(self, ns: str, distillation_id: str,
                    body: d.DistillationStart) -> d.Distillation:
        names = tx.names(body.target.model_dump())
        if not names:
            raise ServiceError(422, "invalid_request", "target.name is empty")
        async with self.sessions() as s, s.begin():
            row = await self._row(s, ns, distillation_id)
            if row.stage not in ("draft", "failed"):
                raise ServiceError(409, "distillation_not_startable",
                                   f"stage is {row.stage}; only draft or failed can start")
            segments = await self._segment_texts(s, distillation_id)
            if not segments:
                raise ServiceError(422, "no_sources", "upload at least one source first")
            target = body.target.model_dump()
            if row.target != target:  # a new target invalidates everything extracted so far
                await s.execute(delete(DistillEvidenceRow).where(
                    DistillEvidenceRow.distillation_id == distillation_id))
                await s.execute(update(DistillSegmentRow).where(
                    DistillSegmentRow.distillation_id == distillation_id).values(
                    status="pending", runs=0, error=None, relevant=False))
                kinds = await self._segment_kinds(s, distillation_id)
                hit = [sid for sid, t in segments
                       if kinds[sid] != "material" or tx.mentions(t, names)]
                if not hit:
                    raise ServiceError(422, "no_mentions",
                                       "no segment mentions the target name or aliases")
                await s.execute(update(DistillSegmentRow).where(
                    DistillSegmentRow.id.in_(hit)).values(relevant=True))
            else:  # resume: failed segments get another try
                await s.execute(update(DistillSegmentRow).where(
                    DistillSegmentRow.distillation_id == distillation_id,
                    DistillSegmentRow.status == "failed").values(status="pending"))
            if row.kind == "initial":  # don't spend model calls on a persona that can't be stored
                await self.store.ensure_room(ns, body.persona_id, s)
            if row.kind == "evolution" and body.persona_id != row.persona_id:
                raise ServiceError(422, "invalid_request",
                                   f"this evolution belongs to {row.persona_id}")
            row.target, row.persona_id, row.language = target, body.persona_id, body.language
            if row.kind == "initial":
                row.title = body.target.name  # named after its character from here on
            row.stage, row.step, row.error, row.updated_at = "running", "extracting", None, now()
        self.schedule(ns, distillation_id)
        return await self.get(ns, distillation_id)

    # ---- background job --------------------------------------------------------------------------
    def schedule(self, ns: str, distillation_id: str) -> None:
        task = asyncio.create_task(self.run(ns, distillation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume(self) -> None:
        async with self.sessions() as s:
            rows = (await s.execute(select(DistillationRow.namespace,
                                           DistillationRow.distillation_id).where(
                DistillationRow.stage == "running"))).all()
        for ns, distillation_id in rows:
            self.schedule(ns, distillation_id)

    async def idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def run(self, ns: str, distillation_id: str) -> None:
        async with self._locks[distillation_id]:
            try:
                await self._extract_all(ns, distillation_id)
                if await self._stop_if_too_many_failed(distillation_id):
                    return
                async with self.sessions() as s:
                    kind = (await s.get(DistillationRow, (ns, distillation_id))).kind
                if kind == "initial":
                    await self._sweep(ns, distillation_id)
                if kind == "evolution":
                    await self._revise_all(ns, distillation_id)
                else:
                    await self._induce_all(ns, distillation_id)
                await self._review(ns, distillation_id)
                await self._set(distillation_id, stage="review", step=None)
            except Exception as exc:  # a failed job must never take the service down
                if not isinstance(exc, ServiceError):
                    log.exception("distillation crashed: %s", distillation_id)
                await self._set(distillation_id, stage="failed", error=str(exc)[:300])

    async def _extract_all(self, ns: str, distillation_id: str) -> None:
        await self._set(distillation_id, step="extracting")
        async with self.sessions() as s:
            row = await s.get(DistillationRow, (ns, distillation_id))
            pending = (await s.scalars(select(DistillSegmentRow.id).where(
                DistillSegmentRow.distillation_id == distillation_id,
                DistillSegmentRow.relevant, DistillSegmentRow.status == "pending")
                .order_by(DistillSegmentRow.idx))).all()
        target, language = row.target, row.language
        await asyncio.gather(*(self._extract(ns, distillation_id, sid, target, language)
                               for sid in pending))

    async def _extract(self, ns: str, distillation_id: str, segment_id: int,
                       target: dict, language: str, domain: str | None = None) -> None:
        """One segment's evidence; with `domain`, a focused second look that only adds trait
        quotes of that Big Five domain and leaves the segment's state alone."""
        async with self.sessions() as s:
            seg = await s.get(DistillSegmentRow, segment_id)
            source = await s.get(DistillSourceRow, seg.source_id)
        segment = source.text[seg.start:seg.end]
        context = source.text[max(0, seg.start - tx.CONTEXT_CHARS):seg.start]
        facets = [i for i in KNOWN_IDS["trait"] if i.split(".")[1] == domain] if domain else []
        data = ExtractInput(target=target, language=language, context=context, segment=segment,
                            focus={"domain": DOMAINS[domain], "cues": DOMAIN_CUES[domain],
                                   "facets": facets} if domain else None)
        lived = source.kind != "material"  # an evolution's chats and deviations
        extract = self.model.extract_changes if lived else self.model.extract_evidence
        calls: list[ModelCallRow] = []
        try:
            result = await self.runner._attempts(
                ns, f"distill:{distillation_id}/" + (f"{domain}-seg{seg.idx}" if domain
                                                      else f"seg{seg.idx}"),
                "evolve_extract" if lived else "distill_extract", calls,
                lambda _: extract(data))
        except Exception as exc:  # one bad segment fails alone; the share check decides
            if not isinstance(exc, ServiceError):
                log.exception("extraction crashed: %s segment %s", distillation_id, seg.idx)
            if domain:  # a focused look that failed changes nothing
                async with self.sessions() as s, s.begin():
                    s.add_all(calls)
                return
            async with self.sessions() as s, s.begin():
                await s.execute(update(DistillSegmentRow).where(
                    DistillSegmentRow.id == segment_id).values(
                    status="failed", runs=DistillSegmentRow.runs + 1, error=str(exc)[:200]))
            return
        async with self.sessions() as s, s.begin():
            if domain:  # keep what the first look found; a quote filed elsewhere (a value, or
                # another domain) may also show this domain, but not twice for the same one
                seen = set((await s.scalars(select(DistillEvidenceRow.offset).where(
                    DistillEvidenceRow.segment_id == segment_id,
                    DistillEvidenceRow.facet.in_(facets)))).all())
            else:
                await s.execute(delete(DistillEvidenceRow).where(
                    DistillEvidenceRow.segment_id == segment_id))
                seen = set()
            for item in result.value.evidence:
                at = tx.locate(item.quote, segment)
                if at == -1 or at in seen:  # not verbatim in this segment: dropped
                    continue
                if source.kind == "chat" and not ev.owner_quote(segment, at, item.quote):
                    continue  # the persona's own lines never count as evidence of change
                facet = item.facet if item.facet in TRAIT_IDS else None
                if domain and (item.category != "trait" or facet not in facets):
                    continue  # a focused look only adds its own domain
                seen.add(at)
                s.add(DistillEvidenceRow(
                    distillation_id=distillation_id, segment_id=segment_id,
                    quote=tx.mask(item.quote.strip()), offset=at, note=tx.mask(item.note),
                    construct=item.category, facet=facet if item.category == "trait" else None))
            s.add_all(calls)
            if not domain:
                await s.execute(update(DistillSegmentRow).where(
                    DistillSegmentRow.id == segment_id).values(
                    status="done", runs=DistillSegmentRow.runs + 1, error=None))

    async def _sweep(self, ns: str, distillation_id: str) -> None:
        """A second look, one domain at a time, for Big Five domains with few trait quotes:
        the segments that mention the character most, asked only about that domain."""
        async with self.sessions() as s:
            row = await s.get(DistillationRow, (ns, distillation_id))
            facets = (await s.scalars(select(DistillEvidenceRow.facet).where(
                DistillEvidenceRow.distillation_id == distillation_id,
                DistillEvidenceRow.construct == "trait"))).all()
            texts = [(sid, t) for sid, t in await self._segment_texts(s, distillation_id)]
            done = set((await s.scalars(select(DistillSegmentRow.id).where(
                DistillSegmentRow.distillation_id == distillation_id,
                DistillSegmentRow.status == "done"))).all())
        counts = {d: sum(1 for f in facets if f and f.split(".")[1] == d) for d in DOMAINS}
        thin = [d for d, n in counts.items() if n < SWEEP_MIN]
        if not thin:
            return
        names = tx.names(row.target)
        ranked = sorted(((tx.mentions(t, names), sid) for sid, t in texts if sid in done),
                        reverse=True)[:SWEEP_SEGMENTS]
        await asyncio.gather(*(self._extract(ns, distillation_id, sid, row.target, row.language,
                                             domain=d) for d in thin for _, sid in ranked))

    async def _stop_if_too_many_failed(self, distillation_id: str) -> bool:
        async with self.sessions() as s:
            relevant, failed = (await s.execute(select(
                func.count(), func.count().filter(DistillSegmentRow.status == "failed")).where(
                DistillSegmentRow.distillation_id == distillation_id,
                DistillSegmentRow.relevant))).one()
        if failed and failed > relevant * MAX_FAILED_SHARE:
            async with self.sessions() as s:
                errors = (await s.scalars(select(DistillSegmentRow.error).where(
                    DistillSegmentRow.distillation_id == distillation_id,
                    DistillSegmentRow.status == "failed"))).all()
            refused = next((e for e in errors if e and e.startswith(REFUSALS)), None)
            await self._set(distillation_id, stage="failed", error=refused or (
                f"{failed}/{relevant} 个片段抽取失败；再次开始会只重试失败的片段"))
            return True
        return False

    async def _induce_all(self, ns: str, distillation_id: str) -> None:
        await self._set(distillation_id, step="inducing")
        async with self.sessions() as s, s.begin():
            row = await s.get(DistillationRow, (ns, distillation_id))
            evidence = (await s.scalars(select(DistillEvidenceRow).where(
                DistillEvidenceRow.distillation_id == distillation_id)
                .order_by(DistillEvidenceRow.id))).all()
            await s.execute(delete(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id))
        groups: dict[str, list[DistillEvidenceRow]] = defaultdict(list)
        for e in evidence:  # traits by Big Five domain; a trait quote without a facet on its own
            domain = e.facet.split(".")[1] if e.construct == "trait" and e.facet else ""
            groups[f"trait.{domain}" if domain else e.construct].append(e)
        results = await asyncio.gather(*(
            self._induce(ns, distillation_id, row.target, row.language, key.split(".")[0], items,
                         [i for i in KNOWN_IDS["trait"] if i.startswith(key + ".")]
                         if "." in key else None, key)
            for key, items in groups.items()))
        used: set[str] = set()
        async with self.sessions() as s, s.begin():
            for key, induced in zip(groups, results, strict=True):
                category = key.split(".")[0]
                for entry in induced:
                    entry_id = self._entry_id(LAYER_OF[category], entry.construct_id, used)
                    s.add(DistillEntryRow(distillation_id=distillation_id, entry_id=entry_id,
                                          payload=self._payload(category, entry_id, entry)))

    async def _induce(self, ns: str, distillation_id: str, target: dict, language: str,
                      category: str, items: list[DistillEvidenceRow],
                      known_ids: list[str] | None = None, label: str = "") -> list[InducedEntry]:
        sample = _sample(items, MAX_EVIDENCE_PER_CATEGORY)
        allowed = {_ev(e.id) for e in sample}
        data = InduceInput(target=target, language=language, layer=LAYER_OF[category],
                           construct=category, known_ids=known_ids or KNOWN_IDS[category],
                           evidence=[{"id": _ev(e.id), "quote": e.quote, "note": e.note}
                                     for e in sample])
        calls: list[ModelCallRow] = []
        result = await self.runner._attempts(
            ns, f"distill:{distillation_id}/{label or category}", "distill_induce", calls,
            lambda _: self.model.induce_beliefs(data))
        async with self.sessions() as s, s.begin():
            s.add_all(calls)
        kept = []
        for entry in result.value.entries:
            cid = _construct_id(category, entry.construct_id)
            refs = [r for r in dict.fromkeys(entry.evidence) if r in allowed]
            if cid and refs:  # an entry without checked evidence is not kept
                kept.append(entry.model_copy(update={"construct_id": cid, "evidence": refs}))
        return kept

    async def _revise_all(self, ns: str, distillation_id: str) -> None:
        """Evolution: each category with evidence revises its entries in the base version."""
        await self._set(distillation_id, step="inducing")
        async with self.sessions() as s, s.begin():
            row = await s.get(DistillationRow, (ns, distillation_id))
            evidence = (await s.scalars(select(DistillEvidenceRow).where(
                DistillEvidenceRow.distillation_id == distillation_id)
                .order_by(DistillEvidenceRow.id))).all()
            kinds = await self._evidence_kinds(s, [e.segment_id for e in evidence])
            base = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.base_version_id)
                .order_by(BeliefEntryRow.id))).all())
            await s.execute(delete(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id))
        by_category: dict[str, list[DistillEvidenceRow]] = defaultdict(list)
        for e in evidence:
            by_category[e.construct].append(e)
        source_word = {"chat": "主人的话", "monitoring": "反复出现的偏差", "material": "新素材"}
        results = await asyncio.gather(*(
            self._revise(ns, distillation_id, row, category, items,
                         [b for b in base if b["construct"] == category],
                         {i.id: source_word[kinds[i.segment_id]] for i in items})
            for category, items in by_category.items()))
        by_id = {b["entry_id"]: b for b in base}
        used, revised = set(by_id), set()
        l3_left = ev.L3_ADDITIONS
        async with self.sessions() as s, s.begin():
            for category, (revisions, allowed) in zip(by_category, results, strict=True):
                layer = LAYER_OF[category]
                for r in revisions:
                    before = by_id.get(r.entry_id or "") if r.action == "revise" else None
                    if r.action == "revise" and (before is None or before["construct"] != category
                                                 or before["entry_id"] in revised):
                        continue  # unknown entry, another category's, or revised already
                    if r.action == "add":
                        cid = _construct_id(category, r.construct_id)
                        if cid is None:
                            continue
                        r = r.model_copy(update={"construct_id": cid})
                        entry_id = self._entry_id(layer, cid, used)
                    else:
                        entry_id = before["entry_id"]
                    payload = ev.apply(r, layer, category, before, entry_id, allowed, l3_left)
                    if payload is None:
                        continue
                    if r.action == "revise":
                        revised.add(entry_id)
                        l3_left -= layer == "L3"
                    s.add(DistillEntryRow(distillation_id=distillation_id, entry_id=entry_id,
                                          payload=payload, base_payload=before,
                                          reason=tx.mask(r.reason)))

    async def _revise(self, ns: str, distillation_id: str, row: DistillationRow, category: str,
                      items: list[DistillEvidenceRow], entries: list[dict[str, Any]],
                      sources: dict[int, str]):
        sample = _sample(items, MAX_EVIDENCE_PER_CATEGORY)
        layer = LAYER_OF[category]
        data = ReviseInput(
            target=row.target, language=row.language, layer=layer, construct=category,
            known_ids=KNOWN_IDS[category], limits=ev.limits(layer),
            entries=[{"entry_id": e["entry_id"], "construct_id": e["construct_id"],
                      "text": e.get("text") or "", "value": e.get("value"),
                      "scope": e.get("scope") or []}
                     for e in entries if e.get("epistemic_status") == "supported"],
            evidence=[{"id": _ev(e.id), "quote": e.quote, "note": e.note,
                       "source": sources[e.id]} for e in sample])
        calls: list[ModelCallRow] = []
        result = await self.runner._attempts(
            ns, f"distill:{distillation_id}/{category}", "evolve_revise", calls,
            lambda _: self.model.revise_beliefs(data))
        async with self.sessions() as s, s.begin():
            s.add_all(calls)
        return result.value.revisions, {_ev(e.id) for e in sample}

    @staticmethod
    def _entry_id(layer: str, construct_id: str, used: set[str]) -> str:
        base = f"{layer}_{construct_id.replace('.', '_')}"[:110]
        entry_id, n = base, 2
        while entry_id in used:
            entry_id, n = f"{base}_{n}", n + 1
        used.add(entry_id)
        return entry_id

    @staticmethod
    def _payload(category: str, entry_id: str, entry: InducedEntry) -> dict[str, Any]:
        return c.BeliefEntry.model_validate({
            "entry_id": entry_id, "layer": LAYER_OF[category], "construct": category,
            "construct_id": entry.construct_id, "value": _value(entry.value),
            "text": tx.mask(entry.text), "source": "inference", "evidence_refs": entry.evidence,
            "confidence": round(entry.confidence, 2), "scope": entry.scope,
        }).model_dump(mode="json")

    async def _review(self, ns: str, distillation_id: str) -> None:
        """Consistency review; a failed review leaves the draft as it is for the owner."""
        await self._set(distillation_id, step="reviewing")
        async with self.sessions() as s:
            row = await s.get(DistillationRow, (ns, distillation_id))
            entries = (await s.scalars(select(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id))).all()
            quotes = await self._quotes(s, [r for e in entries
                                            for r in e.payload["evidence_refs"]])
        if not entries:
            return
        evolving = row.kind == "evolution"
        data = ReviewInput(target=row.target, language=row.language, entries=[
            {"entry_id": e.entry_id, "construct_id": e.payload["construct_id"],
             "text": e.payload["text"], "scope": e.payload["scope"],
             "confidence": e.payload["confidence"],
             "quotes": [quotes[r] for r in self._new_refs(e)[:8] if r in quotes]}
            | ({"before": (e.base_payload or {}).get("text"), "reason": e.reason}
               if evolving else {})
            for e in entries])
        review = self.model.review_changes if evolving else self.model.review_beliefs
        calls: list[ModelCallRow] = []
        try:
            result = await self.runner._attempts(
                ns, f"distill:{distillation_id}/review",
                "evolve_review" if evolving else "distill_review", calls,
                lambda _: review(data))
        except ServiceError as exc:
            await self._set(distillation_id, error=f"复核未完成，请直接审阅：{exc.message}"[:300])
            return
        notes: dict[str, list[str]] = defaultdict(list)
        for n in result.value.notes:
            tip = f"。建议：{n.suggestion}" if n.suggestion else ""
            notes[n.entry_id].append(n.issue.rstrip("。") + tip)
        async with self.sessions() as s, s.begin():
            s.add_all(calls)
            for entry_id, items in notes.items():
                await s.execute(update(DistillEntryRow).where(
                    DistillEntryRow.distillation_id == distillation_id,
                    DistillEntryRow.entry_id == entry_id).values(review_note="\n".join(items)))

    async def _set(self, distillation_id: str, **values: Any) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(update(DistillationRow).where(
                DistillationRow.distillation_id == distillation_id).values(
                **values, updated_at=now()))

    # ---- review by the owner ---------------------------------------------------------------------
    async def entries(self, ns: str, distillation_id: str) -> list[d.ReviewedEntry]:
        async with self.sessions() as s:
            await self._row(s, ns, distillation_id)
            rows = (await s.scalars(select(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id)
                .order_by(DistillEntryRow.id))).all()
            quotes = await self._quotes(s, [r for e in rows for r in e.payload["evidence_refs"]])
        return [self._reviewed(e, quotes) for e in rows]

    async def try_entry(self, ns: str, distillation_id: str, entry_id: str) -> d.TryResult:
        """Base version and base-with-this-change answer the same situation (4 model calls)."""
        async with self.sessions() as s:
            row = await self._reviewable(s, ns, distillation_id)
            if row.kind != "evolution":
                raise ServiceError(409, "not_an_evolution", distillation_id)
            entry = await self._entry(s, distillation_id, entry_id)
            base = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.base_version_id)
                .order_by(BeliefEntryRow.id))).all())
        changed = [entry.payload if b["entry_id"] == entry_id else b for b in base]
        if entry.base_payload is None:
            changed.append(entry.payload)
        scope = "、".join(entry.payload.get("scope") or []) or "和我相处"
        situation = f"你在主人的电脑桌面上陪主人。主人问你：「{scope}，你一般会怎么想、怎么做？」"
        task = "用你平时说话的方式，回答主人。"
        calls: list[ModelCallRow] = []
        try:
            before, after = await asyncio.gather(*(
                self.runner.simulate(ns, f"distill:{distillation_id}/try/{entry_id}/{side}",
                                     beliefs, situation, task, "zh", TRY_LENGTH, calls)
                for side, beliefs in (("before", base), ("after", changed))))
        finally:
            async with self.sessions() as s, s.begin():
                s.add_all(calls)
        return d.TryResult(situation=situation, before=before, after=after)

    async def evidence(self, ns: str, distillation_id: str, evidence_id: str) -> d.Evidence:
        _, _, number = evidence_id.partition(":")
        async with self.sessions() as s:
            await self._row(s, ns, distillation_id)
            ev = await s.get(DistillEvidenceRow, int(number)) if number.isdigit() else None
            if ev is None or ev.distillation_id != distillation_id:
                raise ServiceError(404, "evidence_not_found", evidence_id)
            seg = await s.get(DistillSegmentRow, ev.segment_id)
            source = await s.get(DistillSourceRow, seg.source_id)
        return d.Evidence(evidence_id=_ev(ev.id), quote=ev.quote, note=ev.note,
                          construct=ev.construct, source=source.name,
                          segment_text=source.text[seg.start:seg.end], quote_offset=ev.offset)

    async def update_entry(self, ns: str, distillation_id: str, entry_id: str,
                           body: d.EntryUpdate) -> d.ReviewedEntry:
        async with self.sessions() as s, s.begin():
            await self._reviewable(s, ns, distillation_id)
            row = await self._entry(s, distillation_id, entry_id)
            payload = dict(row.payload)
            if body.text is not None and body.text != payload["text"]:
                if row.original_text is None and row.status != "added":
                    row.original_text = payload["text"]
                payload["text"] = tx.mask(body.text)
                row.status = "added" if row.status == "added" else "edited"
            if body.scope is not None:
                payload["scope"] = body.scope
            if body.status is not None and row.status != "added":
                row.status = "edited" if body.status == "accepted" and row.original_text \
                    else body.status
            row.payload = c.BeliefEntry.model_validate(payload).model_dump(mode="json")
            row.updated_at = now()
            quotes = await self._quotes(s, row.payload["evidence_refs"])
        return self._reviewed(row, quotes)

    async def add_entry(self, ns: str, distillation_id: str,
                        body: d.EntryAdd) -> d.ReviewedEntry:
        async with self.sessions() as s, s.begin():
            await self._reviewable(s, ns, distillation_id)
            used = set((await s.scalars(select(DistillEntryRow.entry_id).where(
                DistillEntryRow.distillation_id == distillation_id))).all())
            entry_id = self._entry_id(body.layer, body.construct_id, used)
            payload = c.BeliefEntry.model_validate({
                "entry_id": entry_id, "layer": body.layer, "construct": body.category,
                "construct_id": body.construct_id, "text": tx.mask(body.text),
                "source": "configured", "scope": body.scope}).model_dump(mode="json")
            row = DistillEntryRow(distillation_id=distillation_id, entry_id=entry_id,
                                  payload=payload, status="added")
            s.add(row)
        return self._reviewed(row, {})

    async def commit(self, ns: str, distillation_id: str) -> d.CommitResult:
        async with self.sessions() as s:
            row = await self._reviewable(s, ns, distillation_id)
            rows = (await s.scalars(select(DistillEntryRow).where(
                DistillEntryRow.distillation_id == distillation_id,
                DistillEntryRow.status.in_(("accepted", "edited", "added")))
                .order_by(DistillEntryRow.id))).all()
            if not rows:
                raise ServiceError(422, "nothing_accepted", "accept or add at least one entry")
            refs = [r for e in rows for r in e.payload["evidence_refs"]]
            quotes = await self._quotes(s, refs)
        entries = [c.BeliefEntry.model_validate(e.payload) for e in rows]
        material = [(ref, quotes[ref]) for ref in dict.fromkeys(refs) if ref in quotes]
        if row.kind == "evolution":
            return await self._commit_evolution(ns, row, entries, material)
        result = await self.store.write_initial(ns, row.persona_id, entries, material)
        await self._set(distillation_id, stage="committed", error=None)
        return d.CommitResult(persona_id=row.persona_id, version_id=result.version_id,
                              entries=len(entries), unknown_constructs=result.unknown_constructs)

    async def _commit_evolution(self, ns: str, row: DistillationRow,
                                changes: list[c.BeliefEntry],
                                material: list[tuple[str, str]]) -> d.CommitResult:
        """The base version with the accepted revisions applied, as the next version."""
        async with self.sessions() as s:
            base = list((await s.scalars(select(BeliefEntryRow.payload).where(
                BeliefEntryRow.version_id == row.base_version_id)
                .order_by(BeliefEntryRow.id))).all())
        new = {e.entry_id: e for e in changes}
        entries = [new.pop(b["entry_id"], None) or c.BeliefEntry.model_validate(b)
                   for b in base] + list(new.values())
        version = await self.store.write_next(ns, row.persona_id, row.base_version_id, entries,
                                              material, "evolution")
        await self._set(row.distillation_id, stage="committed", error=None)
        return d.CommitResult(persona_id=row.persona_id, version_id=version.version_id,
                              entries=len(changes), k=version.k,
                              unknown_constructs=[e.construct_id for e in version.entries
                                                  if e.epistemic_status == "unknown"])

    # ---- reading ---------------------------------------------------------------------------------
    async def list_all(self, ns: str, kind: d.Kind = "initial") -> list[d.Distillation]:
        async with self.sessions() as s:
            ids = (await s.scalars(select(DistillationRow.distillation_id).where(
                DistillationRow.namespace == ns, DistillationRow.kind == kind)
                .order_by(DistillationRow.created_at.desc()))).all()
        return [await self.get(ns, i) for i in ids]

    async def get(self, ns: str, distillation_id: str) -> d.Distillation:
        async with self.sessions() as s:
            row = await self._row(s, ns, distillation_id)
            sources = (await s.execute(select(
                DistillSourceRow.id, DistillSourceRow.name, func.length(DistillSourceRow.text),
                DistillSourceRow.kind)
                .where(DistillSourceRow.distillation_id == distillation_id)
                .order_by(DistillSourceRow.id))).all()
            seg_counts = dict((await s.execute(select(
                DistillSegmentRow.source_id, func.count()).where(
                DistillSegmentRow.distillation_id == distillation_id)
                .group_by(DistillSegmentRow.source_id))).all())
            total, relevant, extracted, failed = (await s.execute(select(
                func.count(), func.count().filter(DistillSegmentRow.relevant),
                func.count().filter(DistillSegmentRow.status == "done"),
                func.count().filter(DistillSegmentRow.status == "failed")).where(
                DistillSegmentRow.distillation_id == distillation_id))).one()
            evidence = await s.scalar(select(func.count()).select_from(DistillEvidenceRow)
                                      .where(DistillEvidenceRow.distillation_id ==
                                             distillation_id))
            entries = await s.scalar(select(func.count()).select_from(DistillEntryRow)
                                     .where(DistillEntryRow.distillation_id == distillation_id))
            calls, prompt, completion = (await s.execute(select(
                func.count(), func.coalesce(func.sum(ModelCallRow.prompt_tokens), 0),
                func.coalesce(func.sum(ModelCallRow.completion_tokens), 0)).where(
                ModelCallRow.namespace == ns,
                ModelCallRow.action_id.startswith(f"distill:{distillation_id}/")))).one()
        return d.Distillation(
            distillation_id=row.distillation_id, title=row.title, kind=row.kind,
            base_version_id=row.base_version_id, stage=row.stage,
            step=row.step, target=row.target, persona_id=row.persona_id,
            language=row.language, error=row.error,
            sources=[d.SourceInfo(source_id=i, name=n, chars=k, segments=seg_counts.get(i, 0),
                                  kind=kind) for i, n, k, kind in sources],
            progress=d.Progress(segments=total, relevant=relevant, extracted=extracted,
                                failed=failed, evidence=evidence, entries=entries),
            usage=d.Usage(model_calls=calls, prompt_tokens=prompt, completion_tokens=completion),
            created_at=row.created_at, updated_at=row.updated_at)

    # ---- helpers ---------------------------------------------------------------------------------
    @staticmethod
    async def _row(s, ns: str, distillation_id: str) -> DistillationRow:
        row = await s.get(DistillationRow, (ns, distillation_id))
        if row is None:
            raise ServiceError(404, "distillation_not_found", distillation_id)
        return row

    async def _reviewable(self, s, ns: str, distillation_id: str) -> DistillationRow:
        row = await self._row(s, ns, distillation_id)
        if row.stage not in ("review", "committed"):
            raise ServiceError(409, "distillation_not_ready",
                               f"stage is {row.stage}; entries can be reviewed once it is review")
        return row

    @staticmethod
    async def _entry(s, distillation_id: str, entry_id: str) -> DistillEntryRow:
        row = await s.scalar(select(DistillEntryRow).where(
            DistillEntryRow.distillation_id == distillation_id,
            DistillEntryRow.entry_id == entry_id))
        if row is None:
            raise ServiceError(404, "entry_not_found", entry_id)
        return row

    async def _active(self, s, ns: str, persona_id: str):
        persona = await s.get(PersonaRow, (ns, persona_id))
        if persona is None:
            raise ServiceError(404, "persona_not_found", persona_id)
        version = await s.get(BeliefVersionRow, persona.active_version_id)
        entries = list((await s.scalars(select(BeliefEntryRow.payload).where(
            BeliefEntryRow.version_id == version.version_id))).all())
        return version, entries

    @staticmethod
    async def _segment_kinds(s, distillation_id: str) -> dict[int, str]:
        return dict((await s.execute(select(DistillSegmentRow.id, DistillSourceRow.kind).join(
            DistillSourceRow, DistillSourceRow.id == DistillSegmentRow.source_id).where(
            DistillSegmentRow.distillation_id == distillation_id))).all())

    @staticmethod
    async def _evidence_kinds(s, segment_ids: list[int]) -> dict[int, str]:
        if not segment_ids:
            return {}
        return dict((await s.execute(select(DistillSegmentRow.id, DistillSourceRow.kind).join(
            DistillSourceRow, DistillSourceRow.id == DistillSegmentRow.source_id).where(
            DistillSegmentRow.id.in_(set(segment_ids))))).all())

    @staticmethod
    async def _segment_texts(s, distillation_id: str) -> list[tuple[int, str]]:
        sources = {r.id: r.text for r in (await s.scalars(select(DistillSourceRow).where(
            DistillSourceRow.distillation_id == distillation_id))).all()}
        segments = (await s.scalars(select(DistillSegmentRow).where(
            DistillSegmentRow.distillation_id == distillation_id)
            .order_by(DistillSegmentRow.idx))).all()
        return [(g.id, sources[g.source_id][g.start:g.end]) for g in segments]

    @staticmethod
    async def _quotes(s, refs: list[str]) -> dict[str, str]:
        ids = {int(r[3:]) for r in refs if r.startswith("ev:") and r[3:].isdigit()}
        if not ids:
            return {}
        rows = (await s.execute(select(DistillEvidenceRow.id, DistillEvidenceRow.quote).where(
            DistillEvidenceRow.id.in_(ids)))).all()
        return {_ev(i): q for i, q in rows}

    @staticmethod
    def _new_refs(row: DistillEntryRow) -> list[str]:
        """Evidence this distillation found for the entry (an evolution's revision also keeps
        the base entry's references, which are not what the change rests on)."""
        old = set((row.base_payload or {}).get("evidence_refs") or [])
        return [r for r in row.payload["evidence_refs"] if r not in old]

    @classmethod
    def _reviewed(cls, row: DistillEntryRow, quotes: dict[str, str]) -> d.ReviewedEntry:
        return d.ReviewedEntry(
            entry=c.BeliefEntry.model_validate(row.payload), status=row.status,
            original_text=row.original_text, review_note=row.review_note,
            evidence=[d.EvidenceBrief(evidence_id=r, quote=quotes[r])
                      for r in cls._new_refs(row) if r in quotes],
            before=c.BeliefEntry.model_validate(row.base_payload) if row.base_payload else None,
            reason=row.reason)
