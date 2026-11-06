"""Extract translatable paragraphs, with their styles, from a PDF page."""

import re

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
    strokes = horizontal_lines(page)
    data = page.get_text("dict", flags=TEXT_FLAGS)

    paragraphs: list[Paragraph] = []
    for block in data["blocks"]:
        if block["type"] != 0:  # 1 = image block
            continue
        lines = [
            line
            for raw in block["lines"]
            for line in _build_lines(raw, links, strokes)
            if line.text.strip() and not _is_code(line)
        ]
        if lines:
            paragraphs.extend(_split_block(lines, pymupdf.Rect(block["bbox"]), strokes))
    return paragraphs


def horizontal_lines(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Thin horizontal strokes/rects: candidates for text underlines."""
    found = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) < 0.5:
                    found.append(pymupdf.Rect(start.x, start.y, end.x, end.y))
            elif item[0] == "re" and item[1].height < 1.5:
                found.append(pymupdf.Rect(item[1]))
    return found


def is_underlined(
    bbox: pymupdf.Rect, baseline: float, size: float, strokes: list[pymupdf.Rect]
) -> bool:
    """True if strokes just below the baseline cover most of the span width.

    Underlines are often drawn in pieces (one per word), so overlaps add up.
    """
    covered = 0.0
    for stroke in strokes:
        y = (stroke.y0 + stroke.y1) / 2
        if baseline - 1 <= y <= baseline + 0.3 * size:
            covered += max(0.0, min(stroke.x1, bbox.x1) - max(stroke.x0, bbox.x0))
    return covered >= 0.8 * bbox.width


def _build_lines(
    raw_line: dict, links: list[tuple[pymupdf.Rect, str]], strokes: list[pymupdf.Rect]
) -> list[Line]:
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
                underline=bool(raw["text"].strip())
                and is_underlined(bbox, raw["origin"][1], raw["size"], strokes),
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


def _rule_between(upper: Line, lower: Line, strokes: list[pymupdf.Rect]) -> bool:
    top = upper.baseline + 0.3 * max(s.size for s in upper.spans)
    bottom = lower.bbox.y0 + 0.5 * lower.bbox.height
    return any(
        top < (s.y0 + s.y1) / 2 < bottom and s.x0 < lower.bbox.x1 and s.x1 > lower.bbox.x0
        for s in strokes
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
