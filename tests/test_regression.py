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


@defect(2, "table rules taken as underlines and removed")
def test_table_rules_are_kept(fixture_paths):
    assert measured("bordered_table", "identity", fixture_paths)["drawings_lost"] == 0


@defect(3, "fonts with empty accented glyphs are reused")
def test_accented_letters_are_drawn(fixture_paths):
    assert measured("blank_accents", "pseudo", fixture_paths)["blank_chars"] == 0


@defect(5, "internal links are removed with the text")
def test_internal_links_are_kept(fixture_paths):
    assert measured("internal_links", "identity", fixture_paths)["internal_links_lost"] == 0


@defect(6, "rotated text is cut and rewritten horizontally")
def test_rotated_text_is_kept(fixture_paths):
    assert measured("rotated_text", "identity", fixture_paths)["rotated_lines_lost"] == 0


@pytest.mark.corpus
@defect(1, "insertion box too short: untranslated text shrinks")
@pytest.mark.parametrize("name", [d.name for d in corpus.CORPUS])
def test_untranslated_text_keeps_its_size(name):
    assert measured(name, "identity")["full_size_pct"] >= 98


@pytest.mark.corpus
@defect(3, "ligature glyphs of reused subset fonts are empty")
def test_ligatures_are_drawn():
    assert measured("nist", "identity")["blank_chars"] == 0


@pytest.mark.corpus
@defect(7, "duplicated ToUnicode tables inflate the output")
@pytest.mark.parametrize("name", [d.name for d in corpus.CORPUS])
def test_output_is_not_inflated(name):
    assert measured(name, "identity")["size_ratio"] <= 1.5
