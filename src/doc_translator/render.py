"""Replace the original text of a page with its translation."""

import html
import logging
from dataclasses import dataclass

import pymupdf

from .align import Run
from .models import Paragraph

log = logging.getLogger(__name__)

SERIF_HINTS = ("times", "serif", "roman", "charis", "georgia", "garamond", "cambria", "tiro")
MONO_HINTS = ("mono", "courier", "consol", "code")

# Shrink redaction boxes vertically so they don't catch glyphs of the lines
# right above/below (line bboxes overlap slightly).
REDACT_INSET = 0.2
# Minimum distance kept between the grown text box and the next element.
GAP = 2.0


@dataclass
class TranslatedParagraph:
    source: Paragraph
    runs: list[Run]


def render_page(page: pymupdf.Page, paragraphs: list[TranslatedParagraph]) -> None:
    obstacles = _obstacles(page, paragraphs)
    _remove_original_text(page, paragraphs)
    for paragraph in paragraphs:
        _insert(page, paragraph, obstacles)


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

    # Second pass: drop the vector strokes that underlined the original text.
    # They are redrawn by CSS under the translated text.
    underlined = [s for p in paragraphs for s in p.source.spans if s.underline]
    for span in underlined:
        zone = pymupdf.Rect(
            span.bbox.x0 + 1, span.baseline - 0.5, span.bbox.x1 - 1, span.baseline + 0.3 * span.size
        )
        page.add_redact_annot(zone)
    if underlined:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
            text=pymupdf.PDF_REDACT_TEXT_NONE,
        )


def _insert(page: pymupdf.Page, paragraph: TranslatedParagraph, obstacles: list[pymupdf.Rect]) -> None:
    source = paragraph.source
    rect = _available_rect(page, source, obstacles)
    body = html.escape(source.prefix) + "".join(_run_html(run) for run in paragraph.runs)
    html_text = f"<p>{body}</p>"
    # Explicit selectors: the HTML engine's default `p` margins (1em) would
    # win over a `*` rule.
    css = f"body, p {{ margin: 0; padding: 0; }} p {{ line-height: {_line_height(source)}; }}"

    # Align the first baseline with the original one. The offset depends on
    # the font metrics used by the HTML engine, so it is measured on a scratch
    # page instead of computed.
    baseline = _first_baseline(page.rect, rect, html_text, css)
    if baseline is not None:
        shift = source.lines[0].spans[0].baseline - baseline
        rect += (0, shift, 0, shift)

    spare_height, scale = page.insert_htmlbox(rect, html_text, css=css)
    if spare_height < 0:
        log.warning("Page %d: text did not fit in %s", page.number + 1, rect)
    elif scale < 1:
        log.debug("Page %d: %r scaled to %.0f%%", page.number + 1, source.text[:30], scale * 100)

    _recreate_links(page, rect, paragraph.runs)


def _available_rect(
    page: pymupdf.Page, paragraph: Paragraph, obstacles: list[pymupdf.Rect]
) -> pymupdf.Rect:
    """Grow the paragraph box into the empty space around it.

    Single-line paragraphs (titles, list items) may grow to the right, since
    their width says nothing about the column width. Every paragraph may grow
    downward until the next element.
    """
    rect = pymupdf.Rect(paragraph.bbox)
    others = [o for o in obstacles if not o.contains(rect) and o != paragraph.bbox]
    left_margin = min((o.x0 for o in obstacles), default=rect.x0)
    top_margin = min((o.y0 for o in obstacles), default=rect.y0)

    if len(paragraph.lines) == 1:
        right = page.rect.x1 - left_margin
        for o in others:
            if o.x0 >= rect.x1 - 1 and o.y0 < rect.y1 and o.y1 > rect.y0:
                right = min(right, o.x0 - GAP)
        rect.x1 = max(rect.x1, right)

    bottom = page.rect.y1 - top_margin
    for o in others:
        is_below = o.y0 > rect.y0 + rect.height / 2
        if is_below and o.x0 < rect.x1 and o.x1 > rect.x0:
            bottom = min(bottom, o.y0 - GAP)
    rect.y1 = max(rect.y1, bottom)
    return rect


def _first_baseline(
    page_rect: pymupdf.Rect, rect: pymupdf.Rect, html_text: str, css: str
) -> float | None:
    scratch = pymupdf.open()
    page = scratch.new_page(width=page_rect.width, height=page_rect.height)
    page.insert_htmlbox(rect, html_text, css=css)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                return span["origin"][1]
    return None


def _run_html(run: Run) -> str:
    style = run.style
    css = [
        f"font-family: {_font_family(style.font)}",
        f"font-size: {style.size}px",
        f"color: #{style.color:06x}",
        f"font-weight: {'bold' if style.bold else 'normal'}",
        f"font-style: {'italic' if style.italic else 'normal'}",
    ]
    if style.underline:
        css.append("text-decoration: underline")
    return f'<span style="{"; ".join(css)}">{html.escape(run.text)}</span>'


def _font_family(font: str) -> str:
    name = font.lower()
    if any(hint in name for hint in MONO_HINTS):
        return "monospace"
    if any(hint in name for hint in SERIF_HINTS):
        return "serif"
    return "sans-serif"


def _line_height(paragraph: Paragraph) -> str:
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
