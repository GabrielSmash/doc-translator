"""End-to-end flow: extract -> translate -> realign styles -> render."""

import logging
from pathlib import Path

import pymupdf

from .align import base_style, paragraph_runs, realign, styled_segments
from .extract import extract_paragraphs
from .render import TranslatedParagraph, render_page
from .translators import Translator

log = logging.getLogger(__name__)


def translate_pdf(source: Path, destination: Path, translator: Translator) -> None:
    doc = pymupdf.open(source)

    pages = [extract_paragraphs(page) for page in doc]
    paragraphs = [p for page_paragraphs in pages for p in page_paragraphs]
    log.info("%d pages, %d paragraphs", len(doc), len(paragraphs))

    # Everything is sent to the translator in a single call so the backend can
    # batch and deduplicate across the whole document.
    prepared = []
    texts: list[str] = []
    for paragraph in paragraphs:
        runs = paragraph_runs(paragraph)
        base = base_style(runs)
        segments = styled_segments(runs, base)
        prepared.append((paragraph, base, segments))
        texts.append(paragraph.text)
        texts.extend(segment.text.strip() for segment in segments)

    translations = iter(translator.translate(texts))

    translated: dict[int, TranslatedParagraph] = {}
    for paragraph, base, segments in prepared:
        translation = next(translations)
        segment_translations = [next(translations) for _ in segments]
        runs = realign(translation, base, segments, segment_translations)
        translated[id(paragraph)] = TranslatedParagraph(paragraph, runs)

    for page, page_paragraphs in zip(doc, pages):
        render_page(page, [translated[id(p)] for p in page_paragraphs])

    doc.subset_fonts()
    doc.save(destination, garbage=3, deflate=True)
