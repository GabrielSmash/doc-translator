"""Cache, per-page isolation and the run report."""

import json

import pymupdf
import pytest

from doc_translator import pipeline
from doc_translator.pipeline import DocumentError, translate_document, translate_pdf
from doc_translator.report import Report
from doc_translator.translators.base import Unit, UnitTranslation
from doc_translator.translators.cache import CachedTranslator, TranslationCache
from tools.measure import Pseudo


class Counting:
    """Uppercases text and counts the units it was asked to translate."""

    cache_id = "counting"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        self.calls.append(len(units))
        return [UnitTranslation(u.text.upper(), [s.upper() for s in u.segments]) for u in units]


def three_pages() -> pymupdf.Document:
    doc = pymupdf.open()
    for number in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"This is page number {number} of the document.", fontsize=11)
    return pymupdf.open("pdf", doc.tobytes())


# -- cache -------------------------------------------------------------------------


def test_cache_translates_each_unit_once(tmp_path):
    cache = TranslationCache(tmp_path / "cache.sqlite")
    engine = Counting()
    first = [Unit("Hello world", ["world"]), Unit("Good morning")]

    assert CachedTranslator(engine, cache).translate_units(first)[0].segments == ["WORLD"]
    again = CachedTranslator(engine, cache).translate_units([*first, Unit("New text")])

    assert engine.calls == [2, 1]  # the second run only sent the new unit
    assert [t.text for t in again] == ["HELLO WORLD", "GOOD MORNING", "NEW TEXT"]


def test_cache_is_kept_on_disk_and_separated_by_configuration(tmp_path):
    path = tmp_path / "cache.sqlite"
    CachedTranslator(Counting(), TranslationCache(path)).translate_units([Unit("Hello")])

    reopened = Counting()
    CachedTranslator(reopened, TranslationCache(path)).translate_units([Unit("Hello")])
    assert reopened.calls == []

    other = Counting()
    other.cache_id = "counting with another glossary"
    CachedTranslator(other, TranslationCache(path)).translate_units([Unit("Hello")])
    assert other.calls == [1]


def test_progress_store_is_given_to_engines_that_resume(tmp_path):
    class Resumable(Counting):
        def keep_progress(self, store):
            self.store = store

    engine = Resumable()
    CachedTranslator(engine, TranslationCache(tmp_path / "cache.sqlite"))
    engine.store.put_many({"Hello": "Olá"})
    assert engine.store.get_many(["Hello", "Bye"]) == {"Hello": "Olá"}


# -- per-page isolation --------------------------------------------------------------


def test_page_failing_to_draw_is_kept_and_reported(monkeypatch):
    original_draw = pipeline.draw_page

    def draw(page, placements, archive):
        if page.number == 1:
            raise ValueError("broken page")
        return original_draw(page, placements, archive)

    monkeypatch.setattr(pipeline, "draw_page", draw)
    doc = three_pages()
    result = translate_document(doc, Pseudo())

    assert list(result.failures) == [1]
    assert "broken page" in result.failures[1]
    assert len(doc) == 3
    assert "page number 2" in doc[1].get_text()  # restored, untranslated
    assert "page number 1" not in doc[0].get_text()  # translated


def test_page_failing_to_read_is_kept_and_reported(monkeypatch):
    original_analyze = pipeline.analyze_page

    def analyze(page):
        if page.number == 0:
            raise ValueError("unreadable")
        return original_analyze(page)

    monkeypatch.setattr(pipeline, "analyze_page", analyze)
    doc = three_pages()
    result = translate_document(doc, Pseudo())

    assert list(result.failures) == [0]
    assert "page number 1" in doc[0].get_text()
    assert "page number 3" not in doc[2].get_text()


def test_unusable_documents_are_refused(tmp_path):
    scanned = pymupdf.open()
    scanned.new_page()
    scanned.save(tmp_path / "scanned.pdf")
    (tmp_path / "text.pdf").write_text("not a pdf")

    with pytest.raises(DocumentError, match="no text layer"):
        translate_pdf(tmp_path / "scanned.pdf", tmp_path / "out.pdf", Counting())
    with pytest.raises(DocumentError):
        translate_pdf(tmp_path / "text.pdf", tmp_path / "out.pdf", Counting())


# -- report ----------------------------------------------------------------------------


def test_report_counts_pages_failures_and_fonts(monkeypatch, tmp_path):
    def draw(page, placements, archive):
        raise ValueError("broken page")

    monkeypatch.setattr(pipeline, "draw_page", draw)
    result = translate_document(three_pages(), Counting())
    report = Report.from_result(result, tmp_path / "in.pdf", tmp_path / "out.pdf")

    assert [p.page for p in report.failed_pages] == [1, 2, 3]
    assert report.chars_translated == report.chars > 0
    assert report.fonts_replaced == ["Helvetica"]  # base-14: never embedded
    assert "Page 2 left untranslated: drawing: ValueError: broken page" in report.summary()

    report.write(tmp_path / "report.json")
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["pages"][0]["error"].endswith("broken page")


def test_warnings_logged_during_the_run_are_reported():
    class Warns(Counting):
        def translate_units(self, units):
            import logging

            logging.getLogger("doc_translator.translators.x").warning("Numbers changed in %r", "x")
            return super().translate_units(units)

    result = translate_document(three_pages(), Warns())
    assert result.warnings == ["Numbers changed in 'x'"]
