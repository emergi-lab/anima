"""Evolution as distillation (Anima A4): what the persona lived through since its active version
becomes sources, and each category's entries are revised instead of induced anew.

Sources: chat transcripts since the active version (only the owner's lines are quotable — the
persona's own lines come from the current beliefs and would only confirm them) and monitoring
deviations seen in at least two sessions; the owner may add material. Revisions are held to small
steps: values move at most TERM_CAP per evolution, narrative identity (L3) only gains appended,
conditional sentences (at most L3_ADDITIONS), and every change cites checked evidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select

from persona_os.application.evolution import (
    L3_ADDITIONS,
    MIN_MONITORED_EPISODES,
    MIN_STEP,
    TERM_CAP,
)
from persona_os.chat.service import OWNER, PREFIX
from persona_os.distill import text as tx
from persona_os.distill.ports import Revision
from persona_os.domain import contract as c
from persona_os.infra.db import (
    EpisodePersonaRow,
    EpisodeRow,
    MonitoringRow,
    ObservationRow,
)

OWNER_TAG = "主人："  # owner lines in a transcript start with this; nothing else is quotable
DIRECTION = {"above": "比平时更强", "below": "比平时更弱", "contrary": "和平时相反"}
FIELDS = {"L1": ("baseline",), "L2": ("baseline", "satisfaction", "frustration"), "L3": ()}
__all__ = ["L3_ADDITIONS", "apply", "gather", "limits", "owner_quote"]


def owner_quote(segment: str, at: int, quote: str) -> bool:
    """The located quote lies inside one owner line, after its tag."""
    start = segment.rfind("\n", 0, at) + 1
    end = segment.find("\n", at)
    end = len(segment) if end == -1 else end
    return segment.startswith(OWNER_TAG, start) and at >= start + len(OWNER_TAG) \
        and at + len(quote.strip()) <= end + 2  # locate() tolerates whitespace differences


async def gather(s, ns: str, persona_id: str, persona_name: str, since: datetime,
                 entries: list[dict[str, Any]]) -> tuple[list[tuple[str, str, str]], dict]:
    """Sources (name, text, kind) and counts of what an evolution would read."""
    episodes = (await s.scalars(select(EpisodeRow).join(
        EpisodePersonaRow, (EpisodePersonaRow.namespace == EpisodeRow.namespace)
        & (EpisodePersonaRow.episode_id == EpisodeRow.episode_id)).where(
        EpisodeRow.namespace == ns, EpisodePersonaRow.persona_id == persona_id,
        EpisodeRow.episode_id.startswith(PREFIX), EpisodeRow.created_at >= since)
        .order_by(EpisodeRow.created_at))).all()
    lines: dict[str, list[dict]] = defaultdict(list)
    if episodes:
        for o in (await s.scalars(select(ObservationRow).where(
                ObservationRow.namespace == ns,
                ObservationRow.episode_id.in_([e.episode_id for e in episodes]))
                .order_by(ObservationRow.seq))).all():
            lines[o.episode_id].append(o.payload)
    sources, owner, own = [], 0, 0
    for e in episodes:
        said = lines[e.episode_id]
        mine = sum(1 for p in said if p.get("actor") == OWNER)
        if not mine:
            continue
        owner += mine
        own += len(said) - mine
        text = "\n".join((OWNER_TAG if p.get("actor") == OWNER else f"{persona_name}：")
                         + " ".join(str(p["content"]).split()) for p in said)
        sources.append((f"聊天 {e.created_at:%Y-%m-%d %H:%M}", text, "chat"))

    texts = {x["entry_id"]: x.get("text") or x["construct_id"] for x in entries}
    groups: dict[tuple[str, str], list[MonitoringRow]] = defaultdict(list)
    for m in (await s.scalars(select(MonitoringRow).where(
            MonitoringRow.namespace == ns, MonitoringRow.persona_id == persona_id,
            MonitoringRow.created_at >= since).order_by(MonitoringRow.id))).all():
        if m.entry_id in texts:
            groups[(m.entry_id, m.direction)].append(m)
    repeated = [g for g in groups.values()
                if len({m.episode_id for m in g}) >= MIN_MONITORED_EPISODES]
    if repeated:
        sources.append(("反复出现的偏差", "\n".join(
            f"关于「{texts[m.entry_id][:40]}」（{DIRECTION.get(m.direction, m.direction)}）："
            + " ".join(m.note.split()) for g in repeated for m in g), "monitoring"))
    return sources, {"sessions": sum(1 for _, _, k in sources if k == "chat"),
                     "owner_lines": owner, "persona_lines": own, "deviations": len(repeated)}


def limits(layer: str) -> dict[str, Any]:
    return {"text": "append" if layer == "L3" else "rewrite",
            "value_step": TERM_CAP.get(layer, 0.0), "fields": list(FIELDS[layer]),
            "allow_add": layer != "L3"}


def apply(revision: Revision, layer: str, category: str, base: dict[str, Any] | None,
          entry_id: str, allowed: set[str], l3_left: int) -> dict[str, Any] | None:
    """The revised (or new) entry payload within the limits, or None when nothing is left."""
    refs = [r for r in dict.fromkeys(revision.evidence) if r in allowed]
    if not refs:
        return None
    text = tx.mask(revision.text.strip())
    if base is None:  # add
        if layer == "L3" or not revision.construct_id:
            return None
        cap = TERM_CAP.get(layer, 0.0)
        value = {k: round(min(max(float(v), 0.0), 1.0), 2)
                 for k, v in (revision.value or {}).items()
                 if k in FIELDS[layer] and isinstance(v, int | float)}
        return c.BeliefEntry.model_validate({
            "entry_id": entry_id, "layer": layer, "construct": category,
            "construct_id": revision.construct_id, "text": text, "source": "inference",
            "value": value | {"scale": "0-1"} if value and cap else None,
            "evidence_refs": refs, "confidence": 0.5, "scope": revision.scope,
        }).model_dump(mode="json")
    if not base.get("modifiable", True) or base.get("epistemic_status") != "supported":
        return None
    new = dict(base)
    if layer == "L3":
        old = (base.get("text") or "").rstrip()
        if text.startswith(old):  # the model wrote the whole entry again: keep only what's new
            text = text[len(old):].strip()
        if l3_left <= 0 or not text or text in old:
            return None
        new["text"] = old + text
    else:
        new["text"] = text
        value = dict(base.get("value") or {})
        cap = TERM_CAP[layer]
        for k, v in (revision.value or {}).items():
            old = value.get(k)
            if k in FIELDS[layer] and isinstance(v, int | float) and isinstance(old, int | float):
                moved = round(min(max(float(v), old - cap), old + cap), 2)
                if abs(moved - old) >= MIN_STEP:
                    value[k] = moved
        new["value"] = value or base.get("value")
    if revision.scope and layer != "L3":
        new["scope"] = revision.scope
    if new["text"] == base.get("text") and new.get("value") == base.get("value") \
            and new.get("scope") == base.get("scope"):
        return None
    new["evidence_refs"] = (list(base.get("evidence_refs") or []) + refs)[-200:]
    return c.BeliefEntry.model_validate(new).model_dump(mode="json")
