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


def comic_page(caption_ink: str = "#000") -> bytes:
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
    page.insert_htmlbox(pymupdf.Rect(660, 75, 1120, 165), f'<p style="color: {caption_ink}">LATER, IN THE LAB...</p>', css=LETTERING, archive=archive)
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


def test_a_pdf_of_page_images_goes_to_the_comic_process():
    from doc_translator.modes import DocumentError, choose

    comic = pymupdf.open("pdf", comic_page())
    assert choose(comic).name == "comic"
    assert choose(comic).engine == "llm"
    with pytest.raises(DocumentError, match="--mode comic"):
        choose(comic, "document")


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


def test_lines_with_an_outline_between_them_are_two_texts():
    """A thought balloon whose outline has gaps is one region with the
    caption over it (page 6): the outline between their lines parts them."""
    from doc_translator.images.ocr import TextLine

    caption = TextLine((100, 100, 600, 130), "THEN SUDDENLY HIS ARMS MOVE", 1.0)
    thought = TextLine((200, 160, 400, 190), "I'VE GOT TO BREAK", 1.0)
    below = TextLine((200, 190, 400, 220), "THOSE BARS!", 1.0)
    walls = np.zeros((300, 700), bool)
    assert len(balloons._groups([caption, thought, below], walls)) == 1
    walls[145, 150:450] = True  # the thought balloon's outline
    texts = balloons._groups([caption, thought, below], walls)
    assert sorted(len(t) for t in texts) == [1, 2]


def test_the_lines_of_a_row_are_read_left_to_right():
    from doc_translator.images.ocr import TextLine

    # Two words of a row, the second a little higher.
    row = [TextLine((1637, 2698, 1864, 2774), "BEGIN!", 1.0), TextLine((1402, 2706, 1629, 2763), "AND NOW", 1.0)]
    assert [l.text for l in balloons._reading(row)] == ["AND NOW", "BEGIN!"]
    # Lines one under the other overlap a little: still top to bottom.
    stack = [TextLine((1416, 1746, 1655, 1824), "EPIC TALE.", 1.0), TextLine((1458, 1715, 1634, 1783), "IN THIS", 1.0)]
    assert [l.text for l in balloons._reading(stack)] == ["IN THIS", "EPIC TALE."]


def test_spaces_the_ocr_dropped_are_put_back():
    assert respace("THEX-RAYSSHOW AN UNCERTAIN AMOUNT OFBRAIN DAMAGE!") == "THE X-RAYS SHOW AN UNCERTAIN AMOUNT OF BRAIN DAMAGE!"
    assert respace(join(["THE RADIA", "TION HAS CAUSED THEM"])) == "THE RADIATION HAS CAUSED THEM"
    assert respace("NOONECAN HOLD HIM") == "NO ONE CAN HOLD HIM"


def test_the_most_used_words_are_chosen():
    """Long words of the dictionary are not enough: "MYNA ME", "THEW ALL",
    "COMET RUE" are words too. The most used ones are taken."""
    assert respace("YOUCANSAYMYNAME!") == "YOU CAN SAY MY NAME!"
    assert respace("UP THEWALL") == "UP THE WALL"
    assert respace("ABOUT TO COMETRUE") == "ABOUT TO COME TRUE"
    assert respace("SUCHAMANISOTTOOCTAVIUS") == "SUCH A MAN IS OTTO OCTAVIUS"
    # Speech: "me" is not rare in a comic ("TOME").
    assert respace("ITMEANTALOTTOME!") == "IT MEANT A LOT TO ME!"


def test_apostrophes_and_misread_letters():
    assert respace("THEY'RETRYING TO") == "THEY'RE TRYING TO"
    assert respace("I'VEGOTTO TRY IT") == "I'VE GOT TO TRY IT"
    assert respace("DOCTOR OCTOPUS'ARMS") == "DOCTOR OCTOPUS' ARMS"
    assert respace("THIS WIRING'LL SUIT ME") == "THIS WIRING'LL SUIT ME"
    assert respace("ABILITYALONEISN'T ENOUGH") == "ABILITY ALONE ISN'T ENOUGH"
    assert respace("ISUREAIN'T") == "I SURE AIN'T"
    # A lone "L" is a misread "I".
    assert respace("LETME UP! LMUSTRETURN") == "LET ME UP! I MUST RETURN"


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
    from doc_translator.report import Report

    summary = Report.from_result(result, source, tmp_path / "out.pdf").summary()
    assert summary.startswith("1 comic pages, 3 balloons lettered again")
    out = pymupdf.open(tmp_path / "out.pdf")
    written = out[0].get_text()
    assert "TRADUZIDO 0" in written and "TRADUZIDO 2" in written


