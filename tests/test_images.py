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


def test_marks_the_ocr_left_out_of_a_closed_balloon_are_erased(page_image):
    """The dots of "LATER..." the OCR did not put in the line (page 6)."""
    image = page_image.copy()
    found = balloons.find(image, ocr.read(image))
    first = found[0]
    x0, y0, x1, y1 = first.text_box
    cv2.circle(image, (x0 - 20, (y0 + y1) // 2), 4, (0, 0, 0), -1)
    # Away from the text, a mark is art the region holds (a door, page 6).
    cv2.circle(image, ((x0 + x1) // 2, y1 + 50), 4, (0, 0, 0), -1)
    assert first.mask[y1 + 50, (x0 + x1) // 2]
    # Near the text, a long shape is art too (an outline, a wall the region
    # reaches, page 6).
    cv2.rectangle(image, (x0, y1 + 10), (x0 + 150, y1 + 16), (0, 0, 0), -1)
    assert first.mask[y1 + 13, x0 + 150]
    clean.erase(image, found)
    assert image[y1 + 13, x0 + 75].max() < 80
    assert cv2.cvtColor(image[(y0 + y1) // 2 - 6 : (y0 + y1) // 2 + 6, x0 - 26 : x0 - 14], cv2.COLOR_BGR2GRAY).min() > 150
    assert image[y1 + 50, (x0 + x1) // 2].max() < 80


def open_balloon_page() -> np.ndarray:
    """A balloon whose outline has a gap into its panel (an open one), its
    text close to its outline."""
    drawn = pymupdf.open()
    page = drawn.new_page(width=WIDTH, height=HEIGHT)
    archive = pymupdf.Archive(str(FONT_DIR))
    page.draw_rect(pymupdf.Rect(270, 170, 730, 470), color=(0, 0, 0), fill=(0.55, 0.75, 0.9), width=6)
    page.draw_oval(pymupdf.Rect(300, 200, 700, 400), color=(0, 0, 0), fill=(1, 1, 1), width=5)
    page.draw_rect(pymupdf.Rect(480, 390, 520, 410), color=None, fill=(1, 1, 1))  # the gap
    page.insert_htmlbox(pymupdf.Rect(340, 250, 660, 350), "<p>SIM! ENTRE! FECHE A PORTA!</p>", css=LETTERING, archive=archive)
    picture = page.get_pixmap(dpi=72)
    return cv2.imdecode(np.frombuffer(picture.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)


def test_an_open_balloon_is_erased_within_its_outline():
    """Its lines' boxes, run past its outline, are painted only where the
    balloon is: no squares of its color over the outline and the panel
    (page 6). A letter the OCR left out beside a line goes too ("I" of "I
    NEVER")."""
    from doc_translator.images.ocr import TextLine

    image = open_balloon_page()
    lines = ocr.read(image)
    # A box crossing the outline, as the OCR's do near it.
    x0, y0, x1, y1 = lines[0].box
    lines[0] = TextLine((x0, y0, 700, y1), lines[0].text, lines[0].score)
    [balloon] = balloons.find(image, lines)
    assert balloon.open
    sx0, sy0, sx1, sy1 = lines[1].box  # "PORTA!", away from the outline
    cv2.putText(image, "I", (sx0 - 25, sy1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3)
    # Hair reaching into the last line's box from under it (page 6).
    cv2.rectangle(image, (sx1 - 20, sy1 - 6), (sx1 + 40, sy1 + 60), (40, 40, 40), -1)
    before = image.copy()
    clean.erase(image, [balloon])
    changed = (before != image).any(axis=2)
    assert not (changed & (balloon.room_mask == 0)).any()
    # The panel's light color around the balloon is part of its region, and
    # stays (no corners painted over it).
    panel = (np.abs(before.astype(int) - before[180, 280].astype(int)).sum(axis=2) < 30)
    assert not (changed & panel).any()
    assert (image[sy1 - 6 : sy1 + 60, sx1 - 20 : sx1 + 40] == 40).all()  # the hair is whole
    assert cv2.cvtColor(image[sy0:sy1, sx0 - 30 : sx0 - 2], cv2.COLOR_BGR2GRAY).min() > 150


def test_a_text_that_cannot_be_erased_is_left_as_drawn():
    """Its letters not where they were taken to be ("JULY" on the cover, in
    a white box, taken to be on the sky's blue): erasing would miss most of
    them and the translation would sit over the original. It is left as it
    is, and not lettered again."""
    image = open_balloon_page()
    [balloon] = balloons.find(image, ocr.read(image))
    assert balloon.open
    balloon.color = (230, 190, 140)  # the panel's blue, not the balloon's white
    before = image.copy()
    assert clean.erase(image, [balloon]) == [False]
    assert (image == before).all()


def test_a_translation_goes_in_the_middle_of_its_room_where_the_text_was_too_high():
    """A caption's text near its top ("MINUTES LATER...", page 6): centered
    there, the translation was lettered small; in the middle of the caption
    it fits at the original size."""
    from doc_translator.images import typeset
    from doc_translator.images.ocr import TextLine

    mask = np.zeros((300, 800), np.uint8)
    mask[100:175, 200:580] = 255
    caption = balloons.Balloon(
        [TextLine((230, 113, 540, 144), "MINUTES LATER...", 1.0)], (200, 100, 579, 174), (200, 250, 250),
        mask=mask, round=False, cap_height=21.0,
    )
    page = pymupdf.open().new_page(width=800, height=300)
    typeset.prepare(page)
    assert typeset.write(page, caption, "MINUTOS DEPOIS...", 1.0) == 1.0


def test_digits_read_for_letters_are_letters_again():
    """Hand lettering's I, S and O read as 1, 5 and 0: the translation had to
    keep "I5THAT" (every number of the original stays)."""
    assert respace("OUR AGREEMENT, J.J., I5THATYOU'RE NEVER") == "OUR AGREEMENT, J.J., IS THAT YOU'RE NEVER"
    assert respace("WHAT YOU DON'T 151T?") == "WHAT YOU DON'T IS IT?"
    assert respace("ROOM 1201, IN 1963") == "ROOM 1201, IN 1963"  # numbers stay
    # The price: a cents sign the OCR read as a letter (the cover's "12¢").
    assert respace("MARVEL COMICS 12t GROUP") == "MARVEL COMICS 12¢ GROUP"


def test_a_line_read_unsure_is_read_again_with_room_around_it(monkeypatch):
    from doc_translator.images import ocr as ocr_

    class Engine:
        def __call__(self, image, use_det=None, use_cls=None):
            if use_det is False:  # the line, read again
                return [["OUT!DON'T", 0.96]], None
            return [[[[10, 10], [90, 10], [90, 40], [10, 40]], "1.NOO10O", 0.59]], None

    monkeypatch.setattr(ocr_, "_engine", lambda: Engine())
    assert [line.text for line in ocr_.read(np.full((60, 120, 3), 255, np.uint8))] == ["OUT!DON'T"]

    class Unsure(Engine):
        def __call__(self, image, use_det=None, use_cls=None):
            if use_det is False:
                return [["L!", 0.68]], None  # surer, not sure: the door's "1201"
            return [[[[10, 10], [90, 10], [90, 40], [10, 40]], "1201", 0.51]], None

    monkeypatch.setattr(ocr_, "_engine", lambda: Unsure())
    assert [line.text for line in ocr_.read(np.full((60, 120, 3), 255, np.uint8))] == ["1201"]

    class Shorter(Engine):
        def __call__(self, image, use_det=None, use_cls=None):
            if use_det is False:
                return [["12", 0.97]], None  # sure, but only drops the price's sign
            return [[[[10, 10], [90, 10], [90, 40], [10, 40]], "12t", 0.75]], None

    monkeypatch.setattr(ocr_, "_engine", lambda: Shorter())
    assert [line.text for line in ocr_.read(np.full((60, 120, 3), 255, np.uint8))] == ["12t"]


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


def test_lines_of_balloons_side_by_side_are_not_one_text():
    """Two balloons touching ("WHAT IS IT? YOU-- OH, NO! NO!" and "HA-HA! YOU
    DON'T BELIEVE...", page 6): lines on a row close to each other, and
    lines meeting at a corner, with the outline between them."""
    from doc_translator.images.ocr import TextLine

    walls = np.zeros((200, 300), np.uint8)
    walls[:, 100] = 255  # the outline between the balloons
    left, right = TextLine((10, 20, 95, 50), "OH, NO! NO!", 1.0), TextLine((105, 22, 190, 52), "WHAT YOU", 1.0)
    assert not balloons._linked(left, right, walls)
    assert balloons._linked(left, right, np.zeros_like(walls))  # one balloon: one text
    corner = TextLine((94, 48, 200, 80), "SEE? BUT", 1.0)
    assert not balloons._linked(left, corner, np.zeros_like(walls))
    under = TextLine((20, 52, 90, 82), "WHAT", 1.0)
    assert balloons._linked(left, under, np.zeros_like(walls))


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


def slanted_caption_page() -> bytes:
    """A caption box turned 10 degrees, descending to the right, its text
    turned with it (the arrows of page 2)."""
    import math

    drawn = pymupdf.open()
    page = drawn.new_page(width=WIDTH, height=HEIGHT)
    pivot = pymupdf.Point(600, 450)
    turn = pymupdf.Matrix(-10)  # PyMuPDF turns counterclockwise for positive angles
    corners = [pymupdf.Point(x, y) for x, y in ((380, 360), (820, 360), (820, 540), (380, 540))]
    turned = [pivot + (c - pivot) * turn for c in corners]
    page.draw_polyline([*turned, turned[0]], color=(0, 0, 0), fill=(0.55, 0.8, 0.95), width=4)
    font = str(FONT_DIR / FONT)
    for i, line in enumerate(("IN THIS EPIC TALE...", "SEE THE HUMAN TORCH!")):
        width = pymupdf.Font(fontfile=font).text_length(line, fontsize=30)
        page.insert_text((600 - width / 2, 440 + 40 * i), line, fontsize=30, fontfile=font, fontname="c", morph=(pivot, turn))
    picture = page.get_pixmap(dpi=72)
    doc = pymupdf.open()
    doc.new_page(width=WIDTH, height=HEIGHT).insert_image(pymupdf.Rect(0, 0, WIDTH, HEIGHT), stream=picture.tobytes("jpeg"))
    assert math.isclose(turn.b, math.sin(math.radians(-10)), abs_tol=1e-6)
    return doc.tobytes()


def test_a_slanted_text_is_lettered_slanted(tmp_path):
    """Its lines' boxes are taller than its letters: taken for a title, it
    was left in English (page 2). Measured across its lines, it is a
    caption, and its translation is written at its slant."""
    import math

    from doc_translator.pipeline import translate_pdf

    doc = pymupdf.open("pdf", slanted_caption_page())
    data = doc.extract_image(doc[0].get_image_info(xrefs=True)[0]["xref"])["image"]
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    [caption] = balloons.find(image, ocr.read(image))
    assert 8 <= caption.angle <= 12
    assert caption.line_height < 45 < caption.lines[0].box[3] - caption.lines[0].box[1]

    class Short:
        cache_id = "short"

        def translate_units(self, units):
            return [UnitTranslation("NESTA HISTÓRIA ÉPICA... VEJA O TOCHA HUMANA!", []) for _ in units]

    source = tmp_path / "comic.pdf"
    source.write_bytes(slanted_caption_page())
    assert translate_pdf(source, tmp_path / "out.pdf", Short()).images.balloons == 1
    lines = [line for block in pymupdf.open(tmp_path / "out.pdf")[0].get_text("dict")["blocks"] for line in block.get("lines", [])]
    assert lines
    for line in lines:  # descending to the right, as the original
        dx, dy = line["dir"]
        assert 8 <= math.degrees(math.atan2(dy, dx)) <= 12


def loose_text_page() -> np.ndarray:
    """A panel and, on the page's white around it, a red balloon with white
    letters, a line of lettering, and print: a web address, a page number."""
    drawn = pymupdf.open()
    page = drawn.new_page(width=WIDTH, height=HEIGHT)
    font = str(FONT_DIR / FONT)
    page.draw_rect(pymupdf.Rect(40, 40, 580, 600), color=(0, 0, 0), fill=(0.55, 0.75, 0.9), width=6)
    page.draw_oval(pymupdf.Rect(640, 80, 1120, 380), color=(0, 0, 0), fill=(0.9, 0.1, 0.1), width=5)
    for i, line in enumerate(("CAN ANYTHING THAT", "LIVES DEFEAT THE", "MIGHTY OCTOPUS?")):
        width = pymupdf.Font(fontfile=font).text_length(line, fontsize=30)
        page.insert_text((880 - width / 2, 200 + 34 * i), line, fontsize=30, fontfile=font, fontname="c", color=(1, 1, 1))
    page.insert_text((660, 700), "FANTASY! THRILLS!", fontsize=30, fontfile=font, fontname="c")
    page.insert_text((80, 820), "www.example.com", fontsize=26, fontname="helv")
    page.insert_text((1100, 860), "7", fontsize=26, fontname="helv")
    picture = page.get_pixmap(dpi=72)
    return cv2.imdecode(np.frombuffer(picture.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)


def test_text_on_a_dark_balloon_and_lettering_on_the_page_are_found():
    """Light letters on a dark balloon (the cover's red question mark) and
    lettering on the page's white with nothing around it ("FANTASY!
    THRILLS!", page 2) are texts; print on the page (a web address, a page
    number) is not."""
    image = loose_text_page()
    found = balloons.find(image, ocr.read(image))
    texts = sorted(respace(join([line.text for line in b.lines])).replace(" ", "") for b in found)
    assert texts == ["CANANYTHINGTHATLIVESDEFEATTHEMIGHTYOCTOPUS?", "FANTASY!THRILLS!"]
    red = next(b for b in found if b.lines[0].text.startswith("CAN"))
    blue, green, red_ = red.color
    assert red_ > 180 and green < 80 and blue < 80
    assert min(red.ink) > 200  # lettered again in white


def test_a_line_the_ocr_missed_is_read_again(page_image):
    """The page's OCR can leave out a line of a balloon ("OCTOPUS??" on the
    cover, slanted more than the others; "WITH HIM? HE DIDN'T ANSWER!"):
    letters near the balloon's text that no line holds are read again."""
    from doc_translator.images import missed

    lines = ocr.read(page_image)
    first = balloons.find(page_image, lines)[0]
    assert len(first.lines) >= 2
    left_out = first.lines[-1]
    lines = [line for line in lines if line is not left_out]
    found = balloons.find(page_image, lines)
    balloon = found[0]
    added = missed.complete(page_image, balloon, lines, found)
    assert added is not None
    assert added.text.replace(" ", "") == left_out.text.replace(" ", "")
    assert balloon.lines[-1] is added
    # Nothing else is left out, in it or in the other balloons.
    lines.append(added)
    assert all(missed.complete(page_image, b, lines, found) is None for b in found)


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
