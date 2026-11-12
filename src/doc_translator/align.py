"""Carry inline styles (bold, italic, links...) over to the translated text.

The paragraph is translated without markup. Each styled segment is also
translated on its own, then located inside the paragraph translation with a
fuzzy match, since word order and inflection may change.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from .analysis.model import Block

MIN_SIMILARITY = 0.6
WORD = re.compile(r"\S+")
EDGE_PUNCTUATION = ".,;:!?\"'()[]«»“”"


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    color: int
    bold: bool
    italic: bool
    link: str | None = None
    underline: bool = False
    superscript: bool = False
    small_caps: float = 0.0


@dataclass
class Run:
    text: str
    style: Style


def paragraph_runs(paragraph: Block) -> list[Run]:
    """Merge consecutive spans with the same style; lines join with a space."""
    runs: list[Run] = []
    for index, line in enumerate(paragraph.lines):
        for position, span in enumerate(line.spans):
            text = span.text
            if index > 0 and position == 0:
                text = " " + text.lstrip()
            style = Style(
                span.font,
                round(span.size, 1),
                span.color,
                span.bold,
                span.italic,
                span.link,
                span.underline,
                span.superscript,
                round(span.small_caps, 2),
            )
            if runs and runs[-1].style == style:
                runs[-1].text += text
            else:
                runs.append(Run(text, style))
    return runs


def base_style(runs: list[Run]) -> Style:
    weights: Counter[Style] = Counter()
    for run in runs:
        weights[run.style] += len(run.text.strip())
    return weights.most_common(1)[0][0]


def styled_segments(runs: list[Run], base: Style) -> list[Run]:
    return [run for run in runs if run.style != base and run.text.strip()]


def realign(
    translation: str, base: Style, segments: list[Run], segment_translations: list[str]
) -> list[Run]:
    """Split `translation` into runs, applying each segment's style where found."""
    words = [(m.start(), m.end()) for m in WORD.finditer(translation)]
    taken: list[tuple[int, int]] = []
    placed: list[tuple[int, int, Style]] = []

    for segment, segment_translation in zip(segments, segment_translations):
        candidates = [segment.text.strip(), segment_translation.strip()]
        span = _locate(translation, words, candidates, taken)
        if span:
            taken.append(span)
            placed.append((*span, segment.style))

    runs: list[Run] = []
    cursor = 0
    for start, end, style in sorted(placed, key=lambda item: item[0]):
        if start > cursor:
            runs.append(Run(translation[cursor:start], base))
        runs.append(Run(translation[start:end], style))
        cursor = end
    if cursor < len(translation):
        runs.append(Run(translation[cursor:], base))
    return runs


def _locate(
    text: str, words: list[tuple[int, int]], candidates: list[str], taken: list[tuple[int, int]]
) -> tuple[int, int] | None:
    # 1. Exact (case-insensitive) match: numbers, names, emails, URLs...
    for candidate in candidates:
        start = text.lower().find(candidate.lower())
        while start != -1:
            span = (start, start + len(candidate))
            if not _overlaps(span, taken):
                return span
            start = text.lower().find(candidate.lower(), start + 1)

    # 2. Fuzzy match over windows of words around the candidate's length.
    best: tuple[float, tuple[int, int]] | None = None
    for candidate in candidates:
        target = _normalize(candidate)
        size = len(candidate.split())
        for length in range(max(1, size - 2), size + 3):
            for first in range(len(words) - length + 1):
                span = _trim(text, words[first][0], words[first + length - 1][1])
                if _overlaps(span, taken):
                    continue
                score = SequenceMatcher(None, _normalize(text[span[0] : span[1]]), target).ratio()
                if best is None or score > best[0]:
                    best = (score, span)
    if best and best[0] >= MIN_SIMILARITY:
        return best[1]
    return None


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop punctuation at the window edges ("desempenho," -> "desempenho")."""
    while start < end and text[start] in EDGE_PUNCTUATION:
        start += 1
    while end > start and text[end - 1] in EDGE_PUNCTUATION:
        end -= 1
    return start, end


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(sorted(re.findall(r"\w+", text)))


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)
