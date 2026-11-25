"""Lines of a balloon the OCR of the page missed.

Read page-wide, the OCR can leave out a line of a balloon: slanted more
than the others ("OCTOPUS??" on the cover's question mark), or set apart.
Its letters are then neither translated nor erased whole. Ink as tall as
the balloon's letters that no line holds is read again, turned level at
the balloon's slant.
"""

import math
from difflib import SequenceMatcher

import cv2
import numpy as np

from . import ocr
from .balloons import COLOR_TOLERANCE, Balloon, letters
from .ocr import TextLine

# Looked for this many line heights around the balloon's text.
NEAR = 1.5
# Around a line's box, ink is its own (antialiasing, a letter's overhang).
LINE_PAD = 4
# Shapes this tall, as a share of the balloon's line height, are letters.
MIN_LETTER, MAX_LETTER = 0.3, 1.2
# Left-out letters: at least this many, this wide in line heights; a
# letter is left out when at most this share of it is in a line's box.
MIN_LETTERS = 3
MIN_WIDTH = 1.5
HELD = 0.2
# Read again from the left-out ink's box with this much room, in line heights.
ROOM = 0.5
# Looked for again this many times in a balloon.
TRIES = 3
# A line read again this similar to one of the balloon's is that one.
SAME_LINE = 0.7
# Turns tried: the balloon's slant, then upright and a little either way.
EXTRA_TURNS = (0.0, 5.0, -5.0, 10.0, -10.0)


def complete(
    image: np.ndarray, balloon: Balloon, lines: list[TextLine], balloons: list[Balloon] = ()
) -> TextLine | None:
    """Add to `balloon` a line near its text the page's OCR missed, and
    return it. `lines`: all the page's lines; `balloons`: the page's, a
    left-out line going to the one whose text is nearest."""
    box = _left_out(image, balloon, lines)
    if box is None or any(_distance(box, other.text_box) < _distance(box, balloon.text_box) for other in balloons):
        return None
    x0, y0, x1, y1 = box
    # The left-out row can be only part of its line (a letter joined to one
    # of the line above is not a shape of its own): read across the
    # balloon's text, the lines already read left out.
    tx0, _, tx1, _ = balloon.text_box
    room = int(ROOM * balloon.line_height)
    rise = int(abs(math.tan(math.radians(balloon.angle))) * (max(tx1, x1) - min(tx0, x0)))
    height, width = image.shape[:2]
    cx0, cx1 = max(0, min(tx0, x0) - room), min(width, max(tx1, x1) + room)
    cy0, cy1 = max(0, y0 - room - rise), min(height, y1 + room + rise)
    crop = image[cy0:cy1, cx0:cx1]
    known = [_letters(line.text) for line in balloon.lines]
    for turn in dict.fromkeys((balloon.angle, *EXTRA_TURNS)):
        level, matrix = _turned(crop, turn, balloon.color)
        found = [
            line
            for line in ocr.read_crop(level)
            if line.score >= ocr.RETRY_SCORE and _word(line.text) and not _read_before(_letters(line.text), known)
        ]
        if found:
            text = " ".join(line.text for line in sorted(found, key=lambda line: (line.box[1], line.box[0])))
            # Where it was read, on the page: its letters are erased there.
            back = cv2.invertAffineTransform(matrix)
            corners = np.array(
                [[(lx, ly) for lx in (line.box[0], line.box[2]) for ly in (line.box[1], line.box[3])] for line in found],
                np.float32,
            ).reshape(-1, 1, 2)
            page = cv2.transform(corners, back).reshape(-1, 2) + (cx0, cy0)
            read = (int(page[:, 0].min()), int(page[:, 1].min()), int(page[:, 0].max()), int(page[:, 1].max()))
            box = (min(x0, read[0]), min(y0, read[1]), max(x1, read[2]), max(y1, read[3]))
            added = TextLine(box, text, min(line.score for line in found), balloon.angle, balloon.line_height)
            # Read in its place by the row found: its box, turned back, also
            # reaches above it.
            row_middle = (y0 + y1) / 2
            before = [line for line in balloon.lines if (line.box[1] + line.box[3]) / 2 < row_middle]
            balloon.lines = [*before, added, *[line for line in balloon.lines if line not in before]]
            if balloon.room_mask is not None:
                balloon.room_mask |= _own_color(image, balloon, box)
            return added
    return None


