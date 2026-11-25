"""Comics (ADR 0008, 0009): pages that are images, read by OCR; their
balloons erased and lettered again in the translation."""

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf

from ..align import locate
from ..images import balloons as balloons_
from ..images import clean, emphasis, image_pages, missed, ocr, typeset
from ..images.text import join, respace
from ..result import Result
from ..translators.base import Translator, Unit

log = logging.getLogger(__name__)

# Written back at this JPEG quality: close to the scans' own.
JPEG_QUALITY = 90


@dataclass
class ComicResult:
    pages: list[int] = field(default_factory=list)  # 0-based
    balloons: int = 0
    shrunk: list[str] = field(default_factory=list)  # texts written below typeset.MIN_SCALE
    emphasized: int = 0  # emphasized phrases of the original
    not_erased: list[str] = field(default_factory=list)  # texts left as drawn: their letters could not be erased
    emphasis_lost: list[str] = field(default_factory=list)  # ...not found in their translation


def translate(doc: pymupdf.Document, translator: Translator, run: Result) -> None:
    """The comic process: the pages that are images, into `run.images`."""
    with run.timed("images"):
        run.images = translate_pages(doc, image_pages(doc), translator)


# Text over the art in an open region, its letters this many times the
# comic's lettering or taller, is a title, a logo or a cover's blurb ("the"
# of the logo, page 1): left as drawn.
DISPLAY_HEIGHT = 2.0


def translate_pages(doc: pymupdf.Document, pages: list[int], translator: Translator) -> ComicResult:
    """Read, erase and write again the balloons of `pages`, in place. Every
    balloon of the document goes to the engine in one call, in reading
    order: a story is translated knowing what came before."""
    result = ComicResult(pages=list(pages))
    found = []
    for index in pages:
        page = doc[index]
        info = page.get_image_info(xrefs=True)[0]
        data = doc.extract_image(info["xref"])["image"]
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        page_lines = ocr.read(image)
        page_balloons = balloons_.find(image, page_lines)
        for balloon in page_balloons:
            # A line read again can be part of what was missed ("DID" of
            # "DID I DO??"): looked for again until nothing is left out.
            for _ in range(missed.TRIES):
                if not (added := missed.complete(image, balloon, page_lines, page_balloons)):
                    break
                page_lines.append(added)  # one balloon's, not the next's
                log.info("Page %d: a line the OCR missed read again: %s", page.number + 1, added.text)
        found.append((page, info, image, page_balloons))
    lettering = float(np.median([b.line_height for *_, page_balloons in found for b in page_balloons] or [0]))
    for page, _, _, page_balloons in found:
        page_balloons[:] = [b for b in page_balloons if not (b.open and b.line_height > DISPLAY_HEIGHT * lettering)]
        log.info("Page %d: %d balloons", page.number + 1, len(page_balloons))

    measured = [[emphasis.measure(image, b) for b in page_balloons] for _, _, image, page_balloons in found]
    plain = emphasis.plain_stroke([words for page_words in measured for words in page_words])
    units = []
    for (*_, page_balloons), page_words in zip(found, measured):
        for balloon, words in zip(page_balloons, page_words):
            text = respace(join([line.text for line in balloon.lines]))
            units.append(Unit(text, segments=emphasis.phrases(balloon, words, text, plain), kind="balloon"))
    translations = iter(translator.translate_units(units))
    sources = iter(units)
    for page, info, image, page_balloons in found:
        # A text the translation leaves as it is (a number, a name, a web
        # address) keeps its original letters.
        written = [
            (balloon, unit, translation)
            for balloon, unit, translation in zip(page_balloons, sources, (next(translations) for _ in page_balloons))
            if _letters(translation.text) != _letters(unit.text)
        ]
        # A text whose letters could not be erased keeps them, untranslated.
        erased = clean.erase(image, [balloon for balloon, *_ in written])
        result.not_erased += [unit.text for (_, unit, _), done in zip(written, erased) if not done]
        written = [item for item, done in zip(written, erased) if done]
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError(f"could not encode page {page.number + 1}")
        page.replace_image(info["xref"], stream=encoded.tobytes())
        scale = pymupdf.Rect(info["bbox"]).width / image.shape[1]
        if written:
            typeset.prepare(page)
        for balloon, unit, translation in written:
            spans = locate(unit.segments, translation.segments, translation.text)
            result.emphasized += len(spans)
            result.emphasis_lost += [segment for segment, span in zip(unit.segments, spans) if span is None]
            shrink = typeset.write(page, balloon, translation.text, scale, [span for span in spans if span])
            if shrink < typeset.MIN_SCALE:
                result.shrunk.append(translation.text)
            result.balloons += 1
    for phrase in result.emphasis_lost:
        log.debug("Emphasis not found in the translation: %s", phrase)
    for text in result.not_erased:
        log.info("Left untranslated, its letters could not be erased: %s", text)
    return result


def _letters(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())
