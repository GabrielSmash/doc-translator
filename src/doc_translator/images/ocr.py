"""The lines of text of a page image, with their boxes (RapidOCR)."""

import math
from dataclasses import dataclass
from functools import cache

import cv2
import numpy as np


@dataclass
class TextLine:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in image pixels
    text: str
    score: float  # the OCR's confidence, 0 to 1
    # Degrees, positive descending to the right (the arrows of page 2 of the
    # comic, -13 and 8); its box is then taller than its letters.
    angle: float = 0.0
    height: float = 0.0  # across the line, when slanted; 0: the box's height
    # The OCR's quadrilateral around a slanted line (its box also holds
    # the corners of the lines above and below).
    quad: tuple[tuple[int, int], ...] | None = None

    @property
    def letter_height(self) -> float:
        return self.height or float(self.box[3] - self.box[1])


@cache
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


# A line slanted this many degrees or more is lettered slanted; lettering
# stands within 1 degree.
SLANTED = 3

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
        (left_x, left_y), (right_x, right_y) = box[0], box[1]
        angle = math.degrees(math.atan2(right_y - left_y, right_x - left_x))
        if abs(angle) >= SLANTED:
            line.angle = angle
            line.height = (math.dist(box[0], box[3]) + math.dist(box[1], box[2])) / 2
            line.quad = tuple((int(x), int(y)) for x, y in box)
        lines.append(_again(image, line) if line.score < RETRY_SCORE else line)
    return [_widened(image, line, lines) for line in lines]


def read_crop(image: np.ndarray) -> list[TextLine]:
    """Lines of a small image (a part of a page read again), as read: no
    second reading of unsure lines."""
    result, _ = _engine()(image)
    lines = []
    for box, text, score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        lines.append(TextLine((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), text, float(score)))
    return lines


def read_line(crop: np.ndarray) -> tuple[str, float] | None:
    """The text of a crop holding one line, and the OCR's confidence."""
    result, _ = _engine()(crop, use_det=False, use_cls=False)
    return (result[0][0], float(result[0][1])) if result and result[0][0].strip() else None


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
            return TextLine(line.box, text, score, line.angle, line.height, line.quad)
    return line


# Ink darker than this is lettering, for a line's ends.
INK_GRAY = 110
# A letter next to a line's end: this tall and wide, in the line's heights,
# at most this far from it.
END_LETTER = (0.5, 1.6)
END_WIDTH = 1.5
END_GAP = 0.5
# Read again with it, the line's ends are found by this many of their
# characters; at most this many letters are added at an end; the new
# reading is this sure at least.
END_ANCHOR = 3
END_LETTERS = 2
END_SCORE = 0.7


def _widened(image: np.ndarray, line: TextLine, lines: list[TextLine]) -> TextLine:
    """`line`, read again with the letter beside one of its ends its box
    left out: a caption's first letter lettered larger ("LATER" read
    "ATER", page 14). Taken when the new reading holds the old one and more,
    and is sure."""
    if line.angle:
        return line
    x0, y0, x1, y1 = line.box
    tall = y1 - y0
    height, width = image.shape[:2]
    cx0, cy0 = max(0, x0 - 2 * tall), max(0, y0 - tall // 2)
    cx1, cy1 = min(width, x1 + 2 * tall), min(height, y1 + tall // 2)
    gray = cv2.cvtColor(image[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2GRAY)
    count, _, stats, _ = cv2.connectedComponentsWithStats((gray < INK_GRAY).astype(np.uint8), connectivity=8)
    others = [other.box for other in lines if other is not line]
    left, right = x0, x1
    for sx, sy, sw, sh, _ in stats[1:count]:
        sx0, sy0, sx1, sy1 = cx0 + sx, cy0 + sy, cx0 + sx + sw, cy0 + sy + sh
        touches_crop = sx == 0 or sy == 0 or sx + sw == cx1 - cx0 or sy + sh == cy1 - cy0
        sized = END_LETTER[0] * tall <= sh <= END_LETTER[1] * tall and sw <= END_WIDTH * tall
        on_row = min(sy1, y1) - max(sy0, y0) >= sh / 2
        held = any(min(sx1, ox1) > max(sx0, ox0) and min(sy1, oy1) > max(sy0, oy0) for ox0, oy0, ox1, oy1 in others)
        if touches_crop or not sized or not on_row or held:
            continue
        if sx1 <= x0 + tall / 4 and x0 - sx1 <= END_GAP * tall:
            left = min(left, sx0)
        elif sx0 >= x1 - tall / 4 and sx0 - x1 <= END_GAP * tall:
            right = max(right, sx1)
    if (left, right) == (x0, x1):
        return line
    room = int(tall * RETRY_ROOM)
    read = read_line(image[max(0, y0 - room) : y1 + room, max(0, left - room) : right + room])
    if read is None or read[1] < END_SCORE:
        return line
    # Read wider, the line is read less surely ("SChOOL"): only the letters
    # added at its ends are taken, and the line kept as read.
    text, new = line.text, read[0].upper()
    head, tail = text[:END_ANCHOR].upper(), text[-END_ANCHOR:].upper()
    if left < x0:
        start = new.find(head)
        if not 0 < start <= END_LETTERS or not new[:start].isalpha():
            return line
        text = read[0][:start] + text
    if right > x1:
        end = new.rfind(tail) + len(tail)
        if not 0 < len(new) - end <= END_LETTERS or not new[end:].isalpha() or new.rfind(tail) < 0:
            return line
        text += read[0][end:]
    return TextLine((int(left), y0, int(right), y1), text, line.score)


def _drops(old: str, new: str) -> bool:
    """`new` is `old` with characters left out."""
    rest = iter(old)
    return len(new) < len(old) and all(c in rest for c in new)
