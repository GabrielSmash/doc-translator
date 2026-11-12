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
        (pymupdf.Rect(link["from"]), link["uri"]) for link in page.get_links() if link.get("uri")
    ]
    blocks = []
    for b, block in enumerate(page.get_text("dict", flags=TEXT_FLAGS)["blocks"]):
        if block["type"] != 0:  # 1 = image block
            continue
        lines = []
        for n, raw in enumerate(block["lines"]):
            spans = [_span(s) for s in raw["spans"] if s["text"]]
            if spans:
                lines.append(
                    Line(spans, pymupdf.Rect(raw["bbox"]), tuple(raw["dir"]), origin=(b, n))
                )
        blocks.append(TextBlock(pymupdf.Rect(block["bbox"]), lines))
    return PageContent(blocks=blocks, drawings=page.get_drawings(), links=links)


def _span(raw: dict) -> Span:
    return Span(
        text=raw["text"],
        font=raw["font"],
        size=raw["size"],
        flags=raw["flags"],
        color=raw["color"],
        bbox=pymupdf.Rect(raw["bbox"]),
        baseline=raw["origin"][1],
    )
