"""Lines of text -> balloons and captions: where each text sits, its shape,
its color, and the room to write a translation in.

A balloon's inside is a region of one flat color closed by a dark outline.
Flooding the light pixels around a line from just above and below it (not
inside a letter's hole) gives that region; lines that reach the same
region are one balloon. A region much larger than its text is open (its
outline has a gap, or there is none): only the lines' boxes count then.
Text over the art (titles, sound effects) reaches no flat region and is
left as it is.
"""

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np

from .ocr import TextLine

# Pixels darker than this are letters and outlines: walls of the flood.
DARK = 110
# Pixels lighter than this can be a balloon's inside.
LIGHT = 170
# A region larger than this share of the page is the page, not a balloon.
MAX_PAGE_SHARE = 0.15
# A region at least this many times its line's box holds that line.
MIN_REGION_SHARE = 2
# A region this much larger than its text (and this many pixels more) is
# open: its text box is the room.
OPEN_WIDTH, OPEN_HEIGHT = 2.2, 2.5
OPEN_MARGIN_X, OPEN_MARGIN_Y = 80, 120
# A closed balloon fills 70% of its box or more (page 5 of the comic); one
# leaked into its panel, 36%.
MIN_FILL = 0.55
# Seeds along a line's box, above and below it.
SEEDS = 12
SEED_OFFSET = 4
# Gutters between panels are lighter than this; a panel covers at least
# this share of the page.
GUTTER = 235
MIN_PANEL_SHARE = 0.02
# A region filling this share of its box is a rectangle (a caption).
RECTANGLE_FILL = 0.9
# The room inside an ellipse: its inscribed box, from each side.
ELLIPSE_INSET = 0.15
ROOM_MARGIN = 10


@dataclass
class Balloon:
    lines: list[TextLine]
    box: tuple[int, int, int, int]  # the region's bounds (the text's, open)
    color: tuple[int, int, int]  # BGR
    mask: np.ndarray | None = field(default=None, repr=False)  # the region with its letters filled
    round: bool = True
    open: bool = False
    # An open balloon's room: its region near its text (the mask is for
    # closed balloons, whose whole inside is erased within).
    room_mask: np.ndarray | None = field(default=None, repr=False)

    @property
    def text_box(self) -> tuple[int, int, int, int]:
        boxes = [line.box for line in self.lines]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    @property
    def line_height(self) -> float:
        return float(np.median([line.box[3] - line.box[1] for line in self.lines]))

    def room(self) -> tuple[float, float, float, float]:
        """Where a translation can be written: the inside of the balloon
        (an ellipse's inscribed box, a caption's rectangle), and at least
        the original text's box."""
        tx0, ty0, tx1, ty1 = self.text_box
        if self.open:
            return (tx0 - ROOM_MARGIN, ty0 - ROOM_MARGIN / 2, tx1 + ROOM_MARGIN, ty1 + ROOM_MARGIN)
        x0, y0, x1, y1 = self.box
        inset = ELLIPSE_INSET if self.round else 0.02
        dx = inset * (x1 - x0) + ROOM_MARGIN
        dy = inset * (y1 - y0) + ROOM_MARGIN * 0.8
        return (min(x0 + dx, tx0), min(y0 + dy, ty0), max(x1 - dx, tx1), max(y1 - dy, ty1))


