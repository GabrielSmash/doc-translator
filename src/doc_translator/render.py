"""Replace the original text of a page with its translation."""

import html
import logging
import re
from dataclasses import dataclass

import pymupdf

from .align import Run
from .fonts import FontLibrary, generic_family
from .analysis.model import Block, Leader

log = logging.getLogger(__name__)

# Shrink redaction boxes vertically so they don't catch glyphs of the lines
# right above/below (line bboxes overlap slightly).
REDACT_INSET = 0.2
# Minimum distance kept between the grown text box and the next element.
GAP = 2.0
# Built-in PDF fonts used to draw TOC leaders and page numbers.
BASE14 = {
    "sans-serif": ("helv", "hebo"),
    "serif": ("tiro", "tibo"),
    "monospace": ("cour", "cobo"),
}


@dataclass
class TranslatedParagraph:
    source: Block
    runs: list[Run]


@dataclass
class Placement:
    """Where a paragraph was written and how much it had to shrink."""

    paragraph: TranslatedParagraph
    rect: pymupdf.Rect
    scale: float  # 1.0 = original font size
    fitted: bool  # False: the text overflowed the box even when scaled


def render_page(
    page: pymupdf.Page,
    paragraphs: list[TranslatedParagraph],
    fonts: FontLibrary,
    kept: list[pymupdf.Rect] = (),
) -> list[Placement]:
    """`kept`: areas of text left as it is (code, markers, rotated text),
    which translations must not grow into."""
    obstacles = _obstacles(page, paragraphs) + list(kept)
    _remove_original_text(page, paragraphs)
    return [_insert(page, paragraph, obstacles, fonts) for paragraph in paragraphs]


def _obstacles(page: pymupdf.Page, paragraphs: list[TranslatedParagraph]) -> list[pymupdf.Rect]:
    """Everything a translated paragraph must not grow into."""
    rects = [p.source.bbox for p in paragraphs]
    rects += [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
    for drawing in page.get_drawings():
        rect = pymupdf.Rect(drawing["rect"])
        only_strokes = all(item[0] == "l" for item in drawing["items"])
        if not only_strokes and rect.height >= 1.5 and rect.width >= 1.5:
            rects.append(rect)
    return rects


def _remove_original_text(page: pymupdf.Page, paragraphs: list[TranslatedParagraph]) -> None:
    # Read before any redaction: span.underline_drawings index this list.
    drawings = page.get_drawings()

    # Links over translated text are recreated at the new text position.
    text_area = [p.source.bbox for p in paragraphs]
    for link in page.get_links():
        if link.get("uri") and any(pymupdf.Rect(link["from"]).intersects(r) for r in text_area):
            page.delete_link(link)

    for paragraph in paragraphs:
        for line in paragraph.source.lines:
            rect = pymupdf.Rect(line.bbox)
            inset = rect.height * REDACT_INSET
            page.add_redact_annot(rect + (0, inset, 0, -inset))
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )

    # Second pass: drop the drawings that underlined the original text (they
    # are redrawn by CSS under the translation). Only drawings made of
    # underlines alone, removed only if entirely inside their zone: removing
    # whatever a zone touched also erased table rules and fills.
    underline_drawings = {d for p in paragraphs for s in p.source.spans for d in s.underline_drawings}
    for index in underline_drawings:
        page.add_redact_annot(_items_rect(drawings[index]) + (-1.5, -1.5, 1.5, 1.5))
    if underline_drawings:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=pymupdf.PDF_REDACT_TEXT_NONE,
        )


