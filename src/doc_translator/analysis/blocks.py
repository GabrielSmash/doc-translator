"""Visual lines -> blocks: paragraphs, list items, table cells, TOC entries."""

import re

import pymupdf

from .classify import kept_block
from .model import Block, Kind, Leader, Line

# Bullets, "1." / "7.2.1." / "a)" numbering.
MARKER = r"([•◦▪●■\-–*]|\d{1,3}(?:\.\d{1,3})*[.)]|[a-zA-Z][.)])"
# Table-of-contents ending: "........ 15"
LEADER = re.compile(r"\s*(?:[.·…]\s?){4,}\s*(\d+|[ivxlcdm]+)?\s*$", re.IGNORECASE)
LIST_MARKER = re.compile(rf"^\s*{MARKER}\s+")
MARKER_ONLY = re.compile(rf"^\s*{MARKER}\s*$")
SYMBOL_FONTS = ("symbol", "wingdings", "dingbats", "webdings")
FOOTNOTE_MARK = re.compile(r"^\s*(?:\d{1,3}|[*†‡§¶])\s*$")
# A footnote mark is set this much smaller than the note's text, at most.
FOOTNOTE_SIZE_RATIO = 0.85

# Tolerance, in points, when comparing positions of lines.
ALIGN_TOLERANCE = 2.0
# Max distance between baselines (in font sizes) for lines of one paragraph.
MAX_LINE_SPACING = 1.8
# A line ending this far (fraction of block width) before the block's right
# edge, after sentence-final punctuation, is treated as a paragraph end.
SHORT_LINE_RATIO = 0.15


def split_block(
    lines: list[Line], block_rect: pymupdf.Rect, rules: list[pymupdf.Rect]
) -> list[Block]:
    """Group the lines of a PyMuPDF block into blocks.

    PyMuPDF blocks may hold several paragraphs (a whole bullet list) or even
    several columns (table rows, diagram labels), so each line joins the
    paragraph whose last line sits right above it, if any.
    """
    blocks: list[Block] = []
    paragraphs: list[Block] = []
    marker_baselines: list[float] = []
    # Where a paragraph started, as (baseline, x0). A line beside a paragraph
    # start is in a new table row, even without a rule in its own column.
    starts: list[tuple[float, float]] = []

    for line in sorted(lines, key=lambda l: (round(l.baseline), l.bbox.x0)):
        # A bullet or number in its own "line" (common when a tab follows it)
        # is left untouched in the PDF: never translated nor redrawn.
        if _is_marker_only(line):
            marker_baselines.append(line.baseline)
            blocks.append(kept_block([line], Kind.MARKER))
            continue
        if not any(c.isalnum() for c in line.text):
            # Dots of a form's leaders, arrows, "!": nothing to translate.
            blocks.append(kept_block([line], Kind.SYMBOLS))
            continue

        after_marker = any(abs(b - line.baseline) < 1 for b in marker_baselines)
        new_row = any(
            abs(b - line.baseline) < 1 and x < line.bbox.x0 for b, x in starts
        )
        target = None
        starts_item = LIST_MARKER.match(line.text) or _starts_footnote(line)
        if not (after_marker or new_row or starts_item):
            target = next(
                (
                    p
                    for p in reversed(paragraphs)
                    # A TOC entry ends at its page number.
                    if p.leader is None and _continues(p.lines[-1], line, block_rect, rules)
                ),
                None,
            )
        if target is None:
            target = Block()
            paragraphs.append(target)
            blocks.append(target)
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
    return blocks


def _starts_footnote(line: Line) -> bool:
    """A small number or mark before text: "¹ https://...", "† Work done at".
    Consecutive footnotes otherwise read as one paragraph."""
    spans = [s for s in line.spans if s.text.strip()]
    if len(spans) < 2 or not FOOTNOTE_MARK.match(spans[0].text):
        return False
    return spans[0].superscript or spans[0].size < FOOTNOTE_SIZE_RATIO * max(s.size for s in spans[1:])


def _is_marker_only(line: Line) -> bool:
    if MARKER_ONLY.match(line.text):
        return True
    spans = [s for s in line.spans if s.text.strip()]
    return all(s.font.lower().startswith(SYMBOL_FONTS) for s in spans)


def _continues(
    previous: Line, line: Line, block_rect: pymupdf.Rect, rules: list[pymupdf.Rect]
) -> bool:
    """Is `line` the next line of the paragraph ending with `previous`?"""
    size = max(s.size for s in previous.spans)
    spacing = line.baseline - previous.baseline
    if not 0 < spacing <= MAX_LINE_SPACING * size:
        return False
    if line.bbox.x0 >= previous.bbox.x1 or line.bbox.x1 <= previous.bbox.x0:
        return False  # no horizontal overlap: another column
    # The largest letters: a footnote mark or superscript starting a line
    # is smaller than the text it belongs to.
    if abs(size - max(s.size for s in line.spans)) > 0.5:
        return False
    if _rule_between(previous, line, rules):
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
