"""End-to-end flow: read and analyze -> translate -> align -> layout -> draw."""

import logging
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .align import base_style, paragraph_runs, realign, styled_segments
from .analysis import Action, Block, Kind, analyze_page
from .analysis.continued import continued, share
from .analysis.tables import table_text, tables
from .fonts import FontLibrary
from .layout import Placement, TranslatedParagraph, layout_page
from .pdf.draw import draw_page
from .pdf.read import graphic_areas
from .terms import screen_names
from .translators import Translator, Unit, UnitTranslation
from .translators.checks import repair_tokens, token_problems
from .translators.form import restore_form, strip_affixes

log = logging.getLogger(__name__)


@dataclass
class Result:
    """What each stage produced, so it can be inspected and measured.

    The measurement harness (tools/measure.py) reads this instead of
    re-implementing the stages, so it measures exactly what the CLI runs.
    """

    pages: list[list[Block]] = field(default_factory=list)  # every block, kept ones too
    units: list[Unit] = field(default_factory=list)
    translations: list[UnitTranslation] = field(default_factory=list)
    translated: list[TranslatedParagraph] = field(default_factory=list)
    fonts: FontLibrary | None = None
    placements: list[Placement] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    # Pages left in the original language because a stage failed on them
    # (page index -> reason), and the warnings logged during the run.
    failures: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def fail(self, index: int, stage: str, error: Exception) -> None:
        self.failures[index] = f"{stage}: {type(error).__name__}: {error}"
        log.error("Page %d left untranslated (%s)", index + 1, self.failures[index])
        log.debug("Traceback", exc_info=error)

    @contextmanager
    def timed(self, stage: str):
        start = time.perf_counter()
        yield
        self.seconds[stage] = self.seconds.get(stage, 0.0) + time.perf_counter() - start


class DocumentError(Exception):
    """A document this tool cannot translate."""


def translate_pdf(source: Path, destination: Path, translator: Translator) -> Result:
    try:
        doc = pymupdf.open(source)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise DocumentError(f"not a readable PDF ({error})") from error
    if not doc.is_pdf:
        raise DocumentError("not a PDF")
    if doc.needs_pass:
        raise DocumentError("the PDF is password-protected")
    if not any(page.get_text("text").strip() for page in doc):
        # Scanned pages are images of text: there is nothing to translate
        # until OCR adds a text layer (e.g. `ocrmypdf`).
        raise DocumentError("the PDF has no text layer (scanned?); run OCR on it first")
    result = translate_document(doc, translator)
    with result.timed("save"):
        save(doc, destination)
    return result


def translate_document(doc: pymupdf.Document, translator: Translator) -> Result:
    """Translate `doc` in place.

    A page on which reading or drawing fails is left as it was and recorded
    in `Result.failures`; the other pages are still translated. Translation
    errors stop the run: they concern the whole document (the engine is down).
    """
    result = Result()
    with _collect_warnings(result.warnings):
        _translate_document(doc, translator, result)
    return result


