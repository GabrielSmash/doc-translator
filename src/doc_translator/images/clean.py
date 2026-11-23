"""The original letters painted over with their balloon's color."""

import cv2
import numpy as np

from .balloons import Balloon

# Around each line's box, so antialiased edges go too.
PAD = 6
# Kept from the balloon's outline: only its inside is painted.
OUTLINE = 9
OPEN_PAD = 4


def erase(image: np.ndarray, balloons: list[Balloon]) -> None:
    """Paint, in place, the lines of each balloon with its color: inside
    its outline for a closed one, the lines' boxes for an open one."""
    height, width = image.shape[:2]
    for balloon in balloons:
        boxes = np.zeros((height, width), np.uint8)
        pad = OPEN_PAD if balloon.open else PAD
        for line in balloon.lines:
            x0, y0, x1, y1 = line.box
            boxes[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad] = 255
        if balloon.mask is not None:
            inside = cv2.erode(balloon.mask, np.ones((OUTLINE, OUTLINE), np.uint8))
            boxes &= inside
        image[boxes > 0] = balloon.color
