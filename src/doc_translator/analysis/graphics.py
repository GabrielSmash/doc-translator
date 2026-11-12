"""Strokes and fills -> their role: underline of some text, or a rule that
separates text (table borders, the edges of a filled header).

Each stroke is classified once, here, so an underline and a table rule
cannot be told apart differently by two parts of the program.
"""

from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from ..pdf.read import column_rules
from .model import Line, Span


@dataclass(frozen=True)
class Stroke:
    """A thin horizontal line or rectangle, and where it comes from."""

    rect: pymupdf.Rect
    drawing: int  # index in page.get_drawings()
    item: int  # index in that drawing's items


def horizontal_lines(drawings: list[dict]) -> list[Stroke]:
    """Thin horizontal strokes/rects: candidates for underlines and rules."""
    found = []
    for d, drawing in enumerate(drawings):
        for i, item in enumerate(drawing["items"]):
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) < 0.5:
                    found.append(Stroke(pymupdf.Rect(start.x, start.y, end.x, end.y), d, i))
            elif item[0] == "re" and item[1].height < 1.5:
                found.append(Stroke(pymupdf.Rect(item[1]), d, i))
    return found


def separating_rules(lines: list[Line], drawings: list[dict]) -> list[pymupdf.Rect]:
    """Mark the underlined spans of `lines`; return the rules between lines.

    Rules are the strokes that are not underlines, plus the top and bottom
    edges of filled areas (a table header on a dark band), plus vertical
    rules (the sides of table cells and frames).
    """
    strokes = horizontal_lines(drawings)
    underlines = _mark_underlines(lines, strokes, drawings)
    verticals = [rule for drawing in drawings for rule in column_rules(drawing)]
    return [s.rect for s in strokes if s not in underlines] + _fill_edges(drawings) + verticals


def _fill_edges(drawings: list[dict]) -> list[pymupdf.Rect]:
    """Top and bottom edges of filled areas, as zero-height rules."""
    edges = []
    for drawing in drawings:
        if drawing.get("fill") is None:
            continue
        for item in drawing["items"]:
            if item[0] == "re" and item[1].height >= 1.5:
                box = item[1]
                edges += [pymupdf.Rect(box.x0, box.y0, box.x1, box.y0), pymupdf.Rect(box.x0, box.y1, box.x1, box.y1)]
    return edges


def _mark_underlines(
    lines: list[Line], strokes: list[Stroke], drawings: list[dict]
) -> set[Stroke]:
    """Flag spans drawn with an underline, and the drawings made only of them.

    Returns the strokes taken as underlines.

    An underline sits just below the baseline and stays within its line of
    text. A table rule sits at the same height but runs past the text to the
    cell border: taking it for an underline removed every rule of bordered
    tables, and the header fill with them.
    """
    accepted: dict[int, set[int]] = defaultdict(set)  # drawing -> items
    underline_strokes: set[Stroke] = set()
    underlined: list[tuple[Span, set[int]]] = []
    for line in lines:
        for span in line.spans:
            if not span.text.strip():
                continue
            tolerance = 0.5 * span.size
            covered, used = 0.0, []
            for stroke in strokes:
                y = (stroke.rect.y0 + stroke.rect.y1) / 2
                if not span.baseline - 1 <= y <= span.baseline + 0.3 * span.size:
                    continue
                if stroke.rect.x0 < line.bbox.x0 - tolerance or stroke.rect.x1 > line.bbox.x1 + tolerance:
                    continue  # runs past the text: a rule, not an underline
                overlap = min(stroke.rect.x1, span.bbox.x1) - max(stroke.rect.x0, span.bbox.x0)
                if overlap > 0:
                    covered += overlap
                    used.append(stroke)
            # Underlines are often drawn in pieces (one per word): overlaps add up.
            if covered >= 0.8 * span.bbox.width:
                span.underline = True
                for stroke in used:
                    accepted[stroke.drawing].add(stroke.item)
                    underline_strokes.add(stroke)
                underlined.append((span, {stroke.drawing for stroke in used}))

    # Only drawings whose every item is an accepted underline may be removed.
    pure = {d for d, items in accepted.items() if len(items) == len(drawings[d]["items"])}
    for span, used in underlined:
        span.underline_drawings = frozenset(used & pure)
    return underline_strokes
