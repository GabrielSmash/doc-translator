"""The lines of text of a page image, with their boxes (RapidOCR)."""

from dataclasses import dataclass
from functools import cache

import numpy as np


@dataclass
class TextLine:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in image pixels
    text: str
    score: float  # the OCR's confidence, 0 to 1


@cache
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


# A line read with less confidence is read again from its box with some
# room around it (the page's boxes are tight): "1.NOO10O" (0.59) is then
# "OUT!DON'T" (0.96). The new reading is kept when it is surer and sure.
RETRY_SCORE = 0.85
RETRY_ROOM = 1 / 3  # of the line's height, on each side


def read(image: np.ndarray) -> list[TextLine]:
    """Lines of `image` (BGR), as the OCR finds them."""
    result, _ = _engine()(image)
    lines = []
    for box, text, score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        line = TextLine((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), text, float(score))
        lines.append(_again(image, line) if line.score < RETRY_SCORE else line)
    return lines


def _again(image: np.ndarray, line: TextLine) -> TextLine:
    x0, y0, x1, y1 = line.box
    room = int((y1 - y0) * RETRY_ROOM)
    crop = image[max(0, y0 - room) : y1 + room, max(0, x0 - room) : x1 + room]
    result, _ = _engine()(crop, use_det=False, use_cls=False)
    if result:
        text, score = result[0][0], float(result[0][1])
        # A reading that only drops characters tells nothing new and loses
        # what it drops ("12t", the cover's "12¢", read again "12").
        if score > line.score and score >= RETRY_SCORE and text.strip() and not _drops(line.text, text):
            return TextLine(line.box, text, score)
    return line


def _drops(old: str, new: str) -> bool:
    """`new` is `old` with characters left out."""
    rest = iter(old)
    return len(new) < len(old) and all(c in rest for c in new)