def _translate_document(doc: pymupdf.Document, translator: Translator, result: Result) -> None:
    with result.timed("analyze"):
        for index, page in enumerate(doc):
            try:
                result.pages.append(analyze_page(page))
            except Exception as error:
                result.fail(index, "reading", error)
                result.pages.append([])
    paragraphs = [b for page_blocks in result.pages for b in page_blocks if b.action == Action.TRANSLATE]
    kept = sum(len(page_blocks) for page_blocks in result.pages) - len(paragraphs)
    log.info("%d pages, %d paragraphs to translate, %d blocks kept", len(doc), len(paragraphs), kept)

    prepared = []
    for paragraph in paragraphs:
        runs = paragraph_runs(paragraph)
        base = base_style(runs)
        prepared.append((paragraph, base, styled_segments(runs, base)))

    # The whole document goes in one call, in reading order, so the backend
    # can batch, deduplicate and use the surrounding text as context.
    result.units = []
    section = ""
    # A cell is translated knowing its whole table: header and rows.
    table_of = {
        id(cell): text
        for blocks in result.pages
        for cells in tables(blocks)
        if (text := table_text(cells))
        for cell in cells
    }
    # Names the document's screens show: kept as the device writes them.
    names = screen_names(
        [line.text for blocks in result.pages for b in blocks if b.kind == Kind.CODE for line in b.lines]
    )
    for paragraph, base, segments in prepared:
        if paragraph.kind == Kind.HEADING:
            section = paragraph.text
        labels = [s.text.strip() for s in segments if s.style.italic and not base.italic]
        result.units.append(
            Unit(
                paragraph.text,
                [s.text.strip() for s in segments],
                section,
                labels,
                names=[n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", paragraph.text)],
                table=table_of.get(id(paragraph), ""),
            )
        )
    with result.timed("translate"):
        # A paragraph cut by a page break is translated whole, then shared
        # back between its pieces.
        groups = _groups(len(paragraphs), continued(paragraphs))
        whole = [_joined([result.units[i] for i in group]) for group in groups]
        stripped = [strip_affixes(unit) for unit in whole]
        translations = translator.translate_units([unit for unit, _, _ in stripped])
        translations = [
            _repaired(unit, restore_form(unit.text, translation, lead, trail))
            for (unit, lead, trail), translation, unit in zip(stripped, translations, whole)
        ]
        by_index = {
            index: piece
            for group, translation in zip(groups, translations)
            for index, piece in zip(group, _shared(translation, [result.units[i] for i in group]))
        }
        result.translations = [by_index[i] for i in range(len(result.units))]

    with result.timed("realign"):
        for (paragraph, base, segments), translation in zip(prepared, result.translations):
            runs = realign(translation.text, base, segments, translation.segments)
            result.translated.append(TranslatedParagraph(paragraph, runs))
    by_paragraph = {id(t.source): t for t in result.translated}

    with result.timed("fonts"):
        result.fonts = FontLibrary.from_document(
            doc, _needed_chars(result.translated), _font_flags(paragraphs)
        )

    with result.timed("render"):
        # Drawing removes the original text first; a page that fails halfway
        # is restored from this copy.
        original = pymupdf.open("pdf", doc.tobytes())
        failed = []
        for index, (page, page_blocks) in enumerate(zip(doc, result.pages)):
            translated = [by_paragraph[id(b)] for b in page_blocks if b.action == Action.TRANSLATE]
            try:
                # Kept text (code, markers, rotated lines) is an obstacle too.
                placements = layout_page(
                    page.rect, translated, page_blocks, graphic_areas(page), result.fonts
                )
                draw_page(page, placements, result.fonts.archive)
                result.placements += placements
            except Exception as error:
                result.fail(index, "drawing", error)
                failed.append(index)
        for index in failed:
            doc.delete_page(index)
            doc.insert_pdf(original, from_page=index, to_page=index, start_at=index)


def _groups(count: int, chains: list[list[int]]) -> list[list[int]]:
    """Indices 0..count-1 in order, the pieces of each chain together."""
    chain_of = {chain[0]: chain for chain in chains}
    inside = {i for chain in chains for i in chain[1:]}
    return [chain_of.get(i, [i]) for i in range(count) if i not in inside]


def _joined(units: list[Unit]) -> Unit:
    if len(units) == 1:
        return units[0]
    first = units[0]
    return Unit(
        " ".join(u.text for u in units),
        [s for u in units for s in u.segments],
        first.section,
        [label for u in units for label in u.labels],
        names=list(dict.fromkeys(n for u in units for n in u.names)),
        table=first.table,
    )


def _shared(translation: UnitTranslation, units: list[Unit]) -> list[UnitTranslation]:
    """The translation of a joined paragraph, back as one per piece."""
    if len(units) == 1:
        return [translation]
    texts = share(translation.text, [len(u.text) for u in units])
    pieces, start = [], 0
    for unit, text in zip(units, texts):
        count = len(unit.segments)
        pieces.append(UnitTranslation(text, translation.segments[start : start + count]))
        start += count
    return pieces


def _repaired(unit: Unit, translation: UnitTranslation) -> UnitTranslation:
    """Numbers and signs the engine still changed after its retries, put
    back; what can't be repaired is reported."""
    text = repair_tokens(unit.text, translation.text)
    segments = [repair_tokens(s, t) for s, t in zip(unit.segments, translation.segments)]
    segments += translation.segments[len(segments) :]
    if problems := token_problems(unit.text, text):
        log.warning("Check translation of %r (%s): %r", unit.text[:50], "; ".join(problems), text[:80])
    return UnitTranslation(text, segments)


class _WarningCollector(logging.Handler):
    def __init__(self, messages: list[str]) -> None:
        super().__init__(logging.WARNING)
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno == logging.WARNING:  # errors are in Result.failures
            self.messages.append(record.getMessage())


@contextmanager
def _collect_warnings(messages: list[str]):
    """Copy the warnings logged by this package into `messages`."""
    logger = logging.getLogger("doc_translator")
    handler = _WarningCollector(messages)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def save(doc: pymupdf.Document, destination: Path) -> None:
    doc.subset_fonts()
    # garbage=4 also merges duplicate objects: every inserted paragraph adds
    # its own copy of the same ToUnicode table (675 copies on a 29-page
    # manual, making the file four times larger).
    doc.save(destination, garbage=4, deflate=True)


def _font_flags(paragraphs: list[Block]) -> dict[str, int]:
    """The PDF flags of each font, as set on most of its text."""
    weights: dict[str, Counter[int]] = defaultdict(Counter)
    for paragraph in paragraphs:
        for span in paragraph.spans:
            weights[span.font][span.flags] += len(span.text)
    return {font: counts.most_common(1)[0][0] for font, counts in weights.items()}


def _needed_chars(paragraphs) -> dict[str, set[str]]:
    """Characters each font must be able to draw in the translated document."""
    needed: dict[str, set[str]] = {}
    for paragraph in paragraphs:
        first_font = paragraph.source.spans[0].font
        needed.setdefault(first_font, set()).update(paragraph.source.prefix)
        for run in paragraph.runs:
            needed.setdefault(run.style.font, set()).update(run.text)
    return needed
