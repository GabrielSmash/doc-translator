"""End-to-end flow: extract -> translate -> realign styles -> render."""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .align import base_style, paragraph_runs, realign, styled_segments
from .extract import extract_paragraphs
from .fonts import FontLibrary
from .models import Paragraph
from .render import Placement, TranslatedParagraph, render_page
from .translators import Translator, Unit, UnitTranslation

log = logging.getLogger(__name__)


@dataclass
class Result:
    """What each stage produced, so it can be inspected and measured.

    The measurement harness (tools/measure.py) reads this instead of
    re-implementing the stages, so it measures exactly what the CLI runs.
    """

    pages: list[list[Paragraph]] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    translations: list[UnitTranslation] = field(default_factory=list)
    translated: list[TranslatedParagraph] = field(default_factory=list)
    fonts: FontLibrary | None = None
    placements: list[Placement] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def timed(self, stage: str):
        start = time.perf_counter()
        yield
        self.seconds[stage] = self.seconds.get(stage, 0.0) + time.perf_counter() - start


def translate_pdf(source: Path, destination: Path, translator: Translator) -> Result:
    doc = pymupdf.open(source)
    result = translate_document(doc, translator)
    with result.timed("save"):
        save(doc, destination)
    return result


def translate_document(doc: pymupdf.Document, translator: Translator) -> Result:
    """Translate `doc` in place."""
    result = Result()

    with result.timed("extract"):
        result.pages = [extract_paragraphs(page) for page in doc]
    paragraphs = [p for page_paragraphs in result.pages for p in page_paragraphs]
    log.info("%d pages, %d paragraphs", len(doc), len(paragraphs))

    prepared = []
    for paragraph in paragraphs:
        runs = paragraph_runs(paragraph)
        base = base_style(runs)
        prepared.append((paragraph, base, styled_segments(runs, base)))

    # The whole document goes in one call, in reading order, so the backend
    # can batch, deduplicate and use the surrounding text as context.
    result.units = [Unit(p.text, [s.text.strip() for s in segments]) for p, _, segments in prepared]
    with result.timed("translate"):
        result.translations = translator.translate_units(result.units)

    with result.timed("realign"):
        for (paragraph, base, segments), translation in zip(prepared, result.translations):
            runs = realign(translation.text, base, segments, translation.segments)
            result.translated.append(TranslatedParagraph(paragraph, runs))
    by_paragraph = {id(t.source): t for t in result.translated}

    with result.timed("fonts"):
        result.fonts = FontLibrary.from_document(doc, _needed_chars(result.translated))

    with result.timed("render"):
        for page, page_paragraphs in zip(doc, result.pages):
            translated = [by_paragraph[id(p)] for p in page_paragraphs]
            result.placements += render_page(page, translated, result.fonts)
    return result


def save(doc: pymupdf.Document, destination: Path) -> None:
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
