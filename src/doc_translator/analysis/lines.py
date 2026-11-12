"""PyMuPDF lines -> visual lines: one per separate item, with small caps
and links resolved."""

import pymupdf

from .model import FLAG_SUPERSCRIPT, Line, Span

# A horizontal gap wider than this (in font sizes) inside a PyMuPDF line
# separates different items: table cells, tab stops...
COLUMN_GAP = 1.0
# A span this much smaller than the line's largest, with its baseline raised
# by this fraction of its own size, is a superscript.
SUPERSCRIPT_SIZE_RATIO = 0.85
SUPERSCRIPT_RISE = 0.25


def visual_lines(line: Line, links: list[tuple[pymupdf.Rect, str]]) -> list[Line]:
    """Split a PyMuPDF line into one Line per horizontally separate item."""
    for span in line.spans:
        span.link = _find_link(span.bbox, links)
    _mark_small_caps(line.spans)
    if not line.horizontal:
        return [line]  # gaps along x mean nothing for vertical text

    groups: list[list[Span]] = []
    for span in line.spans:
        if groups and span.bbox.x0 - groups[-1][-1].bbox.x1 <= COLUMN_GAP * span.size:
            groups[-1].append(span)
        else:
            groups.append([span])

    _mark_raised(line)
    lines = []
    for group in groups:
        rect = pymupdf.Rect(group[0].bbox)
        for span in group[1:]:
            rect |= span.bbox
        lines.append(Line(group, rect, line.direction, line.origin))
    return lines


def _mark_raised(line: Line) -> None:
    """Flag as superscript the smaller spans set above the line's baseline.

    PDFs rarely say a span is a superscript: footnote marks ("∗Equal
    contribution", "¹ https://...") are just smaller text drawn higher.
    """
    largest = max(s.size for s in line.spans)
    for span in line.spans:
        raised = line.baseline - span.baseline
        if span.size < SUPERSCRIPT_SIZE_RATIO * largest and raised > SUPERSCRIPT_RISE * span.size:
            span.flags |= FLAG_SUPERSCRIPT


def _mark_small_caps(spans: list[Span]) -> None:
    """Detect small caps: all-capital spans next to larger all-capital ones."""

    def all_caps(span: Span) -> bool:
        letters = [c for c in span.text if c.isalpha()]
        return bool(letters) and all(c.isupper() for c in letters)

    for index, small in enumerate(spans):
        if not all_caps(small):
            continue
        for neighbor in (index - 1, index + 1):
            if not 0 <= neighbor < len(spans):
                continue
            big = spans[neighbor]
            ratio = small.size / big.size
            if big.font == small.font and all_caps(big) and 0.6 <= ratio <= 0.92:
                small.small_caps = big.small_caps = ratio
                small.size = big.size
                break


def _find_link(bbox: pymupdf.Rect, links: list[tuple[pymupdf.Rect, str]]) -> str | None:
    # Links are often split into one rect per word: sum the overlaps per URI.
    coverage: dict[str, float] = {}
    for rect, uri in links:
        overlap = bbox & rect
        if not overlap.is_empty:
            coverage[uri] = coverage.get(uri, 0) + overlap.get_area()
    best = max(coverage, key=coverage.get, default=None)
    if best and coverage[best] >= 0.5 * bbox.get_area():
        return best
    return None
