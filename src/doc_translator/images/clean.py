"""The original letters painted over with their balloon's color."""

import cv2
import numpy as np

from .balloons import Balloon, letters

# Around each line's box, so antialiased edges go too.
PAD = 6
# Kept from the balloon's outline: only its inside is painted.
OUTLINE = 9
# Around marks the OCR left out, for their antialiased edges.
INK_PAD = 3
# A mark the OCR left out is at most this many line heights tall and wide.
STRAY_SIZE = 1.5
OPEN_PAD = 4


def erase(image: np.ndarray, balloons: list[Balloon]) -> None:
    """Paint, in place, the lines of each balloon with its color, inside
    its outline: a closed one's inside, an open one's region near its text
    (the lines' boxes cross its outline where they run close to it)."""
    height, width = image.shape[:2]
    for balloon in balloons:
        boxes = np.zeros((height, width), np.uint8)
        pad = OPEN_PAD if balloon.open else PAD
        # Sideways, an open balloon's lines reach a letter height further: a
        # letter the OCR left out of a line ("I" of "I NEVER", page 6) is
        # in the region there.
        side = int(balloon.line_height) if balloon.room_mask is not None else pad
        for line in balloon.lines:
            x0, y0, x1, y1 = line.box
            boxes[max(0, y0 - pad) : y1 + pad, max(0, x0 - side) : x1 + side] = 255
        if balloon.mask is not None:
            inside = cv2.erode(balloon.mask, np.ones((OUTLINE, OUTLINE), np.uint8))
            boxes |= _strays(image, balloon) & inside
            boxes &= inside
        elif balloon.room_mask is not None:
            boxes &= balloon.room_mask
        image[boxes > 0] = balloon.color


def _strays(image: np.ndarray, balloon: Balloon) -> np.ndarray:
    """Marks near a balloon's text that the OCR left out of its lines (the
    dots of "LATER...", an "I" set apart; page 6): shapes of ink as small as
    a letter, all within a line height of the text. Larger shapes, or shapes
    running further, are outlines and art: a region taken for a balloon can
    hold a wall its tails reach (page 6)."""
    height, width = image.shape[:2]
    reach = int(balloon.line_height)
    tx0, ty0, tx1, ty1 = balloon.text_box
    # Looked at twice as far, to tell the shapes that run further.
    x0, y0 = max(0, tx0 - 2 * reach), max(0, ty0 - 2 * reach)
    x1, y1 = min(width, tx1 + 2 * reach), min(height, ty1 + 2 * reach)
    ink = letters(image[y0:y1, x0:x1], balloon.color)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    strays = np.zeros((height, width), np.uint8)
    largest = STRAY_SIZE * balloon.line_height
    for label in range(1, count):
        left, top, w, h, _ = stats[label]
        sx0, sy0 = x0 + left, y0 + top
        near = sx0 >= tx0 - reach and sy0 >= ty0 - reach and sx0 + w <= tx1 + reach and sy0 + h <= ty1 + reach
        if near and w <= largest and h <= largest:
            strays[y0:y1, x0:x1][labels == label] = 255
    return cv2.dilate(strays, np.ones((2 * INK_PAD + 1, 2 * INK_PAD + 1), np.uint8))
