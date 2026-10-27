"""Pure text handling for distillation: slicing, speaker candidates, relevance, quote checks and
identity masking. No database and no model calls."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

SEGMENT_CHARS = 3000
CONTEXT_CHARS = 500  # tail of the previous segment shown with each one (not quotable)

# "名字：台词" / "Name: line" at the start of a line; names stay short so prose rarely matches.
SPEAKER = re.compile(r"^[ \t　]*[【\[(（]?([^\s：:，,。.!！?？「」『』“”\"'()（）【】\[\]]{1,12})"
                     r"[】\])）]?[ \t　]*[：:]", re.M)
IDENTITY = [re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), re.compile(r"(?<!\d)\d{8,}(?!\d)")]
MASK = "［已遮蔽］"
SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int


def split(text: str, size: int = SEGMENT_CHARS) -> list[Span]:
    """Cut at line breaks into spans of at most `size` characters; a longer line is cut hard.
    Spans cover the whole text, so offsets map straight back to the source."""
    spans: list[Span] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        spans.append(Span(start, end))
        start = end
    return spans


def speakers(texts: list[str], limit: int = 30) -> list[tuple[str, int]]:
    """Most frequent speaker labels across all sources (dialogue scripts, chat exports)."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(m.group(1) for m in SPEAKER.finditer(text))
    return [(name, n) for name, n in counts.most_common(limit) if n >= 2]


def names(target: dict) -> list[str]:
    return [n for n in [target["name"], *target.get("aliases", [])] if n.strip()]


def mentions(text: str, target_names: list[str]) -> int:
    return sum(text.count(n) for n in target_names)


def locate(quote: str, segment: str) -> int:
    """Offset of a quote in its segment, or -1. Whitespace differences are tolerated because
    models often re-flow line breaks; anything else must match character for character."""
    quote = quote.strip()
    if not quote:
        return -1
    at = segment.find(quote)
    if at != -1:
        return at
    pattern = r"\s*".join(re.escape(part) for part in SPACE.split(quote) if part)
    found = re.search(pattern, segment)
    return found.start() if found else -1


def mask(text: str) -> str:
    """Hide e-mail addresses and long digit runs; persona storage refuses identity data."""
    for pattern in IDENTITY:
        text = pattern.sub(MASK, text)
    return text
