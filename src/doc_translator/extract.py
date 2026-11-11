"""Extract translatable paragraphs, with their styles, from a PDF page."""

import re
from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from .models import Leader, Line, Paragraph, Span

# Expand ligatures ("ﬁ" -> "fi") so the translator sees normal words, and join
# words hyphenated across line breaks.
TEXT_FLAGS = (
    pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
) | pymupdf.TEXT_DEHYPHENATE

# Bullets, "1." / "7.2.1." / "a)" numbering.
MARKER = r"([•◦▪●■\-–*]|\d{1,3}(?:\.\d{1,3})*[.)]|[a-zA-Z][.)])"
# Table-of-contents ending: "........ 15"
LEADER = re.compile(r"\s*(?:[.·…]\s?){4,}\s*(\d+|[ivxlcdm]+)?\s*$", re.IGNORECASE)
LIST_MARKER = re.compile(rf"^\s*{MARKER}\s+")
MARKER_ONLY = re.compile(rf"^\s*{MARKER}\s*$")
SYMBOL_FONTS = ("symbol", "wingdings", "dingbats", "webdings")
MONO_HINTS = ("mono", "courier", "consol")
FLAG_MONOSPACED = 8

# Tolerance, in points, when comparing positions of lines.
ALIGN_TOLERANCE = 2.0
# A horizontal gap wider than this (in font sizes) inside a PyMuPDF line
# separates different items: table cells, tab stops...
COLUMN_GAP = 1.0
# Max distance between baselines (in font sizes) for lines of one paragraph.
MAX_LINE_SPACING = 1.8
# A line ending this far (fraction of block width) before the block's right
# edge, after sentence-final punctuation, is treated as a paragraph end.
SHORT_LINE_RATIO = 0.15


def extract_paragraphs(page: pymupdf.Page) -> list[Paragraph]:
    links = [
        (pymupdf.Rect(link["from"]), link["uri"])
        for link in page.get_links()
        if link.get("uri")
    ]
    drawings = page.get_drawings()
    strokes = horizontal_lines(drawings)
    data = page.get_text("dict", flags=TEXT_FLAGS)

    blocks: list[tuple[pymupdf.Rect, list[Line]]] = []
    for block in data["blocks"]:
        if block["type"] != 0:  # 1 = image block
            continue
        lines = [
            line
            for raw in block["lines"]
            for line in _build_lines(raw, links)
            if line.text.strip() and not _is_code(line)
        ]
        if lines:
            blocks.append((pymupdf.Rect(block["bbox"]), lines))

    underlines = _mark_underlines([line for _, lines in blocks for line in lines], strokes, drawings)
    # The other strokes are rules: one between two lines separates them. So
    # does the edge of a filled area (a table header on a dark band).
    rules = [stroke.rect for stroke in strokes if stroke not in underlines]
    rules += _fill_edges(drawings)
    paragraphs: list[Paragraph] = []
    for block_rect, lines in blocks:
        paragraphs.extend(_split_block(lines, block_rect, rules))
    return paragraphs


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


def _build_lines(raw_line: dict, links: list[tuple[pymupdf.Rect, str]]) -> list[Line]:
    """Turn a PyMuPDF line into one Line per horizontally separate item."""
    spans = []
    for raw in raw_line["spans"]:
        if not raw["text"]:
            continue
        bbox = pymupdf.Rect(raw["bbox"])
        spans.append(
            Span(
                text=raw["text"],
                font=raw["font"],
                size=raw["size"],
                flags=raw["flags"],
                color=raw["color"],
                bbox=bbox,
                baseline=raw["origin"][1],
                link=_find_link(bbox, links),
            )
        )
    _mark_small_caps(spans)

    groups: list[list[Span]] = []
    for span in spans:
        if groups and span.bbox.x0 - groups[-1][-1].bbox.x1 <= COLUMN_GAP * span.size:
            groups[-1].append(span)
        else:
            groups.append([span])

    lines = []
    for group in groups:
        rect = pymupdf.Rect(group[0].bbox)
        for span in group[1:]:
            rect |= span.bbox
        lines.append(Line(spans=group, bbox=rect))
    return lines


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


def _is_code(line: Line) -> bool:
    """Monospaced text (code, terminal output) is left untranslated."""
    spans = [s for s in line.spans if s.text.strip()]
    return all(
        s.flags & FLAG_MONOSPACED or any(h in s.font.lower() for h in MONO_HINTS) for s in spans
    )


