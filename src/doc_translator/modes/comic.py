"""Comics (ADR 0008, 0009): pages that are images, read by OCR; their
balloons erased and lettered again in the translation."""

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf

from ..images import balloons as balloons_
from ..images import clean, image_pages, ocr, typeset
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
        found.append((page, info, image, balloons_.find(image, ocr.read(image))))
    lettering = float(np.median([b.line_height for *_, page_balloons in found for b in page_balloons] or [0]))
    for page, _, _, page_balloons in found:
        page_balloons[:] = [b for b in page_balloons if not (b.open and b.line_height > DISPLAY_HEIGHT * lettering)]
        log.info("Page %d: %d balloons", page.number + 1, len(page_balloons))

    texts = [respace(join([line.text for line in b.lines])) for *_, page_balloons in found for b in page_balloons]
    translations = iter(translator.translate_units([Unit(text, kind="balloon") for text in texts]))
    sources = iter(texts)
    for page, info, image, page_balloons in found:
        # A text the translation leaves as it is (a number, a name, a web
        # address) keeps its original letters.
        written = [
            (balloon, translation)
            for balloon, source, translation in zip(page_balloons, sources, (next(translations).text for _ in page_balloons))
            if _letters(translation) != _letters(source)
        ]
        clean.erase(image, [balloon for balloon, _ in written])
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError(f"could not encode page {page.number + 1}")
        page.replace_image(info["xref"], stream=encoded.tobytes())
        scale = pymupdf.Rect(info["bbox"]).width / image.shape[1]
        if written:
            typeset.prepare(page)
        for balloon, translation in written:
            shrink = typeset.write(page, balloon, translation, scale)
            if shrink < typeset.MIN_SCALE:
                result.shrunk.append(translation)
            result.balloons += 1
    return result


def _letters(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())
