"""Measured quality of the whole pipeline, against tests/baseline.json.

- Regression tests: no metric may get worse than the stored baseline.
  After an improvement, store the new numbers with
  `uv run python -m tools.measure --fixtures --corpus --update-baseline`.
- Defect tests: one per known defect (docs/adr/0001-...). They are expected
  to fail; strict=True makes the suite fail as soon as a fix makes one
  pass, so the marker gets removed and the test keeps the fix in place.
"""

import pytest

from tools import corpus, fixtures, measure

ENGINES = ("identity", "pseudo")
BASELINE = measure.load_baseline()
_cache: dict[str, dict] = {}


@pytest.fixture(scope="session")
def fixture_paths(tmp_path_factory):
    return fixtures.build(tmp_path_factory.mktemp("fixtures"))


def measured(name: str, engine: str, fixture_paths=None) -> dict:
    key = f"{name}/{engine}"
    if key not in _cache:
        if name in fixtures.FIXTURES:
            path, pages = fixture_paths[name], None
        else:
            document = next(d for d in corpus.CORPUS if d.name == name)
            path, pages = corpus.fetch(document), document.pages
        _cache[key] = measure.measure(path, measure.make_engine(engine), engine, pages)["metrics"]
    return _cache[key]


# -- regressions -----------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("name", sorted(fixtures.FIXTURES))
def test_fixture_does_not_regress(name, engine, fixture_paths):
    key = f"{name}/{engine}"
    assert key in BASELINE, f"no baseline for {key}; run tools.measure --update-baseline"
    assert measure.regressions(measured(name, engine, fixture_paths), BASELINE[key]) == []


@pytest.mark.corpus
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("name", [d.name for d in corpus.CORPUS])
def test_corpus_does_not_regress(name, engine):
    key = f"{name}/{engine}"
    assert key in BASELINE, f"no baseline for {key}; run tools.measure --update-baseline"
    assert measure.regressions(measured(name, engine), BASELINE[key]) == []


def test_measurement_is_deterministic(fixture_paths):
    path = fixture_paths["bordered_table"]
    first = measure.measure(path, measure.Pseudo(), "pseudo")["metrics"]
    second = measure.measure(path, measure.Pseudo(), "pseudo")["metrics"]
    assert first == second


def test_pseudo_engine_keeps_segments_inside_their_paragraph():
    from doc_translator.translators import Unit

    unit = Unit("The card provides the main results via VistaLINK.", ["main results", "VistaLINK"])
    result = measure.Pseudo().translate_units([unit])[0]
    assert result.text != unit.text
    assert all(segment in result.text for segment in result.segments)


# -- known defects (docs/adr/0001-pipeline-audit-and-correction-plan.md) --------------


def defect(number: int, description: str):
    return pytest.mark.xfail(reason=f"defect {number}: {description}", strict=True)


def test_table_rules_are_kept(fixture_paths):
    """Defect 2, fixed: rules that run past the text are not underlines."""
    assert measured("bordered_table", "identity", fixture_paths)["drawings_lost"] == 0


def test_table_rows_stay_separate(fixture_paths):
    """With its rules kept, each row of a bordered table is its own paragraph."""
    import pymupdf

    from doc_translator.analysis import analyze_page

    page = pymupdf.open(fixture_paths["bordered_table"])[0]
    texts = [p.text for p in analyze_page(page)]
    for label in ("Hostname", "IP Address", "Netmask", "Gateway", "Broadcast"):
        assert label in texts


def test_ligatures_and_symbols_are_drawn(fixture_paths):
    """Defect 3 (ligatures): no blank glyph, the original font kept, and the
    text searchable as written ("office", not the ligature "oﬃce")."""
    import pymupdf

    metrics = measured("blank_ligatures", "identity", fixture_paths)
    assert metrics["blank_chars"] == 0
    assert metrics["original_font_chars_pct"] > 90

    from doc_translator.pipeline import translate_document

    doc = pymupdf.open(fixture_paths["blank_ligatures"])
    translate_document(doc, measure.Identity())
    assert doc[0].search_for("office") and doc[0].search_for("Find")