def _items_rect(drawing: dict) -> pymupdf.Rect:
    """Bounds of a drawing's items.

    drawing["rect"] can miss items (for an underline drawn in pieces over two
    lines it left out the second line), and Rect union can't be used: it
    skips "empty" rectangles, which every horizontal line is (height 0).
    """
    points = []
    for item in drawing["items"]:
        if item[0] == "re":
            points += [item[1].tl, item[1].br]
        elif item[0] == "qu":
            points += [item[1].ul, item[1].lr]
        else:
            points += [p for p in item[1:] if isinstance(p, pymupdf.Point)]
    if not points:
        return pymupdf.Rect(drawing["rect"])
    xs, ys = [p.x for p in points], [p.y for p in points]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _insert(
    page: pymupdf.Page,
    paragraph: TranslatedParagraph,
    obstacles: list[pymupdf.Rect],
    fonts: FontLibrary,
) -> Placement:
    source = paragraph.source
    rect = _available_rect(page, source, obstacles)
    if source.leader:
        # Keep room for a few dots and the page number.
        leader = source.leader
        rect.x1 = leader.right - _text_width(leader.number, leader) - 4 * leader.size
    prefix = [Run(source.prefix, paragraph.runs[0].style)] if source.prefix else []
    body = "".join(_run_html(run, fonts) for run in [*prefix, *paragraph.runs])
    html_text = f"<p>{body}</p>"
    # Explicit selectors: the HTML engine's default `p` margins (1em) would
    # win over a `*` rule.
    css = (
        f"{fonts.css}\n"
        f"body, p {{ margin: 0; padding: 0; }} "
        f"p {{ line-height: {_line_height(source)}; text-align: {source.align}; }}"
    )

    # Align the first baseline with the original one. The offset depends on
    # the font metrics used by the HTML engine, so it is measured on a scratch
    # page instead of computed.
    baseline = _first_baseline(page.rect, rect, html_text, css, fonts.archive)
    if baseline is not None:
        shift = source.lines[0].spans[0].baseline - baseline
        rect += (0, shift, 0, shift)

    spare_height, scale = page.insert_htmlbox(rect, html_text, css=css, archive=fonts.archive)
    if spare_height < 0:
        log.warning("Page %d: text did not fit in %s", page.number + 1, rect)
    elif scale < 1:
        log.debug("Page %d: %r scaled to %.0f%%", page.number + 1, source.text[:30], scale * 100)

    _recreate_links(page, rect, paragraph.runs)
    if source.leader:
        _draw_leader(page, rect, source.leader)
    return Placement(paragraph, rect, scale, fitted=spare_height >= 0)


