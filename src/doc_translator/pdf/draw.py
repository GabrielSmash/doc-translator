"""Stage 6: replace the original text of a page with the placed translations.

Everything about where and how the text goes was decided by layout; this
module only executes it.
"""

import io
import logging

import pymupdf

from ..analysis.model import Leader
from ..layout import Placement, base14, text_width
from .read import link_key

log = logging.getLogger(__name__)

# Shrink redaction boxes vertically so they don't catch glyphs of the lines
# right above/below (line bboxes overlap slightly).
REDACT_INSET = 0.2


def draw_page(page: pymupdf.Page, placements: list[Placement], archive: pymupdf.Archive) -> None:
    # Saved first: removing the text also removes the links over it.
    links = [link for link in page.get_links() if link["kind"] != pymupdf.LINK_NONE]
    targets = {link_key(link): link for link in links}
    _remove_original_text(page, placements)
    if placements:
        # All the paragraphs go in one piece of content. insert_htmlbox adds
        # one per paragraph, with its own resources: 681 on the 4-page IRS
        # form, most of its output size.
        page.show_pdf_page(page.rect, _compose(page.rect, placements, archive), 0)
    recreated: set[str] = set()
    for placement in placements:
        recreated |= _finish(page, placement, targets)
    _restore_links(page, links, recreated)


def _compose(
    page_rect: pymupdf.Rect, placements: list[Placement], archive: pymupdf.Archive
) -> pymupdf.Document:
    """A page holding only the translated paragraphs, each written as
    insert_htmlbox would: the HTML engine shrinks it until it fits its box."""
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    device = writer.begin_page(pymupdf.Rect(0, 0, page_rect.width, page_rect.height))
    for placement in placements:
        rect = placement.rect
        # The same story and margin as insert_htmlbox, which layout measured.
        story = pymupdf.Story(placement.html, user_css="body {margin:1px;}" + placement.css, archive=archive)
        fit = story.fit_scale(
            pymupdf.Rect(0, 0, rect.width, rect.height),
            scale_min=1,
            flags=pymupdf.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW,
        )
        story.reset()
        story.place(fit.rect)
        scale = 1 / fit.parameter
        story.draw(device, pymupdf.Matrix(scale, scale).pretranslate(0, 0) * pymupdf.Matrix(1, 0, 0, 1, rect.x0, rect.y0))
    writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buffer.getvalue())


def _remove_original_text(page: pymupdf.Page, placements: list[Placement]) -> None:
    sources = [p.paragraph.source for p in placements]
    # Read before any redaction: span.underline_drawings index this list.
    drawings = page.get_drawings()

    # Links over translated text are recreated at the new text position.
    text_area = [s.bbox for s in sources]
    for link in page.get_links():
        if any(pymupdf.Rect(link["from"]).intersects(r) for r in text_area):
            page.delete_link(link)

    for source in sources:
        for line in source.lines:
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
    underline_drawings = {d for s in sources for span in s.spans for d in span.underline_drawings}
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


def _finish(page: pymupdf.Page, placement: Placement, targets: dict[str, dict]) -> set[str]:
    """Links and leader dots of a written paragraph; return the links recreated."""
    source = placement.paragraph.source
    rect = placement.rect
    if not placement.fitted:
        log.warning("Page %d: text did not fit in %s", page.number + 1, rect)
    elif placement.scale < 1:
        log.debug("Page %d: %r scaled to %.0f%%", page.number + 1, source.text[:30], placement.scale * 100)

    recreated = _recreate_links(page, rect, placement.paragraph.runs, targets)
    if source.leader:
        _draw_leader(page, rect, source.leader)
    return recreated


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

    fontname = base14(leader)
    color = tuple(((leader.color >> shift) & 0xFF) / 255 for shift in (16, 8, 0))
    number_x = leader.right - text_width(leader.number, leader)
    dot = text_width(".", leader)
    count = int((number_x - end) / dot) - 2
    if count > 0:
        page.insert_text(
            (end + dot, baseline), "." * count, fontname=fontname, fontsize=leader.size, color=color
        )
    page.insert_text(
        (number_x, baseline), leader.number, fontname=fontname, fontsize=leader.size, color=color
    )


def _recreate_links(
    page: pymupdf.Page, rect: pymupdf.Rect, runs, targets: dict[str, dict]
) -> set[str]:
    recreated = set()
    used: list[pymupdf.Rect] = []
    for run in runs:
        target = targets.get(run.style.link)
        if target is None or not run.text.strip():
            continue
        # search_for returns one rect per line when the text wraps.
        for hit in page.search_for(run.text.strip(), clip=rect):
            if any(hit.intersects(u) for u in used):
                continue
            used.append(hit)
            page.insert_link(_link_at(target, hit))
            recreated.add(run.style.link)
    return recreated


def _restore_links(page: pymupdf.Page, links: list[dict], recreated: set[str]) -> None:
    """Put back, where they were, the links that are gone and were not
    recreated over the translation: removed with the text they covered, or
    whose words could not be found in the translation."""
    present = {(link_key(link), _rounded(link["from"])) for link in page.get_links()}
    for link in links:
        key = link_key(link)
        if key not in recreated and (key, _rounded(link["from"])) not in present:
            page.insert_link(_link_at(link, link["from"]))


def _link_at(link: dict, rect: pymupdf.Rect) -> dict:
    copy = {k: v for k, v in link.items() if k not in ("xref", "id")}
    copy["from"] = pymupdf.Rect(rect)
    return copy


def _rounded(rect: pymupdf.Rect) -> tuple[int, ...]:
    return tuple(round(v) for v in rect)