def find(image: np.ndarray, lines: list[TextLine]) -> list[Balloon]:
    """The balloons of `image` holding `lines`, in reading order."""
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    walls = np.where(gray < DARK, 255, 0).astype(np.uint8)
    region_of = np.zeros((height, width), np.int32)
    regions: dict[int, dict] = {}
    owners: dict[int, list[TextLine]] = {}

    def region_at(x: int, y: int) -> int:
        if region_of[y, x]:
            return int(region_of[y, x])
        mask = np.zeros((height + 2, width + 2), np.uint8)
        cv2.floodFill(walls.copy(), mask, (x, y), 128)
        filled = mask[1:-1, 1:-1].astype(bool)
        number = len(regions) + 1
        region_of[filled & (region_of == 0)] = number
        ys, xs = np.nonzero(filled)
        regions[number] = {"area": int(filled.sum()), "box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))}
        return number

    for line in lines:
        x0, y0, x1, y1 = line.box
        step = max(1, (x1 - x0) // SEEDS)
        votes: dict[int, int] = {}
        for x in range(max(0, x0), min(width, x1), step):
            for y in (y0 - SEED_OFFSET, y1 + SEED_OFFSET):
                if 0 <= y < height and gray[y, x] > LIGHT:
                    number = region_at(x, y)
                    if regions[number]["area"] >= MIN_REGION_SHARE * (x1 - x0) * (y1 - y0):
                        votes[number] = votes.get(number, 0) + 1
        if votes:
            owners.setdefault(max(votes, key=votes.get), []).append(line)

    balloons = []
    for number, own in owners.items():
        region = regions[number]
        if region["area"] > MAX_PAGE_SHARE * width * height:
            continue  # text over the page's art
        whole = region_of == number
        # Balloons touching each other are one region: their texts stand
        # side by side, in columns no line crosses.
        columns = _columns(own)
        cuts = [0] + [(max(l.box[2] for l in a) + min(l.box[0] for l in b)) // 2 for a, b in itertools.pairwise(columns)] + [width]
        for column, left, right in zip(columns, cuts, cuts[1:]):
            inside = whole.copy()
            inside[:, :left] = False
            inside[:, right:] = False
            balloons.append(_balloon(image, column, inside))
    return reading_order(balloons, panels(gray))


def _balloon(image: np.ndarray, lines: list[TextLine], inside: np.ndarray) -> Balloon:
    ys, xs = np.nonzero(inside)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    balloon = Balloon(sorted(lines, key=lambda line: (line.box[1], line.box[0])), (x0, y0, x1, y1), _color(image, inside))
    tx0, ty0, tx1, ty1 = balloon.text_box
    # Leaked through a gap into the panel: a shape filling little of its
    # box (a balloon fills 70% of it, a caption all of it).
    leaked = len(xs) / max(1, (x1 - x0) * (y1 - y0)) < MIN_FILL
    if leaked or x1 - x0 > OPEN_WIDTH * (tx1 - tx0) + OPEN_MARGIN_X or y1 - y0 > OPEN_HEIGHT * (ty1 - ty0) + OPEN_MARGIN_Y:
        balloon.open = True
        balloon.box = balloon.text_box
        # The region runs into the panel: its most common color can be the
        # panel's ("yellow" for a white balloon on a yellow panel).
        near = np.zeros_like(inside)
        near[max(0, ty0 - 8) : ty1 + 8, max(0, tx0 - 8) : tx1 + 8] = True
        balloon.color = _color(image, inside & near)
        # Near its text the balloon still has its outline: the room is the
        # region there, not the text's box (whose corners cross it).
        reach = int(balloon.line_height)
        around = np.zeros_like(inside)
        around[max(0, ty0 - reach) : ty1 + reach, max(0, tx0 - reach) : tx1 + reach] = True
        balloon.room_mask = _solid(inside & around)
    else:
        balloon.mask = _solid(inside)
        # With its letters: a caption fills its box whatever its text.
        balloon.round = np.count_nonzero(balloon.mask) < RECTANGLE_FILL * (x1 - x0 + 1) * (y1 - y0 + 1)
    return balloon


def _columns(lines: list[TextLine]) -> list[list[TextLine]]:
    """`lines` in groups whose horizontal extents overlap, left to right."""
    groups: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda l: l.box[0]):
        if groups and line.box[0] < max(l.box[2] for l in groups[-1]):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def panels(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """The panels of a page: the areas the white gutters separate."""
    height, width = gray.shape
    ink = np.where(gray < GUTTER, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in contours]
    return [(x, y, x + w, y + h) for x, y, w, h in boxes if w * h >= MIN_PANEL_SHARE * width * height]


def reading_order(balloons: list[Balloon], panel_boxes: list[tuple[int, int, int, int]]) -> list[Balloon]:
    """Panel by panel, in reading order; in a panel, top to bottom and left
    to right. A balloon belongs to the panel holding its center."""

    def panel_of(balloon: Balloon) -> int:
        x0, y0, x1, y1 = balloon.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        inside = [i for i, (px0, py0, px1, py1) in enumerate(panel_boxes) if px0 <= cx <= px1 and py0 <= cy <= py1]
        return min(inside, key=lambda i: _area(panel_boxes[i])) if inside else -1

    order = {panel: rank for rank, panel in enumerate(_rows(list(range(len(panel_boxes))), lambda i: panel_boxes[i]))}
    order[-1] = len(order)
    groups: dict[int, list[Balloon]] = {}
    for balloon in balloons:
        groups.setdefault(panel_of(balloon), []).append(balloon)
    return [b for panel in sorted(groups, key=order.get) for b in _rows(groups[panel], lambda b: b.box)]


def _rows(items: list, box_of) -> list:
    """`items` in rows of overlapping heights, top to bottom, each row left
    to right."""
    rows: list[list] = []
    for item in sorted(items, key=lambda i: box_of(i)[1]):
        top, bottom = box_of(item)[1], box_of(item)[3]
        row = next((r for r in rows if top < max(box_of(i)[3] for i in r) and bottom > min(box_of(i)[1] for i in r)), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    return [item for row in rows for item in sorted(row, key=lambda i: box_of(i)[0])]


def _area(box: tuple[int, int, int, int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def _color(image: np.ndarray, inside: np.ndarray) -> tuple[int, int, int]:
    """The region's most common color: letters and antialiasing pull a
    median off it."""
    pixels = image[inside].reshape(-1, 3) // 8 * 8
    values, counts = np.unique(pixels, axis=0, return_counts=True)
    b, g, r = (values[counts.argmax()] + 4).tolist()
    return int(b), int(g), int(r)


def _solid(inside: np.ndarray) -> np.ndarray:
    """The region with its holes (the letters) filled."""
    region = inside.astype(np.uint8) * 255
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solid = np.zeros_like(region)
    cv2.drawContours(solid, contours, -1, 255, -1)
    return solid