def test_the_translation_is_lettered_as_tall_and_in_the_color_of_the_original(tmp_path):
    """Lettered 30 px high in red on the caption, black in the balloons: the
    translation's capitals as tall as the original's, in their color."""
    from doc_translator.pipeline import translate_pdf

    class Short:
        cache_id = "short"

        def translate_units(self, units):
            return [UnitTranslation("OI", []) for _ in units]

    source = tmp_path / "comic.pdf"
    source.write_bytes(comic_page(caption_ink="#c00000"))
    doc = pymupdf.open(source)
    data = doc.extract_image(doc[0].get_image_info(xrefs=True)[0]["xref"])["image"]
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    found = balloons.find(image, ocr.read(image))
    capitals = 30 * 0.68  # Comic Neue's capitals at 30 px
    assert all(abs(b.cap_height - capitals) <= 2 for b in found)
    caption = next(b for b in found if not b.round)
    blue, green, red = caption.ink
    assert red > 150 and green < 80 and blue < 80

    translate_pdf(source, tmp_path / "out.pdf", Short())
    spans = [
        span
        for block in pymupdf.open(tmp_path / "out.pdf")[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]
    assert len(spans) == 3
    assert all(abs(span["size"] - 30) <= 2 for span in spans)
    colors = sorted(span["color"] for span in spans)
    assert colors[0] < 0x303030 and colors[1] < 0x303030  # the balloons: black
    assert colors[2] >> 16 > 150 and (colors[2] >> 8) & 0xFF < 80  # the caption: red


def emphasis_page() -> bytes:
    """A balloon with one emphasized word, heavier and slanted: "MY *ARMS*
    DID IT!" """
    from doc_translator.images.typeset import EMPHASIS_FONT

    drawn = pymupdf.open()
    page = drawn.new_page(width=WIDTH, height=HEIGHT)
    page.draw_rect(page.rect, color=None, fill=(0.55, 0.75, 0.9))
    page.draw_oval(pymupdf.Rect(40, 60, 660, 340), color=(0, 0, 0), fill=(1, 1, 1), width=5)
    plain, heavy = str(FONT_DIR / FONT), str(FONT_DIR / EMPHASIS_FONT)
    x = 175
    for word, font in (("MY", plain), ("ARMS", heavy), ("DID IT!", plain)):
        bold = font == heavy
        page.insert_text(
            (x, 180), word, fontsize=30, fontfile=font, fontname="h" if bold else "p",
            render_mode=2 if bold else 0, border_width=0.06 if bold else 0,
        )
        x += pymupdf.Font(fontfile=font).text_length(word + " ", fontsize=30) + 4
    page.insert_text((165, 230), "THEY CAN DO IT ALL.", fontsize=30, fontfile=plain, fontname="p")
    picture = page.get_pixmap(dpi=72)
    doc = pymupdf.open()
    doc.new_page(width=WIDTH, height=HEIGHT).insert_image(pymupdf.Rect(0, 0, WIDTH, HEIGHT), stream=picture.tobytes("jpeg"))
    return doc.tobytes()


def test_emphasized_words_are_found_and_lettered_in_bold_italic(tmp_path):
    from doc_translator.images import emphasis
    from doc_translator.pipeline import translate_pdf

    doc = pymupdf.open("pdf", emphasis_page())
    data = doc.extract_image(doc[0].get_image_info(xrefs=True)[0]["xref"])["image"]
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    [balloon] = balloons.find(image, ocr.read(image))
    words = emphasis.measure(image, balloon)
    text = respace(join([line.text for line in balloon.lines]))
    assert emphasis.phrases(balloon, words, text, emphasis.plain_stroke([words])) == ["ARMS"]

    class Arms:
        cache_id = "arms"

        def translate_units(self, units):
            self.units = units
            return [UnitTranslation("MEUS BRAÇOS FIZERAM ISSO! ELES PODEM TUDO.", ["BRAÇOS"]) for _ in units]

    source = tmp_path / "comic.pdf"
    source.write_bytes(emphasis_page())
    engine = Arms()
    result = translate_pdf(source, tmp_path / "out.pdf", engine)
    assert engine.units[0].segments == ["ARMS"]
    assert result.images.emphasized == 1 and not result.images.emphasis_lost
    fonts = {
        span["text"]: span["font"]
        for block in pymupdf.open(tmp_path / "out.pdf")[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    }
    assert "Italic" in fonts["BRAÇOS"]
    assert all("Italic" not in font for text, font in fonts.items() if text != "BRAÇOS")
    # Lettered heavy and slanted as the original: measured again, it is the
    # one emphasized word ("MEUS BRAÇOS FIZERAM ISSO! ELES" / "PODEM TUDO.").
    picture = pymupdf.open(tmp_path / "out.pdf")[0].get_pixmap(dpi=72)
    lettered = cv2.imdecode(np.frombuffer(picture.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
    [balloon] = balloons.find(lettered, ocr.read(lettered))
    words = emphasis.measure(lettered, balloon)
    plain = emphasis.plain_stroke([words])
    heavy = [[w.stroke >= emphasis.HEAVY * plain and w.slant >= emphasis.SLANT for w in line] for line in words]
    assert heavy == [[False, True, False, False, False], [False, False]]


def test_a_text_the_translation_leaves_as_it_is_keeps_its_letters(tmp_path):
    """A number, a name, a web address: erased and lettered again the same,
    it would only lose the original lettering."""
    from doc_translator.pipeline import translate_pdf

    class KeepsTheCaption:
        cache_id = "keeps"

        def translate_units(self, units):
            return [UnitTranslation(u.text if "LAB" in u.text else "TRADUZIDO", []) for u in units]

    source = tmp_path / "comic.pdf"
    source.write_bytes(comic_page())
    result = translate_pdf(source, tmp_path / "out.pdf", KeepsTheCaption())
    assert result.images.balloons == 2
    assert "LAB" not in pymupdf.open(tmp_path / "out.pdf")[0].get_text()  # not lettered again
