"""Extract translatable paragraphs, with their styles, from a PDF page."""

import re

import pymupdf

from .models import Line, Paragraph, Span

# Expand ligatures ("ﬁ" -> "fi") so the translator sees normal words, and join
# words hyphenated across line breaks.
TEXT_FLAGS = (
    pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
) | pymupdf.TEXT_DEHYPHENATE

LIST_MARKER = re.compile(r"^\s*([•◦▪●■\-–*]|\d{1,3}[.)]|[a-zA-Z][.)])\s+")

# A line ending this far (fraction of block width) before the block's right
# edge, after sentence-final punctuation, is treated as a paragraph end.
SHORT_LINE_RATIO = 0.15


def extract_paragraphs(page: pymupdf.Page) -> list[Paragraph]:
    links = [
        (pymupdf.Rect(link["from"]), link["uri"])
        for link in page.get_links()
        if link.get("uri")
    ]
    lines = horizontal_lines(page)
    data = page.get_text("dict", flags=TEXT_FLAGS)

    paragraphs: list[Paragraph] = []
    for block in data["blocks"]:
        if block["type"] != 0:  # 1 = image block
            continue
        text_lines = [_build_line(raw, links, lines) for raw in block["lines"]]
        text_lines = [line for line in text_lines if line.text.strip()]
        if text_lines:
            block_rect = pymupdf.Rect(block["bbox"])
            paragraphs.extend(_split_block(text_lines, block_rect))
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
    bbox: pymupdf.Rect, baseline: float, size: float, lines: list[pymupdf.Rect]
) -> bool:
    """True if strokes just below the baseline cover most of the span width.

    Underlines are often drawn in pieces (one per word), so overlaps add up.
    """
    covered = 0.0
    for line in lines:
        y = (line.y0 + line.y1) / 2
        if baseline - 1 <= y <= baseline + 0.3 * size:
            covered += max(0.0, min(line.x1, bbox.x1) - max(line.x0, bbox.x0))
    return covered >= 0.8 * bbox.width


def _build_line(
    raw_line: dict, links: list[tuple[pymupdf.Rect, str]], lines: list[pymupdf.Rect]
) -> Line:
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
                and is_underlined(bbox, raw["origin"][1], raw["size"], lines),
            )
        )
    return Line(spans=spans, bbox=pymupdf.Rect(raw_line["bbox"]))


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


def _split_block(lines: list[Line], block_rect: pymupdf.Rect) -> list[Paragraph]:
    """PyMuPDF blocks may hold several paragraphs (e.g. a whole bullet list)."""
    paragraphs: list[Paragraph] = []
    current: Paragraph | None = None
    previous: Line | None = None

    for line in lines:
        if current is None or _starts_new_paragraph(previous, line, block_rect):
            current = Paragraph()
            paragraphs.append(current)
            if marker := LIST_MARKER.match(line.text):
                current.prefix = _strip_prefix(line, marker.end())
        current.lines.append(line)
        previous = line

    return paragraphs


def _starts_new_paragraph(previous: Line, line: Line, block_rect: pymupdf.Rect) -> bool:
    if LIST_MARKER.match(line.text):
        return True
    if abs(previous.spans[0].size - line.spans[0].size) > 0.5:
        return True
    ends_sentence = previous.text.rstrip().endswith((".", "!", "?", ":"))
    gap = block_rect.x1 - previous.bbox.x1
    return ends_sentence and gap > SHORT_LINE_RATIO * block_rect.width


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
