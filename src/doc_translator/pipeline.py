"""End-to-end flow: extract -> translate -> realign styles -> render."""

import logging
from pathlib import Path

import pymupdf

from .align import base_style, paragraph_runs, realign, styled_segments
from .extract import extract_paragraphs
from .fonts import FontLibrary
from .render import TranslatedParagraph, render_page
from .translators import Translator, Unit

log = logging.getLogger(__name__)


def translate_pdf(source: Path, destination: Path, translator: Translator) -> None:
    doc = pymupdf.open(source)

    pages = [extract_paragraphs(page) for page in doc]
    paragraphs = [p for page_paragraphs in pages for p in page_paragraphs]
    log.info("%d pages, %d paragraphs", len(doc), len(paragraphs))

    prepared = []
    for paragraph in paragraphs:
        runs = paragraph_runs(paragraph)
        base = base_style(runs)
        prepared.append((paragraph, base, styled_segments(runs, base)))

    # The whole document goes in one call, in reading order, so the backend
    # can batch, deduplicate and use the surrounding text as context.
    units = [Unit(p.text, [s.text.strip() for s in segments]) for p, _, segments in prepared]
    results = translator.translate_units(units)

    translated: dict[int, TranslatedParagraph] = {}
    for (paragraph, base, segments), result in zip(prepared, results):
        runs = realign(result.text, base, segments, result.segments)
        translated[id(paragraph)] = TranslatedParagraph(paragraph, runs)

    fonts = FontLibrary.from_document(doc, _needed_chars(translated.values()))

    for page, page_paragraphs in zip(doc, pages):
        render_page(page, [translated[id(p)] for p in page_paragraphs], fonts)

    doc.subset_fonts()
    doc.save(destination, garbage=3, deflate=True)


def _needed_chars(paragraphs) -> dict[str, set[str]]:
    """Characters each font must be able to draw in the translated document."""
    needed: dict[str, set[str]] = {}
    for paragraph in paragraphs:
        first_font = paragraph.source.spans[0].font
        needed.setdefault(first_font, set()).update(paragraph.source.prefix)
        for run in paragraph.runs:
            needed.setdefault(run.style.font, set()).update(run.text)
    return needed
