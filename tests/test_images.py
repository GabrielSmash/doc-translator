"""Pages that are images (ADR 0008), on a synthetic comic page: balloons
drawn, lettered in capitals, the whole page made one image."""

import pymupdf
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("rapidocr_onnxruntime")

import numpy as np

from doc_translator.images import balloons, clean, image_pages, ocr
from doc_translator.images.text import join, respace
from doc_translator.images.typeset import FONT, FONT_DIR
from doc_translator.terms import repeated_phrases
from doc_translator.translators.base import UnitTranslation

WIDTH, HEIGHT = 1200, 900
LETTERING = f"@font-face {{font-family: comic; src: url({FONT});}} p {{font-family: comic; text-align: center; font-size: 30px; margin: 0;}}"


def comic_page() -> bytes:
    """Two panels; a round balloon in the first, a caption box and a
    balloon in the second. Drawn as vectors, then flattened to an image."""
    drawn = pymupdf.open()
    page = drawn.new_page(width=WIDTH, height=HEIGHT)
    archive = pymupdf.Archive(str(FONT_DIR))
    for panel in (pymupdf.Rect(40, 40, 580, 860), pymupdf.Rect(620, 40, 1160, 860)):
        page.draw_rect(panel, color=(0, 0, 0), fill=(0.55, 0.75, 0.9), width=6)
    page.draw_oval(pymupdf.Rect(90, 100, 530, 360), color=(0, 0, 0), fill=(1, 1, 1), width=5)
    page.insert_htmlbox(pymupdf.Rect(160, 160, 460, 320), "<p>THE MACHINE IS READY! NOW WE WAIT.</p>", css=LETTERING, archive=archive)
    page.draw_rect(pymupdf.Rect(640, 60, 1140, 170), color=(0, 0, 0), fill=(1, 1, 0.6), width=4)
    page.insert_htmlbox(pymupdf.Rect(660, 75, 1120, 165), "<p>LATER, IN THE LAB...</p>", css=LETTERING, archive=archive)
    page.draw_oval(pymupdf.Rect(680, 420, 1100, 680), color=(0, 0, 0), fill=(1, 1, 1), width=5)
    page.insert_htmlbox(pymupdf.Rect(750, 480, 1030, 640), "<p>WHERE IS THE KEY?</p>", css=LETTERING, archive=archive)
    picture = page.get_pixmap(dpi=72)
    doc = pymupdf.open()
    flat = doc.new_page(width=WIDTH, height=HEIGHT)
    flat.insert_image(flat.rect, stream=picture.tobytes("jpeg"))
    return doc.tobytes()


@pytest.fixture(scope="module")
def page_image():
    doc = pymupdf.open("pdf", comic_page())
    data = doc.extract_image(doc[0].get_image_info(xrefs=True)[0]["xref"])["image"]
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def test_a_page_that_is_one_image_is_an_image_page():
    assert image_pages(pymupdf.open("pdf", comic_page())) == [0]


def test_balloons_are_found_in_reading_order(page_image):
    found = balloons.find(page_image, ocr.read(page_image))
    texts = [respace(join([line.text for line in b.lines])).upper() for b in found]
    # Panel by panel: the first panel's balloon, then the caption and the
    # balloon of the second.
    assert [t.replace(" ", "") for t in texts] == ["THEMACHINEISREADY!NOWWEWAIT.", "LATER,INTHELAB...", "WHEREISTHEKEY?"]
    assert [b.round for b in found] == [True, False, True]
    assert all(not b.open for b in found)


def test_the_letters_are_erased_and_the_outline_kept(page_image):
    image = page_image.copy()
    found = balloons.find(image, ocr.read(image))
    clean.erase(image, found)
    for balloon in found:
        x0, y0, x1, y1 = balloon.text_box
        inside = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        assert inside.min() > 150  # no dark letters left
    # The first balloon's outline, left of its text, is still black.
    assert image[222:238, 84:100].min() < 80


def test_spaces_the_ocr_dropped_are_put_back():
    assert respace("THEX-RAYSSHOW AN UNCERTAIN AMOUNT OFBRAIN DAMAGE!") == "THE X-RAYS SHOW AN UNCERTAIN AMOUNT OF BRAIN DAMAGE!"
    assert respace(join(["THE RADIA", "TION HAS CAUSED THEM"])) == "THE RADIATION HAS CAUSED THEM"
    assert respace("NOONECAN HOLD HIM") == "NO ONE CAN HOLD HIM"


def test_names_in_capitals_are_found_by_repetition():
    texts = [
        "DOCTOR OCTOPUS IS STILL BREATHING!",
        "NO ONE CAN HOLD DOCTOR OCTOPUS!",
        "THE BRAIN OF DR. OCTOPUS REACTS.",
        "NO ONE! NO ONE AT ALL!",
    ]
    found = repeated_phrases(texts)
    assert "Doctor Octopus" in found and "Dr. Octopus" in found
    assert "No One" not in found  # common words


def test_a_comic_page_is_translated_in_its_balloons(tmp_path):
    from doc_translator.pipeline import translate_pdf

    class Upper:
        cache_id = "upper"

        def translate_units(self, units):
            self.units = units
            return [UnitTranslation("TRADUZIDO " + str(i), []) for i, _ in enumerate(units)]

    source = tmp_path / "comic.pdf"
    source.write_bytes(comic_page())
    engine = Upper()
    result = translate_pdf(source, tmp_path / "out.pdf", engine)
    assert result.images.balloons == 3
    assert all(unit.kind == "balloon" for unit in engine.units)
    out = pymupdf.open(tmp_path / "out.pdf")
    written = out[0].get_text()
    assert "TRADUZIDO 0" in written and "TRADUZIDO 2" in written
