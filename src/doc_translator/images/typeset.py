"""A translation lettered in its balloon: comic font, capitals, centered,
each line as wide as the balloon is at its height, as large as the
original letters where it fits; emphasized words in bold italic."""

import re
from functools import cache
from importlib.resources import files

import cv2
import numpy as np
import pymupdf

from .balloons import Balloon

FONT_DIR = files("doc_translator.images") / "fonts"
FONT = "ComicNeue-Bold.ttf"
FONT_NAME = "comicneue"
EMPHASIS_FONT = "ComicNeue-BoldItalic.ttf"
EMPHASIS_FONT_NAME = "comicneuebi"
# Comic Neue Bold Italic is as heavy as Bold; emphasis is heavier in comics
# (a stroke 1.5 times the plain one): outlined in its color this much, in
# font sizes, its stroke goes from 0.10 to 0.15 of the size.
EMPHASIS_OUTLINE = 0.05
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
def _font(name: str = FONT) -> pymupdf.Font:
    return pymupdf.Font(fontfile=str(FONT_DIR / name))


def write(
    page: pymupdf.Page, balloon: Balloon, text: str, scale: float, emphasized: list[tuple[int, int]] = ()
) -> float:
    """Letter `text` (in capitals) in `balloon` on `page` (`prepare`d); `scale`: page
    units per image pixel; `emphasized`: spans of `text` lettered in bold
    italic. Returns the size used, as a share of the original's (below
    MIN_SCALE when even the smallest did not fit). The original is the size
    whose capitals are as tall as the balloon's, in their color."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not spans:
        return 1.0
    words = [text[start:end].upper() for start, end in spans]
    heavy = [any(start < e and s < end for s, e in emphasized) for start, end in spans]
    fonts = [_font(EMPHASIS_FONT) if h else _font() for h in heavy]
    room = _room(balloon)
    tx0, ty0, tx1, ty1 = balloon.text_box
    pivot = ((tx0 + tx1) / 2, (ty0 + ty1) / 2)
    # A slanted text (an arrow's, page 2): its room turned level around the
    # text's middle, fitted as any, and written turned back.
    # Where the original lines were: each new line goes to the part of the
    # room the original text was in at its height.
    guide = np.zeros(room.shape, np.uint8)
    for line in balloon.lines:
        lx0, ly0, lx1, ly1 = (max(0, v) for v in line.box)
        guide[ly0:ly1, lx0:lx1] = 1
    if angle := balloon.angle:
        turn = cv2.getRotationMatrix2D(pivot, angle, 1.0)
        room = cv2.warpAffine(room.astype(np.uint8), turn, (room.shape[1], room.shape[0]), flags=cv2.INTER_NEAREST) > 0
        guide = cv2.warpAffine(guide, turn, (guide.shape[1], guide.shape[0]), flags=cv2.INTER_NEAREST)
    guide = guide > 0
    base = balloon.cap_height / CAP if balloon.cap_height else balloon.line_height * SIZE_PER_LINE
    blue, green, red = balloon.ink
    # Where the original text was; failing that, the middle of the room: a
    # caption's text can sit near its top, closer than the margin allows
    # ("MINUTES LATER...", page 6).
    ys, xs = np.nonzero(room)
    centers = [pivot, (pivot[0], (ys.min() + ys.max()) / 2)]

    def widths(size: float) -> list[float]:
        return [
            font.text_length(word, fontsize=size) + (EMPHASIS_OUTLINE * size if h else 0)
            for font, word, h in zip(fonts, words, heavy)
        ]

    for step in STEPS:
        if lines := next(filter(None, (_fit(widths(base * step), room, base * step, c, guide=guide) for c in centers)), None):
            break
    else:
        step = STEPS[-1] * 0.8
        lines = _fit(widths(base * step), room, base * step, centers[0], force=True, guide=guide)
    size = base * step
    space = _font().text_length(" ", fontsize=size)
    sizes = widths(size)
    color = (red / 255, green / 255, blue / 255)
    # PyMuPDF turns text counterclockwise for a positive angle.
    morph = (pymupdf.Point(pivot[0] * scale, pivot[1] * scale), pymupdf.Matrix(-angle)) if angle else None
    for first, last, x, baseline in lines:
        for index in range(first, last):
            page.insert_text(
                (x * scale, baseline * scale),
                words[index],
                fontname=EMPHASIS_FONT_NAME if heavy[index] else FONT_NAME,
                fontsize=size * scale,
                color=color,
                fill=color,
                render_mode=2 if heavy[index] else 0,
                border_width=EMPHASIS_OUTLINE if heavy[index] else 0,
                morph=morph,
            )
            x += sizes[index] + space
    return step


def prepare(page: pymupdf.Page) -> None:
    """Put the lettering fonts in `page`, once, before `write`."""
    page.insert_font(fontname=FONT_NAME, fontbuffer=_font().buffer)
    page.insert_font(fontname=EMPHASIS_FONT_NAME, fontbuffer=_font(EMPHASIS_FONT).buffer)


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
    widths: list[float],
    room: np.ndarray,
    size: float,
    center: tuple[float, float],
    force: bool = False,
    guide: np.ndarray | None = None,
) -> list[tuple[int, int, float, float]] | None:
    """Lines (first word, end word, left x, baseline) of words this wide at
    `size`, centered on `center`, each within the room at its height; None
    if they do not fit."""
    margin = max(1, int(MARGIN * size))
    inside = cv2.erode(room.astype(np.uint8), np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)) > 0
    space = _font().text_length(" ", fontsize=size)
    line_height = size * LEADING
    cx, cy = center
    for count in range(1, len(widths) + 1):
        top = cy - count * line_height / 2
        spans = [_span(inside, top + i * line_height, top + (i + 1) * line_height, cx, guide) for i in range(count)]
        if not force and any(span is None for span in spans):
            if top < 0 or all(span is None for span in spans):
                return None
            continue
        lines, index = [], 0
        for i, span in enumerate(spans):
            left, right = span if span else (cx - size * 4, cx + size * 4)
            first, width = index, 0.0
            while index < len(widths):
                extra = widths[index] + (space if index > first else 0)
                if width + extra > right - left and (index > first or not force):
                    break
                width += extra
                index += 1
            if index == first:
                break
            mid = (left + right) / 2
            baseline = top + i * line_height + (line_height + CAP * size) / 2
            lines.append((first, index, mid - width / 2, baseline))
        if index == len(widths) and len(lines) == count:
            return lines
        if force and count == len(widths):
            return lines
    return None


def _span(
    inside: np.ndarray, top: float, bottom: float, cx: float, guide: np.ndarray | None = None
) -> tuple[float, float] | None:
    """The room's run free from `top` to `bottom`: the one where the original
    text was at that height (`guide`: its lines' boxes), or around `cx`.
    In two balloons joined (page 14), the original text goes from one to the
    other; centered on the whole, lines fell on the narrow join."""
    height, width = inside.shape
    y0, y1 = int(max(0, top)), int(min(height, bottom + 1))
    if y0 >= y1 or top < 0 or bottom >= height:
        return None
    free = inside[y0:y1].all(axis=0)
    if not free.any():
        return None
    edges = np.flatnonzero(np.diff(np.concatenate(([0], free.astype(np.int8), [0]))))
    runs = list(zip(edges[::2], edges[1::2] - 1))
    if guide is not None:
        text = guide[y0:y1].any(axis=0)
        overlap = [int(text[left : right + 1].sum()) for left, right in runs]
        if max(overlap) > 0:
            left, right = runs[int(np.argmax(overlap))]
            return (float(left), float(right))
    x = int(min(max(cx, 0), width - 1))
    for left, right in runs:
        if left <= x <= right:
            return (float(left), float(right))
    left, right = min(runs, key=lambda run: min(abs(run[0] - cx), abs(run[1] - cx)))
    return (float(left), float(right))
