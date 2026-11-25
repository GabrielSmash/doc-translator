"""The original letters painted over with their balloon's color."""

import cv2
import numpy as np

from .balloons import Balloon, letters

# Around each line's box, so antialiased edges go too.
PAD = 6
# Kept from the balloon's outline: only its inside is painted.
OUTLINE = 9
# Around letters painted one by one, for their antialiased edges.
INK_PAD = 4
# A mark the OCR left out is at most this many line heights tall and wide.
STRAY_SIZE = 1.5
OPEN_PAD = 4
# A shape of ink is a letter of an open balloon when this share of it is in
# its lines' boxes; less, it runs out of them: art (page 6: hair under a
# balloon's text).
IN_LINES = 0.5


def erase(image: np.ndarray, balloons: list[Balloon]) -> None:
    """Paint, in place, the letters of each balloon with its color.

    A closed balloon: its lines' boxes, within its outline, and the marks
    near its text the OCR left out of them.

    An open one: its letters one by one, within its region. Its region
    runs into the panel, where light art (a face, a light background) is
    part of it, and dark art (hair) is a hole in it: its lines' boxes,
    painted whole, cut the art they reach (page 6).
    """
    height, width = image.shape[:2]
    for balloon in balloons:
        pad = OPEN_PAD if balloon.open else PAD
        boxes = np.zeros((height, width), np.uint8)
        for line in balloon.lines:
            x0, y0, x1, y1 = line.box
            boxes[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad] = 255
        shapes, strays = _shapes(image, balloon, boxes)
        if balloon.mask is not None:
            inside = cv2.erode(balloon.mask, np.ones((OUTLINE, OUTLINE), np.uint8))
            paint = (boxes | strays) & inside
        else:
            paint = shapes | strays
            if balloon.room_mask is not None:
                paint &= balloon.room_mask
        image[paint > 0] = balloon.color


def _shapes(image: np.ndarray, balloon: Balloon, boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The shapes of ink around a balloon's text, widened by INK_PAD:
    those mostly in its lines' boxes (its letters), and the marks the OCR
    left out of them (the dots of "LATER...", an "I" set apart; page 6):
    shapes as small as a letter, all within a line height of the text.
    Larger shapes, or running further, are outlines and art: a region taken
    for a balloon can hold a wall its tails reach (page 6)."""
    height, width = image.shape[:2]
    reach = int(balloon.line_height)
    tx0, ty0, tx1, ty1 = balloon.text_box
    # Looked at twice as far, to tell the shapes that run further.
    x0, y0 = max(0, tx0 - 2 * reach), max(0, ty0 - 2 * reach)
    x1, y1 = min(width, tx1 + 2 * reach), min(height, ty1 + 2 * reach)
    ink = letters(image[y0:y1, x0:x1], balloon.color)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    in_boxes = np.bincount(labels[boxes[y0:y1, x0:x1] > 0], minlength=count)
    lettered, stray = np.zeros(count, bool), np.zeros(count, bool)
    largest = STRAY_SIZE * balloon.line_height
    for label in range(1, count):
        left, top, w, h, area = stats[label]
        sx0, sy0 = x0 + left, y0 + top
        near = sx0 >= tx0 - reach and sy0 >= ty0 - reach and sx0 + w <= tx1 + reach and sy0 + h <= ty1 + reach
        stray[label] = near and w <= largest and h <= largest
        lettered[label] = in_boxes[label] >= IN_LINES * area
    widen = np.ones((2 * INK_PAD + 1, 2 * INK_PAD + 1), np.uint8)
    shapes, strays = np.zeros((height, width), np.uint8), np.zeros((height, width), np.uint8)
    shapes[y0:y1, x0:x1] = cv2.dilate(lettered[labels].astype(np.uint8) * 255, widen)
    strays[y0:y1, x0:x1] = cv2.dilate(stray[labels].astype(np.uint8) * 255, widen)
    return shapes, strays
