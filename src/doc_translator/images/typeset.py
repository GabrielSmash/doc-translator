"""A translation lettered in its balloon: comic font, capitals, centered,
each line as wide as the balloon is at its height, as large as the
original letters where it fits."""

from functools import cache
from importlib.resources import files

import cv2
import numpy as np
import pymupdf

from .balloons import Balloon

FONT_DIR = files("doc_translator.images") / "fonts"
FONT = "ComicNeue-Bold.ttf"
FONT_NAME = "comicneue"
# Font size for an OCR line box this tall, when the capitals could not be
# measured: the box holds the capitals and some room (they are 61% of it in
# the comic).
SIZE_PER_LINE = 0.9
LEADING = 1.12
# Kept from the outline, in font sizes.
MARGIN = 0.45
# Sizes tried, as shares of the original's, largest first.
STEPS = [1 - 0.05 * i for i in range(11)]
# Below this share of the original size, the text is reported.
MIN_SCALE = 0.6
# How far the capitals of Comic Neue reach above the baseline, in sizes
# (measured on its "H").
CAP = 0.68


@cache
def _font() -> pymupdf.Font:
    return pymupdf.Font(fontfile=str(FONT_DIR / FONT))


def write(page: pymupdf.Page, balloon: Balloon, text: str, scale: float) -> float:
    """Letter `text` (in capitals) in `balloon` on `page` (`prepare`d); `scale`: page
    units per image pixel. Returns the size used, as a share of the
    original's (below MIN_SCALE when even the smallest did not fit). The
original is the size whose capitals are as tall as the balloon's, in their
color."""
    words = text.upper().split()
    if not words:
        return 1.0
    room = _room(balloon)
    base = balloon.cap_height / CAP if balloon.cap_height else balloon.line_height * SIZE_PER_LINE
    blue, green, red = balloon.ink
    tx0, ty0, tx1, ty1 = balloon.text_box
    center = ((tx0 + tx1) / 2, (ty0 + ty1) / 2)
    for step in STEPS:
        if lines := _fit(words, room, base * step, center):
            break
    else:
        step, lines = STEPS[-1] * 0.8, _fit(words, room, base * STEPS[-1] * 0.8, center, force=True)
    size = base * step
    for text_line, x, baseline in lines:
        page.insert_text(
            (x * scale, baseline * scale), text_line, fontname=FONT_NAME, fontsize=size * scale, color=(red / 255, green / 255, blue / 255)
        )
    return step


def prepare(page: pymupdf.Page) -> None:
    """Put the lettering font in `page`, once, before `write`."""
    page.insert_font(fontname=FONT_NAME, fontbuffer=_font().buffer)


def _room(balloon: Balloon) -> np.ndarray:
    """Where letters may go, as a mask in image pixels: the balloon's
    inside, or the text's box for an open balloon."""
    if balloon.mask is not None:
        return balloon.mask > 0
    if balloon.room_mask is not None:
        return balloon.room_mask > 0
    x0, y0, x1, y1 = (int(v) for v in balloon.room())
    width, height = max(x1 + 2, 1), max(y1 + 2, 1)
    mask = np.zeros((height, width), bool)
    mask[max(0, y0) : y1, max(0, x0) : x1] = True
    return mask


def _fit(
    words: list[str], room: np.ndarray, size: float, center: tuple[float, float], force: bool = False
) -> list[tuple[str, float, float]] | None:
    """Lines (text, left x, baseline) of `words` at `size`, centered on
    `center`, each within the room at its height; None if they do not fit."""
    margin = max(1, int(MARGIN * size))
    inside = cv2.erode(room.astype(np.uint8), np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)) > 0
    font = _font()
    space = font.text_length(" ", fontsize=size)
    widths = [font.text_length(w, fontsize=size) for w in words]
    line_height = size * LEADING
    cx, cy = center
    for count in range(1, len(words) + 1):
        top = cy - count * line_height / 2
        spans = [_span(inside, top + i * line_height, top + (i + 1) * line_height, cx) for i in range(count)]
        if not force and any(span is None for span in spans):
            if top < 0 or all(span is None for span in spans):
                return None
            continue
        lines, index = [], 0
        for i, span in enumerate(spans):
            left, right = span if span else (cx - size * 4, cx + size * 4)
            taken, width = [], 0.0
            while index < len(words):
                extra = widths[index] + (space if taken else 0)
                if width + extra > right - left and (taken or not force):
                    break
                taken.append(words[index])
                width += extra
                index += 1
            if not taken:
                break
            mid = (left + right) / 2
            baseline = top + i * line_height + (line_height + CAP * size) / 2
            lines.append((" ".join(taken), mid - width / 2, baseline))
        if index == len(words) and len(lines) == count:
            return lines
        if force and count == len(words):
            return lines
    return None


def _span(inside: np.ndarray, top: float, bottom: float, cx: float) -> tuple[float, float] | None:
    """The room's run around `cx` that is free from `top` to `bottom`."""
    height, width = inside.shape
    y0, y1 = int(max(0, top)), int(min(height, bottom + 1))
    if y0 >= y1 or top < 0 or bottom >= height:
        return None
    free = inside[y0:y1].all(axis=0)
    x = int(min(max(cx, 0), width - 1))
    if not free[x]:
        runs = np.flatnonzero(free)
        if runs.size == 0:
            return None
        x = int(runs[np.abs(runs - cx).argmin()])
    left = x
    while left > 0 and free[left - 1]:
        left -= 1
    right = x
    while right < width - 1 and free[right + 1]:
        right += 1
    return (float(left), float(right))
