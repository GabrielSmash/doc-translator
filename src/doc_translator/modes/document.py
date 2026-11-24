"""Documents with a text layer (manuals, datasheets): read and analyze ->
translate -> align -> layout -> draw, keeping the layout."""

import logging
import re
from collections import Counter, defaultdict

import pymupdf

from ..align import base_style, paragraph_runs, realign, styled_segments
from ..analysis import Action, Block, Kind, analyze_page
from ..analysis.continued import continued, share
from ..analysis.tables import table_text, tables
from ..fonts import FontLibrary
from ..languages import LanguagePair
from ..layout import TranslatedParagraph, layout_page
from ..pdf.draw import draw_page
from ..pdf.read import graphic_areas
from ..result import Result, collect_warnings
from ..terms import screen_names
from ..translators import Translator, Unit, UnitTranslation
from ..translators.checks import repair_tokens, token_problems
from ..translators.form import restore_form, strip_affixes

log = logging.getLogger(__name__)


def translate_document(doc: pymupdf.Document, translator: Translator) -> Result:
    """Translate `doc` in place.

    A page on which reading or drawing fails is left as it was and recorded
    in `Result.failures`; the other pages are still translated. Translation
    errors stop the run: they concern the whole document (the engine is down).
    """
    result = Result()
    with collect_warnings(result.warnings):
        translate(doc, translator, result)
    return result


def translate(doc: pymupdf.Document, translator: Translator, result: Result) -> None:
    """The document process: its text pages, into `result`."""
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
        groups = result.groups = _groups(len(paragraphs), continued(paragraphs))
        whole = [_joined([result.units[i] for i in group]) for group in groups]
        stripped = [strip_affixes(unit) for unit in whole]
        translations = translator.translate_units([unit for unit, _, _ in stripped])
        languages = _languages(translator)
        translations = [
            _repaired(unit, restore_form(unit.text, translation, lead, trail), languages)
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


def _languages(translator) -> LanguagePair | None:
    """The language pair of a translating engine (the measuring engines,
    identity and pseudo, have none), through the cache that wraps it."""
    return getattr(translator, "languages", None) or getattr(getattr(translator, "translator", None), "languages", None)


def _repaired(unit: Unit, translation: UnitTranslation, languages: LanguagePair | None = None) -> UnitTranslation:
    """Numbers and signs the engine still changed after its retries, put
    back, and dates written in the target language; what can't be repaired
    is reported."""
    text = repair_tokens(unit.text, translation.text)
    if languages:
        text = languages.localize_dates(unit.text, text)
    segments = [repair_tokens(s, t) for s, t in zip(unit.segments, translation.segments)]
    segments += translation.segments[len(segments) :]
    if problems := token_problems(unit.text, text):
        log.warning("Check translation of %r (%s): %r", unit.text[:50], "; ".join(problems), text[:80])
    return UnitTranslation(text, segments)


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
