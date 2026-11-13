"""Visual lines -> blocks: paragraphs, list items, table cells, TOC entries."""

import re

import pymupdf

from .classify import kept_block
from .model import Block, Kind, Leader, Line

# Bullets, "1." / "7.2.1." / "a)" numbering.
MARKER = r"([•◦▪●■\-–*]|\d{1,3}(?:\.\d{1,3})*[.)]|[a-zA-Z][.)])"
# Table-of-contents ending: "........ 15"
LEADER = re.compile(r"\s*(?:[.·…]\s?){4,}\s*(\d+|[ivxlcdm]+)?\s*$", re.IGNORECASE)
# "(a)", "(2)": forms number their items this way. Running text also wraps
# with "(b) is more accurate" first, so such an item starts with a capital.
PARENTHESIZED = r"(\((?:[a-zA-Z]|\d{1,2}|[ivx]{1,4})\))"
LIST_MARKER = re.compile(rf"^\s*(?:{MARKER}\s+|{PARENTHESIZED}\s+(?=[A-Z]))")
MARKER_ONLY = re.compile(rf"^\s*(?:{MARKER}|{PARENTHESIZED})\s*$")
SYMBOL_FONTS = ("symbol", "wingdings", "dingbats", "webdings")
# Number of a bibliography entry: "[33] Nitish Srivastava, ...". Unlike a
# list marker it does not start a block on its own, since running text can
# wrap with a citation first ("[16] and ConvS2S [9]").
# Followed by text: a number alone in its cell is left as it is.
REFERENCE = re.compile(r"^\s*\[\d{1,3}\]\s+(?=\S)")
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
# Words separated by 3+ spaces: columns aligned by hand ("Date   : Jul 17").
# Prose has at most two (after a period).
ALIGNED_COLUMNS = re.compile(r"\S {3,}\S")
# Width of a space, in font sizes, when testing whether a word would fit.
HAND_BREAK_SPACE = 0.3
# Room on the left, in font sizes, beyond any first-line indent.
HAND_BREAK_LEFT = 4.0


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
                    if p.leader is None and _continues(p.lines[-1], line, block_rect, rules, lines)
                ),
                None,
            )
        if target is None:
            target = Block()
            paragraphs.append(target)
            blocks.append(target)
            starts.append((line.baseline, line.bbox.x0))
            # Kept out of the translation, which may drop it ("[33]" was lost).
            if marker := LIST_MARKER.match(line.text) or REFERENCE.match(line.text):
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
    previous: Line,
    line: Line,
    block_rect: pymupdf.Rect,
    rules: list[pymupdf.Rect],
    lines: list[Line],
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
    if _broken_by_hand(previous, line, lines, rules):
        return False  # menu and terminal lines, two-line headers, notes
    ends_sentence = previous.text.rstrip().endswith((".", "!", "?", ":"))
    gap = block_rect.x1 - previous.bbox.x1
    return not (ends_sentence and gap > SHORT_LINE_RATIO * block_rect.width)


def _broken_by_hand(
    previous: Line, line: Line, lines: list[Line], rules: list[pymupdf.Rect] | None = None
) -> bool:
    """Would the first word of `line` have fit at the end of `previous`?

    Text wraps only when the next word does not fit; if it would have, the
    line was ended on purpose (menu and terminal lines, two-line headers, a
    note after a list item). The room is measured against the other lines of
    the column. The word is measured in Helvetica, wider than most text
    fonts, so that justified text (whose line breaks are not greedy) is not
    split by an underestimate.
    """
    if ALIGNED_COLUMNS.search(previous.text) or ALIGNED_COLUMNS.search(line.text):
        return True  # columns lined up with spaces: terminal output, a screen
    words = line.text.split()
    span = next((s for s in line.spans if s.text.strip()), None)
    if not words or span is None:
        return False
    fontname = "hebo" if span.bold else "helv"
    needed = pymupdf.get_text_length(words[0], fontname=fontname, fontsize=span.size)
    needed += HAND_BREAK_SPACE * span.size
    # Lines of the same column, near these two: a wider row elsewhere in the
    # block (a table heading) says nothing about this paragraph's width.
    reach = MAX_LINE_SPACING * span.size
    column = [
        l
        for l in lines
        if l.bbox.x0 < previous.bbox.x1
        and l.bbox.x1 > previous.bbox.x0
        and previous.baseline - reach <= l.baseline <= line.baseline + reach
    ]
    # Room taken by something else on the same line (a run-in heading, the
    # next table cell) is not room.
    beside = [l for l in lines if l is not previous and abs(l.baseline - previous.baseline) < ALIGN_TOLERANCE]
    right_room = max(l.bbox.x1 for l in column) - previous.bbox.x1
    if box := _box_around(previous, line, rules or []):
        # Lines in a frame (a menu, a terminal screen) could use its width,
        # up to the next vertical rule (a table cell's side), less the same
        # margin they keep on the left.
        sides = [r for r in rules if r.height > r.width and r.y0 <= previous.baseline <= r.y1]
        right = min([box.x1, *(r.x0 for r in sides if r.x0 >= previous.bbox.x1)])
        left = max([box.x0, *(r.x1 for r in sides if r.x1 <= previous.bbox.x0)])
        right_room = max(right_room, (right - previous.bbox.x1) - (previous.bbox.x0 - left))
    if any(l.bbox.x0 >= previous.bbox.x1 - ALIGN_TOLERANCE for l in beside):
        right_room = 0
    if right_room - ALIGN_TOLERANCE >= needed:
        return True
    # Right-aligned or centered lines leave their room on the left. A first-
    # line indent does too, so only a room well beyond an indent counts.
    right_aligned = abs(previous.bbox.x1 - line.bbox.x1) < ALIGN_TOLERANCE
    centered = abs((previous.bbox.x0 + previous.bbox.x1) - (line.bbox.x0 + line.bbox.x1)) < 2 * ALIGN_TOLERANCE
    left_room = previous.bbox.x0 - min(l.bbox.x0 for l in column)
    if not (right_aligned or centered) or any(
        l.bbox.x1 <= previous.bbox.x0 + ALIGN_TOLERANCE for l in beside
    ):
        left_room = 0
    return left_room >= max(needed, HAND_BREAK_LEFT * span.size)


def _box_around(previous: Line, line: Line, rules: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """The frame the two lines sit in: the nearest rules above and below
    covering them, of the same width, with a side rule at each end."""
    def covers(rule: pymupdf.Rect) -> bool:
        return rule.x0 <= previous.bbox.x0 and rule.x1 >= max(previous.bbox.x1, line.bbox.x1)

    above = [r for r in rules if covers(r) and r.y1 <= previous.bbox.y0 + 1]
    below = [r for r in rules if covers(r) and r.y0 >= line.bbox.y1 - 1]
    if not above or not below:
        return None
    top = max(above, key=lambda r: r.y1)
    bottom = min(below, key=lambda r: r.y0)
    if abs(top.x0 - bottom.x0) > ALIGN_TOLERANCE or abs(top.x1 - bottom.x1) > ALIGN_TOLERANCE:
        return None
    # And sides: rules above and below alone may be the page's header and
    # footer lines.
    sides = [r for r in rules if r.height > r.width and r.y0 <= previous.baseline <= r.y1]
    if not (
        any(abs(r.x0 - top.x0) <= ALIGN_TOLERANCE for r in sides)
        and any(abs(r.x1 - top.x1) <= ALIGN_TOLERANCE for r in sides)
    ):
        return None
    return pymupdf.Rect(top.x0, top.y1, top.x1, bottom.y0)


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
