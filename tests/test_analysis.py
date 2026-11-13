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
            found.update(line.origins)
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


def test_footnotes_are_separate_blocks_with_raised_marks():
    page = page_with(
        ("1", (72, 700), {"fontsize": 6}),
        (" https://example.com/first-reference", (75, 703), {"fontsize": 9}),
        ("2", (72, 712), {"fontsize": 6}),
        (" Any mention of commercial products is for information only.", (75, 715), {"fontsize": 9}),
    )
    notes = [b for b in analyze_page(page) if b.text[0] in "12"]
    assert [b.text[:2] for b in notes] == ["1 ", "2 "]
    assert all(b.spans[0].superscript for b in notes)


def test_reference_numbers_are_kept_out_of_the_translation():
    page = page_with(
        ("[33] Nitish Srivastava and Geoffrey Hinton. Dropout.", (72, 72), {}),
        ("as in ByteNet, the Extended Neural GPU and the models of", (72, 120), {}),
        ("[16] and ConvS2S, all of which use convolution.", (72, 132), {}),
        ("[2] ", (72, 300), {}),  # number alone in a table cell
    )
    blocks = analyze_page(page)
    reference = next(b for b in blocks if "Srivastava" in b.text)
    assert reference.prefix == "[33] "
    assert reference.text.startswith("Nitish")
    # A citation starting a line of running text stays in its paragraph.
    running = next(b for b in blocks if "ByteNet" in b.text)
    assert "[16] and ConvS2S" in running.text and running.prefix == ""


def test_lines_ended_by_hand_are_not_joined():
    page = page_with(
        ("**** Main Menu ****", (72, 72), {}),
        ("<1> Network Setup", (72, 84), {}),
        ("<X> Exit without saving to flash", (72, 96), {}),
        ("Choose one of the menu choices with the letter or number on", (72, 140), {}),
        ("the left and press Enter.", (72, 152), {}),
        # A two-line header, right-aligned.
        ("7700 MultiFrame Manual", (500 - pymupdf.get_text_length("7700 MultiFrame Manual", fontsize=11), 300), {}),
        (
            "7700FC VistaLINK Frame Controller",
            (500 - pymupdf.get_text_length("7700FC VistaLINK Frame Controller", fontsize=11), 312),
            {},
        ),
    )
    texts = [b.text for b in analyze_page(page)]
    assert "<1> Network Setup" in texts and "**** Main Menu ****" in texts
    assert "Choose one of the menu choices with the letter or number on the left and press Enter." in texts
    assert "7700 MultiFrame Manual" in texts


def test_form_items_numbered_in_parentheses():
    page = page_with(
        ("(a) Use the estimator for the most accurate withholding.", (72, 72), {}),
        ("(c)", (72, 100), {}),
        ("If there are only two jobs total, check this box.", (95, 100), {}),
        ("so the result in (b) applies to both jobs, and", (72, 140), {}),
        ("(b) is more accurate for them.", (72, 152), {}),
    )
    blocks = analyze_page(page)
    item = next(b for b in blocks if "estimator" in b.text)
    assert item.prefix.strip() == "(a)" and item.kind == Kind.LIST_ITEM
    alone = next(b for b in blocks if b.text == "(c)")
    assert (alone.kind, alone.action) == (Kind.MARKER, Action.KEEP)
    # Wrapped running text starting with "(b)" stays in its paragraph.
    running = next(b for b in blocks if "applies" in b.text)
    assert "(b) is more accurate" in running.text


def test_a_dot_leader_drawn_dot_by_dot_follows_its_words():
    """Forms draw leaders one dot at a time: joined to the words, the
    leader is redrawn after the translation instead of under it."""
    dots = [(".", (300 + 12 * i, 72), {}) for i in range(10)]
    page = page_with(("Otherwise, (b) is more accurate", (72, 72), {}), *dots)
    blocks = analyze_page(page)
    assert len(blocks) == 1
    assert blocks[0].text == "Otherwise, (b) is more accurate"
    assert blocks[0].leader is not None and blocks[0].leader.number == ""
