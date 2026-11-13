"""PyMuPDF lines -> visual lines: one per separate item, with small caps
and links resolved."""

import pymupdf

from .model import FLAG_SUPERSCRIPT, Line, Span

# A horizontal gap wider than this (in font sizes) inside a PyMuPDF line
# separates different items: table cells, tab stops...
COLUMN_GAP = 1.0
# Gaps between the words of one line: up to this, always (spaces of normal
# text); up to JUSTIFIED_GAP when another line of the paragraph runs across
# the gap, as justified text stretches its spaces on every line but the last.
WORD_GAP = 0.6
JUSTIFIED_GAP = 3.0
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
        lines.append(Line(group, rect, line.direction, line.origins))
    return lines


def join_pieces(lines: list[Line], rules: list[pymupdf.Rect]) -> list[Line]:
    """Join the pieces of one line of text, in reading order.

    PDFs often write a justified line one word at a time, and PyMuPDF then
    reads each word as a line of its own ("and", "two", "subassemblies,"),
    which became a paragraph each, translated alone and written over the
    text around it. Pieces on one baseline are one line when the gap
    between them is a space between words: a normal one, or a stretched
    one inside a justified paragraph. `rules`: vertical rules; pieces on
    both sides of one are table cells.
    """
    joined: list[Line] = []
    for line in lines:
        previous = joined[-1] if joined else None
        if previous is not None and _same_line(previous, line, lines, rules):
            joined[-1] = _join(previous, line)
        else:
            joined.append(line)
    return joined


def _same_line(left: Line, right: Line, lines: list[Line], rules: list[pymupdf.Rect]) -> bool:
    if not (left.horizontal and right.horizontal) or abs(left.baseline - right.baseline) > 1:
        return False
    # Dot leaders and other runs of symbols stay apart: they are kept as
    # they are, not translated with the words beside them.
    if not all(any(c.isalnum() for c in piece.text) for piece in (left, right)):
        return False
    size = max(s.size for s in left.spans)
    gap = right.bbox.x0 - left.bbox.x1
    if not -1 <= gap <= JUSTIFIED_GAP * size:
        return False
    if any(left.bbox.x1 - 0.5 <= (r.x0 + r.x1) / 2 <= right.bbox.x0 + 0.5 and r.y0 < left.baseline < r.y1 for r in rules):
        return False
    if gap <= WORD_GAP * size:
        return True
    return any(
        other.bbox.x0 < left.bbox.x1
        and other.bbox.x1 > right.bbox.x0
        and 0 < abs(other.baseline - left.baseline) <= 1.5 * size
        for other in lines
    )


def _join(left: Line, right: Line) -> Line:
    spans = list(left.spans)
    if not left.text.endswith(" ") and not right.text.startswith(" "):
        last = spans[-1]
        spans[-1] = Span(**{**last.__dict__, "text": last.text + " "})
    return Line(spans + right.spans, left.bbox | right.bbox, left.direction, left.origins + right.origins)


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
