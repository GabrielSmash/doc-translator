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


def read(image: np.ndarray) -> list[TextLine]:
    """Lines of `image` (BGR), as the OCR finds them."""
    result, _ = _engine()(image)
    lines = []
    for box, text, score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        lines.append(TextLine((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), text, float(score)))
    return lines
