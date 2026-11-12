"""Stage 1: read a page into plain data, without interpreting it."""

import pymupdf

from ..analysis.model import Line, PageContent, Span, TextBlock

# Expand ligatures ("ﬁ" -> "fi") so the translator sees normal words, and join
# words hyphenated across line breaks.
TEXT_FLAGS = (
    pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
) | pymupdf.TEXT_DEHYPHENATE


def read_page(page: pymupdf.Page) -> PageContent:
    links = [
        (pymupdf.Rect(link["from"]), link_key(link))
        for link in page.get_links()
        if link["kind"] != pymupdf.LINK_NONE
    ]
    blocks = []
    # rawdict: the same as dict, plus the box of each character, needed to
    # split text where a link starts or ends.
    for b, block in enumerate(page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]):
        if block["type"] != 0:  # 1 = image block
            continue
        lines = []
        for n, raw in enumerate(block["lines"]):
            spans = [span for s in raw["spans"] for span in _spans(s, links)]
            if spans:
                lines.append(
                    Line(spans, pymupdf.Rect(raw["bbox"]), tuple(raw["dir"]), origin=(b, n))
                )
        blocks.append(TextBlock(pymupdf.Rect(block["bbox"]), lines))
    return PageContent(blocks=blocks, drawings=page.get_drawings(), links=links)


def link_key(link: dict) -> str:
    """What a link points to, as a string: the same for every piece of it,
    and enough for drawing to find the link again and recreate it."""
    if link.get("uri"):
        return link["uri"]
    fields = ("kind", "page", "to", "file", "name", "nameddest")
    return "#" + "|".join(str(link[f]) for f in fields if link.get(f) is not None)


def _spans(raw: dict, links: list[tuple[pymupdf.Rect, str]]) -> list[Span]:
    """The span, split where a link starts or ends inside it ("see the
    *configuration section* for..."), so the link can follow its words."""
    chars = raw["chars"]
    if not chars:
        return []
    keys = [_link_at(pymupdf.Rect(c["bbox"]), links) for c in chars]
    pieces: list[list[dict]] = [[chars[0]]]
    for previous, key, char in zip(keys, keys[1:], chars[1:]):
        if key == previous:
            pieces[-1].append(char)
        else:
            pieces.append([char])
    if len(pieces) == 1:
        return [_span(raw, "".join(c["c"] for c in chars), pymupdf.Rect(raw["bbox"]))]
    spans = []
    for piece in pieces:
        bbox = pymupdf.Rect(piece[0]["bbox"])
        for char in piece[1:]:
            bbox |= char["bbox"]
        spans.append(_span(raw, "".join(c["c"] for c in piece), bbox))
    return spans


def _link_at(rect: pymupdf.Rect, links: list[tuple[pymupdf.Rect, str]]) -> str | None:
    center = (rect.tl + rect.br) / 2
    return next((key for area, key in links if center in area), None)


def _span(raw: dict, text: str, bbox: pymupdf.Rect) -> Span:
    return Span(
        text=text,
        font=raw["font"],
        size=raw["size"],
        flags=raw["flags"],
        color=raw["color"],
        bbox=bbox,
        baseline=raw["origin"][1],
    )


# Vertical rules at least this long (points) bound text growing sideways.
MIN_COLUMN_RULE = 8.0


def graphic_areas(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Images, filled areas and column rules: places translated text must
    not grow into.

    Other drawings made only of strokes (horizontal rules, underlines) are
    left out: text sits between them, and line art is checked separately
    when grouping lines. Vertical rules are kept: they separate the columns
    of a table or form, and text growing sideways must stop at them.
    """
    rects = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
    for drawing in page.get_drawings():
        rect = pymupdf.Rect(drawing["rect"])
        only_strokes = all(item[0] == "l" for item in drawing["items"])
        if not only_strokes and rect.height >= 1.5 and rect.width >= 1.5:
            rects.append(rect)
        rects += column_rules(drawing)
    return rects


def column_rules(drawing: dict) -> list[pymupdf.Rect]:
    """Vertical rules of a drawing: lines, or rectangles thinner than 1.5 pt."""
    rules = []
    for item in drawing["items"]:
        if item[0] == "l":
            start, end = item[1], item[2]
            if abs(start.x - end.x) < 1 and abs(start.y - end.y) >= MIN_COLUMN_RULE:
                x = (start.x + end.x) / 2
                rules.append(pymupdf.Rect(x - 0.5, min(start.y, end.y), x + 0.5, max(start.y, end.y)))
        elif item[0] == "re":
            rect = pymupdf.Rect(item[1])
            if rect.width < 1.5 and rect.height >= MIN_COLUMN_RULE:
                rules.append(rect)
    return rules
