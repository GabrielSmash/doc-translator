"""Emphasized words of a balloon ("MY **ARMS** DID IT!"): lettered heavier
and slanted, where the rest stands upright (ADR 0008).

The OCR gives lines, not words, and drops spaces. Words are the letters
closer to each other, row by row, than a space: slanted letters leave no
blank column between words. Each is measured, and the letters of the line
text are spread over the words' ink to tell which word each letter is in.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import cv2
import numpy as np

from .balloons import Balloon, letters

# A blank this share of the capitals' height separates words; letters of
# a word are closer.
WORD_GAP = 0.35
# Emphasized: a stroke this many times the comic's plain stroke, slanted
# right by this many degrees or more. On the comic, plain words have a
# stroke of 18-19% of their capitals and stand within 8 degrees; the
# emphasized ones, 26-30% and 14-25 degrees. Comic Neue Bold Italic, the
# emphasis lettered in a translation, leans 9.
HEAVY = 1.3
SLANT = 8
# Slants tried, in degrees.
SLANTS = range(-25, 26)
WORD = re.compile(r"[^\s.,;:!?\"]+")


@dataclass
class Word:
    x0: int  # in the line's box
    x1: int
    stroke: float  # as a share of the balloon's capitals
    slant: float  # degrees, positive leaning right


def measure(image: np.ndarray, balloon: Balloon) -> list[list[Word]]:
    """The words of each of `balloon`'s lines."""
    # Slant and stroke are measured on upright letters.
    if not balloon.cap_height or balloon.angle:
        return [[] for _ in balloon.lines]
    measured = []
    for line in balloon.lines:
        x0, y0, x1, y1 = (max(0, v) for v in line.box)
        ink = letters(image[y0:y1, x0:x1], balloon.color)
        words = []
        for start, end, word in _words(ink, WORD_GAP * balloon.cap_height):
            # Not a word: a sliver of the lines above and below, cut by the
            # line's box, or a mark.
            if end - start >= balloon.cap_height / 2 and np.ptp(np.flatnonzero(word.any(axis=1))) >= balloon.cap_height / 2:
                words.append(Word(start, end, _stroke(word) / balloon.cap_height, _slant(word)))
        measured.append(words)
    return measured


def plain_stroke(measured: list[list[list[Word]]]) -> float:
    """The stroke of most words (of every balloon of a comic)."""
    strokes = [w.stroke for balloon in measured for line in balloon for w in line]
    return float(np.median(strokes)) if strokes else 0.0


def phrases(balloon: Balloon, words: list[list[Word]], text: str, plain: float) -> list[str]:
    """The emphasized phrases of `text` (the balloon's lines joined and
    spaced again), each a run of emphasized words."""
    flags = []  # one per letter (not space) of the lines, as read
    for line, line_words in zip(balloon.lines, words):
        flags += _letter_flags(line.text, line_words, plain)
    if not any(flags):
        return []
    read = "".join(c for line in balloon.lines for c in line.text if not c.isspace())
    # The text was joined and spaced again (a hyphen of a cut word dropped):
    # its letters are matched to the lines' letters.
    kept = [i for i, c in enumerate(text) if not c.isspace()]
    flagged = [False] * len(text)
    matcher = SequenceMatcher(None, read, "".join(text[i] for i in kept), autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            flagged[kept[block.b + k]] = flags[block.a + k]
    # Words end at spaces and at punctuation the OCR glued ("RIGHT!BY").
    words = [(m.start(), m.end()) for m in WORD.finditer(text)]
    emphasized = [sum(flagged[start:end]) * 2 > end - start for start, end in words]
    found, run = [], []
    for (start, end), on in zip(words, emphasized):
        if on:
            run.append((start, end))
        elif run:
            found.append(text[run[0][0] : run[-1][1]])
            run = []
    if run:
        found.append(text[run[0][0] : run[-1][1]])
    return found


def _letter_flags(text: str, words: list[Word], plain: float) -> list[bool]:
    """For each letter (not space) of `text`: whether it is in an emphasized
    word. The letters are spread evenly over the words' ink."""
    count = sum(1 for c in text if not c.isspace())
    if not words or not count:
        return [False] * count
    widths = np.array([w.x1 - w.x0 for w in words], float)
    ends = np.cumsum(widths)
    centers = (np.arange(count) + 0.5) * ends[-1] / count
    heavy = [plain and w.stroke >= HEAVY * plain and w.slant >= SLANT for w in words]
    return [bool(heavy[int(np.searchsorted(ends, c))]) for c in centers]


def _words(ink: np.ndarray, gap: float) -> list[tuple[int, int, np.ndarray]]:
    """The words of a line's letters, left to right: (left, right, its ink).
    Letters less than `gap` apart on some row are one word."""
    width = 2 * int(gap / 2) + 1
    joined = cv2.dilate(ink, np.ones((1, width), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    words = []
    for label in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_LEFT]):
        own = (labels == label) & (ink > 0)
        columns = np.flatnonzero(own.any(axis=0))
        if columns.size:
            words.append((int(columns[0]), int(columns[-1]) + 1, own.astype(np.uint8)))
    return words


def _stroke(ink: np.ndarray) -> float:
    """Width of the strokes: twice the distance to the edge along their
    middle."""
    distance = cv2.distanceTransform(ink, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    middle = (distance > 0.5) & (distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)))
    return float(np.median(distance[middle]) * 2) if middle.any() else 0.0


def _slant(ink: np.ndarray) -> float:
    """The slant that, undone, makes the stems most upright: the columns'
    ink most concentrated."""
    height, width = ink.shape
    best, score = 0.0, -1.0
    for angle in SLANTS:
        shear = np.tan(np.deg2rad(angle))
        matrix = np.float32([[1, shear, max(0.0, -shear * height)], [0, 1, 0]])
        sheared = cv2.warpAffine(ink, matrix, (int(width + abs(shear) * height) + 1, height), flags=cv2.INTER_NEAREST)
        columns = sheared.sum(axis=0, dtype=np.float64)
        if (value := float((columns**2).sum())) > score:
            best, score = float(angle), value
    return best