def _left_out(image: np.ndarray, balloon: Balloon, lines: list[TextLine]) -> tuple[int, int, int, int] | None:
    """The box of a row of letters near the balloon's text, on its color,
    that no line of the page holds."""
    height, width = image.shape[:2]
    tall = balloon.line_height
    reach = int(NEAR * tall)
    tx0, ty0, tx1, ty1 = balloon.text_box
    x0, y0, x1, y1 = max(0, tx0 - reach), max(0, ty0 - reach), min(width, tx1 + reach), min(height, ty1 + reach)
    crop = image[y0:y1, x0:x1]
    # On the balloon's color: its pixels, and the letters they surround.
    own = np.abs(crop.astype(np.int16) - np.array(balloon.color, np.int16)).max(axis=2) <= COLOR_TOLERANCE
    size = 2 * int(tall / 2) + 1
    own = cv2.morphologyEx(own.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((size, size), np.uint8)) > 0
    ink = (letters(crop, balloon.color) & own).astype(np.uint8)
    held = np.zeros_like(ink)
    for line in lines + balloon.lines:
        if line.quad:
            cv2.fillPoly(held, [np.array(line.quad, np.int32) - (x0, y0)], 1)
        else:
            lx0, ly0, lx1, ly1 = line.box
            held[max(0, ly0 - y0) : max(0, ly1 - y0), max(0, lx0 - x0) : max(0, lx1 - x0)] = 1
    held = cv2.dilate(held, np.ones((2 * LINE_PAD + 1, 2 * LINE_PAD + 1), np.uint8)) > 0
    count, labels, stats, centers = cv2.connectedComponentsWithStats(ink, connectivity=8)
    in_lines = np.bincount(labels[held], minlength=count)
    shapes = [
        label
        for label in range(1, count)
        if MIN_LETTER * tall <= stats[label, cv2.CC_STAT_HEIGHT] <= MAX_LETTER * tall
        and stats[label, cv2.CC_STAT_WIDTH] <= 2 * tall
        and in_lines[label] <= HELD * stats[label, cv2.CC_STAT_AREA]
    ]
    row = _longest_row(shapes, stats, centers, tall)
    if len(row) < MIN_LETTERS:
        return None
    left = min(stats[i, cv2.CC_STAT_LEFT] for i in row)
    right = max(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] for i in row)
    top = min(stats[i, cv2.CC_STAT_TOP] for i in row)
    bottom = max(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] for i in row)
    if right - left < MIN_WIDTH * tall:
        return None
    return (x0 + int(left), y0 + int(top), x0 + int(right), y0 + int(bottom))


def _longest_row(shapes: list[int], stats: np.ndarray, centers: np.ndarray, tall: float) -> list[int]:
    """The longest chain of shapes left to right, each next to the one
    before (a letter's gap) at about its height (a slanted line rises or
    falls a little from letter to letter)."""
    order = sorted(shapes, key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    best: list[int] = []
    for start in order:
        row = [start]
        for i in order:
            last = row[-1]
            gap = stats[i, cv2.CC_STAT_LEFT] - (stats[last, cv2.CC_STAT_LEFT] + stats[last, cv2.CC_STAT_WIDTH])
            if stats[i, cv2.CC_STAT_LEFT] > stats[last, cv2.CC_STAT_LEFT] and gap <= tall / 2 and abs(centers[i][1] - centers[last][1]) <= tall / 2:
                row.append(i)
        best = max(best, row, key=len)
    return best


def _own_color(image: np.ndarray, balloon: Balloon, box: tuple[int, int, int, int]) -> np.ndarray:
    """The balloon's color around `box`, and the letters it surrounds, as a
    mask of the page (for the room of an open balloon a line was added to)."""
    height, width = image.shape[:2]
    reach = int(balloon.line_height / 2)
    x0, y0, x1, y1 = max(0, box[0] - reach), max(0, box[1] - reach), min(width, box[2] + reach), min(height, box[3] + reach)
    own = np.abs(image[y0:y1, x0:x1].astype(np.int16) - np.array(balloon.color, np.int16)).max(axis=2) <= COLOR_TOLERANCE
    size = 2 * int(balloon.line_height / 2) + 1
    mask = np.zeros((height, width), np.uint8)
    mask[y0:y1, x0:x1] = cv2.morphologyEx(own.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((size, size), np.uint8)) * 255
    return mask


def _turned(crop: np.ndarray, angle: float, color: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """`crop` turned by `angle` degrees (counterclockwise, as a line
    descending to the right by that much is made level), on the balloon's
    color; and the turn's matrix."""
    height, width = crop.shape[:2]
    turn = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    if not angle:
        return crop, turn
    return cv2.warpAffine(crop, turn, (width, height), borderValue=tuple(int(c) for c in color)), turn


def _read_before(text: str, known: list[str]) -> bool:
    """Whether `text` is one of the balloon's lines read again, maybe a
    letter different ("IHEMIGHTY" for "THEMIGHTY")."""
    return any(text in k or SequenceMatcher(None, text, k).ratio() >= SAME_LINE for k in known)


def _distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Between two boxes: 0 when they overlap."""
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _letters(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())


def _word(text: str) -> bool:
    return sum(c.isalpha() for c in text) >= MIN_LETTERS
