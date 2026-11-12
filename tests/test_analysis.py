"""Stage 2: every line ends up in one block, with a kind and an action."""

import pymupdf
import pytest

from doc_translator.analysis import Action, Kind, analyze_page
from doc_translator.pdf.read import TEXT_FLAGS
from tools import corpus, fixtures


def unaccounted(page: pymupdf.Page) -> list[str]:
    """Problems with how the lines of `page` are distributed into blocks."""
    expected = {
        (b, n)
        for b, block in enumerate(page.get_text("dict", flags=TEXT_FLAGS)["blocks"])
        for n, line in enumerate(block.get("lines", []))
        if "".join(s["text"] for s in line["spans"]).strip()
    }
    seen: dict[int, int] = {}
    found = set()
    for index, block in enumerate(analyze_page(page)):
        assert block.lines, f"empty {block.kind} block"
        for line in block.lines:
            if id(line) in seen:
                return [f"line {line.text!r} in blocks {seen[id(line)]} and {index}"]
            seen[id(line)] = index
            found.add(line.origin)
    return [f"page {page.number + 1}: line {origin} in no block" for origin in sorted(expected - found)]


@pytest.fixture(scope="module")
def fixture_paths(tmp_path_factory):
    return fixtures.build(tmp_path_factory.mktemp("fixtures"))


@pytest.mark.parametrize("name", sorted(fixtures.FIXTURES))
def test_every_line_of_the_fixtures_is_in_one_block(name, fixture_paths):
    for page in pymupdf.open(fixture_paths[name]):
        assert unaccounted(page) == []


@pytest.mark.corpus
@pytest.mark.parametrize("name", [d.name for d in corpus.CORPUS])
def test_every_line_of_the_corpus_is_in_one_block(name):
    document = next(d for d in corpus.CORPUS if d.name == name)
    for page in pymupdf.open(corpus.fetch(document)):
        assert unaccounted(page) == []


# -- kinds and actions ---------------------------------------------------------------


def page_with(*items) -> pymupdf.Page:
    """A page with `(text, point, options)` items written on it."""
    doc = pymupdf.open()
    page = doc.new_page()
    for text, point, options in items:
        page.insert_text(point, text, **options)
    return pymupdf.open("pdf", doc.tobytes())[0]


def by_text(page) -> dict:
    return {b.text: b for b in analyze_page(page)}


def test_code_markers_and_rotated_text_are_kept():
    page = page_with(
        ("Run the following command:", (72, 72), {}),
        ("ping 192.168.0.1", (72, 90), {"fontname": "cour"}),
        ("-", (72, 130), {}),
        ("First item of a list.", (100, 130), {}),
        ("Axis label", (40, 400), {"rotate": 90}),
    )
    blocks = by_text(page)
    assert (blocks["ping 192.168.0.1"].kind, blocks["ping 192.168.0.1"].action) == (Kind.CODE, Action.KEEP)
    assert (blocks["-"].kind, blocks["-"].action) == (Kind.MARKER, Action.KEEP)
    assert (blocks["Axis label"].kind, blocks["Axis label"].action) == (Kind.ROTATED, Action.KEEP)
    assert blocks["Run the following command:"].action == Action.TRANSLATE


def test_kinds_of_translated_blocks():
    page = page_with(
        ("Installation", (72, 72), {"fontsize": 16}),
        ("The device is installed in a rack with four screws.", (72, 100), {}),
        ("Figure 3: Front panel of the device", (72, 130), {}),
        ("1. Remove the cover.", (72, 160), {}),
        ("Overview ........ 12", (72, 190), {}),
        ("Pin", (72, 230), {}),
        ("Name", (200, 230), {}),
        ("Function", (330, 230), {}),
    )
    kinds = {text: block.kind for text, block in by_text(page).items()}
    assert kinds["Installation"] == Kind.HEADING
    assert kinds["The device is installed in a rack with four screws."] == Kind.PARAGRAPH
    assert kinds["Figure 3: Front panel of the device"] == Kind.CAPTION
    assert kinds["Remove the cover."] == Kind.LIST_ITEM
    assert kinds["Overview"] == Kind.TOC_ENTRY
    assert kinds["Pin"] == kinds["Name"] == kinds["Function"] == Kind.TABLE_CELL