def _split_block(
    lines: list[Line], block_rect: pymupdf.Rect, strokes: list[pymupdf.Rect]
) -> list[Paragraph]:
    """Group a block's lines into paragraphs.

    PyMuPDF blocks may hold several paragraphs (a whole bullet list) or even
    several columns (table rows, diagram labels), so each line joins the
    paragraph whose last line sits right above it, if any.
    """
    paragraphs: list[Paragraph] = []
    marker_baselines: list[float] = []
    # Where a paragraph started, as (baseline, x0). A line beside a paragraph
    # start is in a new table row, even without a rule in its own column.
    starts: list[tuple[float, float]] = []

    for line in sorted(lines, key=lambda l: (round(l.baseline), l.bbox.x0)):
        # A bullet or number in its own "line" (common when a tab follows it)
        # is left untouched in the PDF: never translated nor redrawn.
        if _is_marker_only(line):
            marker_baselines.append(line.baseline)
            continue

        after_marker = any(abs(b - line.baseline) < 1 for b in marker_baselines)
        new_row = any(
            abs(b - line.baseline) < 1 and x < line.bbox.x0 for b, x in starts
        )
        target = None
        if not (after_marker or new_row) and not LIST_MARKER.match(line.text):
            target = next(
                (
                    p
                    for p in reversed(paragraphs)
                    # A TOC entry ends at its page number.
                    if p.leader is None and _continues(p.lines[-1], line, block_rect, strokes)
                ),
                None,
            )
        if target is None:
            target = Paragraph()
            paragraphs.append(target)
            starts.append((line.baseline, line.bbox.x0))
            if marker := LIST_MARKER.match(line.text):
                target.prefix = _strip_prefix(line, marker.end())
        if leader := LEADER.search(line.text):
            target.leader = _strip_leader(line, leader)
        target.lines.append(line)

    # Single-line paragraphs say nothing about alignment: use the block's.
    block_align = _alignment([line for p in paragraphs for line in p.lines])
    for paragraph in paragraphs:
        if paragraph.leader:
            continue  # TOC entries look right-aligned (page numbers) but aren't
        if len(paragraph.lines) > 1:
            paragraph.align = _alignment(paragraph.lines)
        elif block_align in ("right", "center"):
            paragraph.align = block_align
    return paragraphs


def _is_marker_only(line: Line) -> bool:
    if MARKER_ONLY.match(line.text):
        return True
    spans = [s for s in line.spans if s.text.strip()]
    return all(s.font.lower().startswith(SYMBOL_FONTS) for s in spans)


def _continues(
    previous: Line, line: Line, block_rect: pymupdf.Rect, strokes: list[pymupdf.Rect]
) -> bool:
    """Is `line` the next line of the paragraph ending with `previous`?"""
    size = max(s.size for s in previous.spans)
    spacing = line.baseline - previous.baseline
    if not 0 < spacing <= MAX_LINE_SPACING * size:
        return False
    if line.bbox.x0 >= previous.bbox.x1 or line.bbox.x1 <= previous.bbox.x0:
        return False  # no horizontal overlap: another column
    if abs(previous.spans[0].size - line.spans[0].size) > 0.5:
        return False
    if _rule_between(previous, line, strokes):
        return False  # e.g. the border between two table rows
    ends_sentence = previous.text.rstrip().endswith((".", "!", "?", ":"))
    gap = block_rect.x1 - previous.bbox.x1
    return not (ends_sentence and gap > SHORT_LINE_RATIO * block_rect.width)


def _rule_between(upper: Line, lower: Line, rules: list[pymupdf.Rect]) -> bool:
    """A rule below the upper line's baseline and above the lower line's
    letters: the border between two table rows.

    Tight tables put it where an underline would be, so underlines are
    excluded from `rules` instead. A border spans the line; shorter strokes
    in that band are something else (the bar of a square root, √dk).
    """
    top = upper.baseline
    bottom = lower.baseline - 0.5 * max(s.size for s in lower.spans)
    width = lower.bbox.width
    return any(
        top < (s.y0 + s.y1) / 2 < bottom
        and min(s.x1, lower.bbox.x1) - max(s.x0, lower.bbox.x0) >= 0.5 * width
        for s in rules
    )


def _alignment(lines: list[Line]) -> str:
    """Infer text-align from the line edges (needs at least two lines)."""
    if len(lines) < 2:
        return "left"

    def aligned(values: list[float]) -> bool:
        return max(values) - min(values) < ALIGN_TOLERANCE

    lefts = [line.bbox.x0 for line in lines]
    rights = [line.bbox.x1 for line in lines]
    centers = [(line.bbox.x0 + line.bbox.x1) / 2 for line in lines]
    if aligned(lefts):
        # The last line of a justified paragraph is shorter.
        return "justify" if aligned(rights[:-1]) and len(lines) > 2 else "left"
    if aligned(rights):
        return "right"
    if aligned(centers):
        return "center"
    return "left"


def _strip_leader(line: Line, match: re.Match) -> Leader:
    """Remove "...... 15" from the line, keeping the page number's style."""
    number = match.group(1) or ""
    styled = next((s for s in reversed(line.spans) if number and number in s.text), line.spans[-1])
    leader = Leader(
        number=number,
        font=styled.font,
        size=styled.size,
        color=styled.color,
        bold=styled.bold,
        right=line.bbox.x1,
    )
    keep = match.start()
    for span in line.spans:
        span.text, keep = span.text[:keep], max(0, keep - len(span.text))
    line.spans = [s for s in line.spans if s.text]
    return leader


def _strip_prefix(line: Line, length: int) -> str:
    """Remove the list marker from the line's spans and return it."""
    prefix = line.text[:length]
    remaining = length
    for span in line.spans:
        cut = min(remaining, len(span.text))
        span.text = span.text[cut:]
        remaining -= cut
        if remaining == 0:
            break
    line.spans = [s for s in line.spans if s.text]
    return prefix