def _draw_leader(page: pymupdf.Page, rect: pymupdf.Rect, leader: Leader) -> None:
    """Fill the gap between the translated title and the page number with dots."""
    spans = [
        span
        for block in page.get_text("dict", clip=rect)["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["text"].strip()
    ]
    if not spans:
        return
    baseline = max(span["origin"][1] for span in spans)
    end = max(span["bbox"][2] for span in spans if abs(span["origin"][1] - baseline) < 1)

    fontname = _base14(leader)
    color = tuple(((leader.color >> shift) & 0xFF) / 255 for shift in (16, 8, 0))
    number_x = leader.right - _text_width(leader.number, leader)
    dot = _text_width(".", leader)
    count = int((number_x - end) / dot) - 2
    if count > 0:
        page.insert_text(
            (end + dot, baseline), "." * count, fontname=fontname, fontsize=leader.size, color=color
        )
    page.insert_text(
        (number_x, baseline), leader.number, fontname=fontname, fontsize=leader.size, color=color
    )


def _base14(leader: Leader) -> str:
    regular, bold = BASE14[generic_family(leader.font)]
    return bold if leader.bold else regular


def _text_width(text: str, leader: Leader) -> float:
    return pymupdf.get_text_length(text, fontname=_base14(leader), fontsize=leader.size)


def _available_rect(
    page: pymupdf.Page, paragraph: Block, obstacles: list[pymupdf.Rect]
) -> pymupdf.Rect:
    """Grow the paragraph box into the empty space around it.

    Single-line paragraphs (titles, list items) may grow sideways, since their
    width says nothing about the column width: away from the side they are
    aligned to. Every paragraph may grow downward until the next element.
    """
    rect = pymupdf.Rect(paragraph.bbox)
    others = [o for o in obstacles if not o.contains(rect) and o != paragraph.bbox]
    # Content area: margins mirrored from the top-left corner, widened to
    # whatever the page actually uses (margins are often asymmetric).
    left_margin = min((o.x0 for o in obstacles), default=rect.x0)
    top_margin = min((o.y0 for o in obstacles), default=rect.y0)
    content_right = max([page.rect.x1 - left_margin, *(o.x1 for o in obstacles)])
    content_bottom = max([page.rect.y1 - top_margin, *(o.y1 for o in obstacles)])

    if len(paragraph.lines) == 1:
        same_row = [o for o in others if o.y0 < rect.y1 and o.y1 > rect.y0]
        right = content_right
        left = left_margin
        for o in same_row:
            if o.x0 >= rect.x1 - 1:
                right = min(right, o.x0 - GAP)
            if o.x1 <= rect.x0 + 1:
                left = max(left, o.x1 + GAP)
        room_right = max(0.0, right - rect.x1)
        room_left = max(0.0, rect.x0 - left)
        if paragraph.align == "right":
            rect.x0 -= room_left
        elif paragraph.align == "center":
            rect.x0 -= min(room_left, room_right)
            rect.x1 += min(room_left, room_right)
        else:
            rect.x1 += room_right

    bottom = content_bottom
    for o in others:
        is_below = o.y0 > rect.y0 + rect.height / 2
        if is_below and o.x0 < rect.x1 and o.x1 > rect.x0:
            bottom = min(bottom, o.y0 - GAP)
    rect.y1 = max(rect.y1, bottom)
    return rect


def _first_baseline(
    page_rect: pymupdf.Rect,
    rect: pymupdf.Rect,
    html_text: str,
    css: str,
    archive: pymupdf.Archive,
) -> float | None:
    scratch = pymupdf.open()
    page = scratch.new_page(width=page_rect.width, height=page_rect.height)
    page.insert_htmlbox(rect, html_text, css=css, archive=archive)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                return span["origin"][1]
    return None


def _run_html(run: Run, fonts: FontLibrary) -> str:
    style = run.style
    css = [
        f"font-family: {fonts.family(style.font)}",
        f"font-size: {style.size}px",
        f"color: #{style.color:06x}",
        f"font-weight: {'bold' if style.bold else 'normal'}",
        f"font-style: {'italic' if style.italic else 'normal'}",
    ]
    if style.underline:
        css.append("text-decoration: underline")
    if style.superscript:
        css.append("vertical-align: super")
    content = (
        _small_caps_html(run.text, style.size * style.small_caps)
        if style.small_caps
        else html.escape(run.text)
    )
    return f'<span style="{"; ".join(css)}">{content}</span>'


def _small_caps_html(text: str, small_size: float) -> str:
    """Emulate small caps: words start with a full-size capital, the rest of
    the letters are capitals at the smaller size. Numbers stay full size."""
    small = f'<span style="font-size: {small_size:.1f}px">{{}}</span>'
    parts = []
    for token in re.split(r"(\s+)", text):
        if not token.strip() or not token[0].isalpha():
            parts.append(html.escape(token))
        elif len(token) > 3 or not parts:
            parts.append(html.escape(token[0].upper()) + small.format(html.escape(token[1:].upper())))
        else:
            parts.append(small.format(html.escape(token.upper())))
    return "".join(parts)


def _line_height(paragraph: Block) -> str:
    """Reproduce the original baseline-to-baseline distance."""
    lines = paragraph.lines
    size = lines[0].spans[0].size
    if len(lines) < 2 or size <= 0:
        return "1.2"
    spacing = (lines[-1].spans[0].baseline - lines[0].spans[0].baseline) / (len(lines) - 1)
    return f"{spacing / size:.3f}"


def _recreate_links(page: pymupdf.Page, rect: pymupdf.Rect, runs: list[Run]) -> None:
    used: list[pymupdf.Rect] = []
    for run in runs:
        if not run.style.link or not run.text.strip():
            continue
        # search_for returns one rect per line when the text wraps.
        for hit in page.search_for(run.text.strip(), clip=rect):
            if any(hit.intersects(u) for u in used):
                continue
            used.append(hit)
            page.insert_link({"kind": pymupdf.LINK_URI, "from": hit, "uri": run.style.link})