def test_accented_letters_are_drawn(fixture_paths):
    """Defect 3, fixed: fonts with empty accented glyphs are no longer reused."""
    assert measured("blank_accents", "pseudo", fixture_paths)["blank_chars"] == 0


@pytest.mark.parametrize("engine", ENGINES)
def test_links_follow_the_translated_words(engine, fixture_paths):
    """Defect 5, fixed: links inside a line, internal ones too, are recreated
    over the translation (or put back where they were)."""
    metrics = measured("internal_links", engine, fixture_paths)
    assert metrics["internal_links_lost"] == metrics["uri_links_lost"] == 0


def test_text_on_a_dark_band_stays_inside_it(fixture_paths):
    """Defect 10, fixed: white text on a black band shrinks to stay on it
    instead of running onto the white page, where it would be invisible."""
    assert measured("dark_band", "pseudo", fixture_paths)["blank_chars"] == 0


def test_rotated_text_is_kept(fixture_paths):
    """Defect 6, fixed: rotated lines are kept as they are (action keep)."""
    assert measured("rotated_text", "identity", fixture_paths)["rotated_lines_lost"] == 0


@pytest.mark.corpus
@pytest.mark.parametrize("name", ["arxiv", "irs-w4", "nist"])
def test_untranslated_text_keeps_its_size(name):
    """Defect 1, fixed: text is bounded by the letters of its neighbors and
    by baselines, not by the boxes around them."""
    assert measured(name, "identity")["full_size_pct"] >= 98


@pytest.mark.corpus
def test_ligatures_are_drawn_on_word_documents():
    """Defect 3 (ligatures), fixed: ligature codes are removed from reused fonts."""
    assert measured("nist", "identity")["blank_chars"] == 0


@pytest.mark.corpus
@pytest.mark.parametrize("name", ["arxiv", "irs-w4", "nist"])
def test_output_is_not_inflated(name):
    """Defect 7, fixed: duplicate ToUnicode tables are merged on save. The
    paragraphs of a page are drawn as one piece of content, not one per
    paragraph (681 on the IRS form)."""
    assert measured(name, "identity")["size_ratio"] <= 1.5


# -- defect classes (tools/defects.py) -------------------------------------------
# One fixture reproduces each class; the whole corpus is counted in the baseline.


def defect_class(cause: str):
    return pytest.mark.xfail(reason=f"open: {cause}", strict=True)


@pytest.mark.parametrize("engine", ENGINES)
def test_a_line_written_word_by_word_is_one_line(engine, fixture_paths):
    """C1, fixed: pieces of one line are joined before forming paragraphs."""
    metrics = measured("split_line", engine, fixture_paths)
    assert metrics["split_lines"] == metrics["overlapping_text"] == 0


@defect_class("C2, text leaves its cell")
def test_table_text_stays_in_its_cell(fixture_paths):
    metrics = measured("ruled_table", "pseudo", fixture_paths)
    assert metrics["crossed_rules"] == 0


@defect_class("C3, alignment of one-line cells")
def test_centered_cells_stay_centered(fixture_paths):
    assert measured("ruled_table", "pseudo", fixture_paths)["lost_alignment"] == 0


@pytest.mark.parametrize("engine", ENGINES)
def test_page_numbers_stay_on_their_entry(engine, fixture_paths):
    """C4, fixed: leaders are drawn where layout measured the title, not
    where reading the drawn page back finds text."""
    assert measured("toc_leaders", engine, fixture_paths)["misplaced_leaders"] == 0


@defect_class("C5, cells taken for headings")
def test_table_header_cells_are_not_headings(fixture_paths):
    assert measured("bordered_table", "identity", fixture_paths)["headings_in_cells"] == 0
