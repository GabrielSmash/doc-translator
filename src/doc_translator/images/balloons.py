"""Lines of text -> balloons and captions: where each text sits, its shape,
its color, and the room to write a translation in.

A balloon's inside is a region of one flat color closed by a dark outline.
Flooding the light pixels around a line from just above and below it (not
inside a letter's hole) gives that region; lines that reach the same
region are one balloon. A region much larger than its text is open (its
outline has a gap, or there is none): only the lines' boxes count then.
Text over the art (titles, sound effects) reaches no flat region and is
left as it is. The letters are measured too: their color and the height of
their capitals, which the translation is lettered with.
"""

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import ocr
from .ocr import TextLine

# Pixels darker than this are letters and outlines: walls of the flood.
DARK = 110
# Pixels lighter than this can be a balloon's inside.
LIGHT = 170
# A region larger than this share of the page is the page, not a balloon:
# only text with the region all around it is read there.
MAX_PAGE_SHARE = 0.15
FLAT_AROUND = 0.9
# Text there is comic lettering when this share of its letters are capitals
# and it has this many letters.
LETTERED_SHARE = 0.8
LETTERED_MIN = 3
# Pixels of a dark balloon differ from its color by this much at most, per
# channel (JPEG noise); its letters and outline, by much more.
COLOR_TOLERANCE = 40
# Pixels darker than this are ink: outlines and letters, not a balloon's color.
INK = 60
# A region at least this many times its line's box holds that line.
MIN_REGION_SHARE = 1.5
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
# Letters differ from their balloon's color by this much at least (a
# distance in BGR); JPEG noise and antialiasing stay under it.
INK_DISTANCE = 40
# A capital is at least this share of its OCR line's box (punctuation,
# commas and bits of noise are less).
MIN_CAP_SHARE = 0.4


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
    ink: tuple[int, int, int] = (0, 0, 0)  # the letters' color, BGR
    cap_height: float = 0.0  # the letters' capitals, in pixels; 0 if not measured

    @property
    def text_box(self) -> tuple[int, int, int, int]:
        boxes = [line.box for line in self.lines]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    @property
    def line_height(self) -> float:
        return float(np.median([line.letter_height for line in self.lines]))

    @property
    def angle(self) -> float:
        """Its lines' slant, in degrees (0 for most balloons)."""
        return float(np.median([line.angle for line in self.lines]))

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

    def colored_region_at(x: int, y: int) -> int:
        """The region of `image`'s color at (x, y): pixels near it, reached
        without crossing others (letters lighter than their balloon, an
        outline)."""
        if region_of[y, x]:
            return int(region_of[y, x])
        mask = np.zeros((height + 2, width + 2), np.uint8)
        tolerance = (COLOR_TOLERANCE,) * 3
        flags = 4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
        cv2.floodFill(image, mask, (x, y), 0, tolerance, tolerance, flags)
        filled = mask[1:-1, 1:-1] > 0
        number = len(regions) + 1
        region_of[filled & (region_of == 0)] = number
        ys, xs = np.nonzero(filled)
        regions[number] = {
            "area": int(filled.sum()),
            "box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
            "colored": True,
        }
        return number

    for line in lines:
        x0, y0, x1, y1 = line.box
        step = max(1, (x1 - x0) // SEEDS)
        votes: dict[int, int] = {}
        seeds = [
            (x, y)
            for x in range(max(0, x0), min(width, x1), step)
            for y in (y0 - SEED_OFFSET, y1 + SEED_OFFSET)
            if 0 <= y < height
        ]
        # Light seeds are a light balloon's inside, unless on both sides of
        # the line most seeds are of a middle tone: a dark balloon, its light
        # seeds on letters of the lines next to it (the cover's red question
        # mark). One side can be out of a light balloon hugging its text
        # (the panel's yellow above "MY WEB!", page 9). Black seeds are
        # outlines and letters, in either.
        light = [(x, y) for x, y in seeds if gray[y, x] > LIGHT]
        sides = [[gray[y, x] for x, y in seeds if y == side] for side in (y0 - SEED_OFFSET, y1 + SEED_OFFSET)]
        dark_balloon = all(sum(INK < g <= LIGHT for g in side) > sum(g > LIGHT for g in side) for side in sides if side)
        for x, y in [] if dark_balloon else light:
            number = region_at(x, y)
            if regions[number]["area"] >= MIN_REGION_SHARE * (x1 - x0) * (y1 - y0):
                votes[number] = votes.get(number, 0) + 1
        if not votes and _lettered(line.text):
            # No light inside around the line: a balloon of a dark color, its
            # letters lighter (white on the cover's red question mark). Its
            # region is the one of its color, not of light pixels. Dark art
            # holds page numbers and marks too: only lettering is read there.
            for x, y in seeds:
                if gray[y, x] <= LIGHT:
                    number = colored_region_at(x, y)
                    if regions[number]["area"] >= MIN_REGION_SHARE * (x1 - x0) * (y1 - y0):
                        votes[number] = votes.get(number, 0) + 1
        if votes:
            # A tight balloon's seeds also reach the panel around it (a
            # thought balloon under a caption, page 6): the line is in the
            # smallest region holding it.
            holding = [n for n in votes if _holds(regions[n]["box"], line.box)]
            owner = min(holding, key=lambda n: regions[n]["area"]) if holding else max(votes, key=votes.get)
            owners.setdefault(owner, []).append(line)

    _merge_colored(owners, regions, region_of, image)
    balloons = []
    for number, own in owners.items():
        region = regions[number]
        whole = region_of == number
        if region["area"] > MAX_PAGE_SHARE * width * height:
            # The page's background, or a panel's: text written on it with
            # nothing around ("FANTASY! THRILLS!", a caption in the margin)
            # is open text, the rest is over the art.
            own = [line for line in own if _flat_around(line, whole, lines, walls) and _lettered(line.text)]
            if not own:
                continue
        # One region can hold several texts: balloons touching each other,
        # a thought balloon whose outline has gaps under a caption (page 6),
        # a cover's sky under its blurbs. Each text gets the part of the
        # region on its side.
        region_walls = _walls_in(image, whole, walls) if region.get("colored") else walls
        groups = [part for group in _groups(own, region_walls) for part in _columns(image, group, region_walls)]
        for group in groups:
            inside = whole.copy()
            for other in groups:
                if other is not group:
                    _cut(inside, _box(group), _box(other))
            balloons.append(_balloon(image, group, inside))
    return reading_order(balloons, panels(gray))


def _merge_colored(owners: dict[int, list[TextLine]], regions: dict[int, dict], region_of: np.ndarray, image: np.ndarray) -> None:
    """Join the regions of a dark balloon its light letters cut apart.

    Lines lettered tight in a dark balloon (the cover's red question mark)
    leave thin strips of its color between them, each a region of its own.
    Regions of one color whose lines are one text (near each other, one
    under the other) are one balloon."""
    numbers = [n for n in owners if regions[n].get("colored")]
    color = {n: np.array(_color(image, region_of == n), int) for n in numbers}
    no_walls = np.zeros(region_of.shape, np.uint8)
    for a, b in itertools.combinations(numbers, 2):
        if a not in owners or b not in owners:
            continue
        if np.abs(color[a] - color[b]).max() > COLOR_TOLERANCE:
            continue
        if any(_linked(x, y, no_walls) for x in owners[a] for y in owners[b]):
            owners[a] += owners.pop(b)
            region_of[region_of == b] = a
            regions[a]["area"] += regions[b]["area"]


def _walls_in(image: np.ndarray, region: np.ndarray, walls: np.ndarray) -> np.ndarray:
    """The outlines of a dark balloon: pixels darker than its color (its own
    color is darker than DARK, its letters lighter)."""
    color = np.array(_color(image, region), np.int16)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.int16)
    own = int(cv2.cvtColor(color.astype(np.uint8).reshape(1, 1, 3), cv2.COLOR_BGR2GRAY)[0, 0])
    return np.where(gray < own - COLOR_TOLERANCE, 255, 0).astype(np.uint8) & walls


def _lettered(text: str) -> bool:
    """Comic lettering: in capitals. Print on a page (a web address, a
    copyright, readers' letters) is not."""
    letters_ = [c for c in text if c.isalpha()]
    return len(letters_) >= LETTERED_MIN and sum(c.isupper() for c in letters_) >= LETTERED_SHARE * len(letters_)


def _flat_around(line: TextLine, region: np.ndarray, lines: list[TextLine], walls: np.ndarray) -> bool:
    """Whether `region` holds nearly all of a ring half a line height wide
    around the line, text lines and frames left out: the line is on it
    with no art nearby. A caption on the page's background can sit right
    on a panel's frame ("AND SO...", pages 3 and 7): straight lines
    FRAME_LENGTH line heights long are frames, not art."""
    x0, y0, x1, y1 = line.box
    reach = int(line.letter_height / 2)
    ring = np.zeros_like(region)
    ring[max(0, y0 - reach) : y1 + reach, max(0, x0 - reach) : x1 + reach] = True
    for other in lines:
        ox0, oy0, ox1, oy1 = other.box
        ring[max(0, oy0) : oy1, max(0, ox0) : ox1] = False
    # Frames are looked for a frame's length around the ring.
    length = max(3, int(FRAME_LENGTH * line.letter_height))
    height, width = region.shape
    fx0, fy0 = max(0, x0 - reach - length), max(0, y0 - reach - length)
    fx1, fy1 = min(width, x1 + reach + length), min(height, y1 + reach + length)
    crop = walls[fy0:fy1, fx0:fx1]
    # Thin: a block of art as long is not a frame.
    thick = max(2, int(FRAME_THICKNESS * line.letter_height))
    straight = np.zeros_like(crop)
    for along, across in (((1, length), (thick, 1)), ((length, 1), (1, thick))):
        lines_ = cv2.morphologyEx(crop, cv2.MORPH_OPEN, np.ones(along, np.uint8))
        straight |= lines_ & ~cv2.morphologyEx(lines_, cv2.MORPH_OPEN, np.ones(across, np.uint8))
    # With the antialiased pixels along them.
    ring[fy0:fy1, fx0:fx1] &= cv2.dilate(straight, np.ones((FRAME_EDGE, FRAME_EDGE), np.uint8)) == 0
    return bool(ring.any()) and np.count_nonzero(region & ring) >= FLAT_AROUND * np.count_nonzero(ring)


# A dark straight line this many line heights long is a frame (a panel's,
# a caption's); its edges are this many pixels wide.
FRAME_LENGTH = 3
FRAME_EDGE = 5
# A frame is thinner than this share of a line height.
FRAME_THICKNESS = 0.3


def _holds(box: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return box[0] <= inner[0] and box[1] <= inner[1] and box[2] >= inner[2] and box[3] >= inner[3]


def _box(lines: list[TextLine]) -> tuple[int, int, int, int]:
    return (min(l.box[0] for l in lines), min(l.box[1] for l in lines), max(l.box[2] for l in lines), max(l.box[3] for l in lines))


# Lines this far apart, in line heights, are not one text.
LINE_GAP = 1.0
# Lines with an outline between them across this share of their overlap
# are in different balloons.
WALLED = 0.6
# Lines one under the other share this share of the narrower one's width.
MIN_SHARED = 0.25
# Lines whose slants differ by more than this, in degrees, are not one
# text: on the comic, a balloon's lines differ by 4 at most.
NOT_PARALLEL = 8.0


def _groups(lines: list[TextLine], walls: np.ndarray, apart: tuple[TextLine, ...] = ()) -> list[list[TextLine]]:
    """`lines` in texts: a line is in the text of a line just above it
    (they overlap across, nothing but the balloon's inside between them)
    or beside it on the same row. Lines in `apart` are not linked to each
    other."""
    lines = sorted(lines, key=lambda l: (l.box[1], l.box[0]))
    parent = list(range(len(lines)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (i, a), (j, b) in itertools.combinations(enumerate(lines), 2):
        if not (a in apart and b in apart) and _linked(a, b, walls):
            parent[root(j)] = root(i)
    texts: dict[int, list[TextLine]] = {}
    for i, line in enumerate(lines):
        texts.setdefault(root(i), []).append(line)
    return list(texts.values())


def _columns(image: np.ndarray, lines: list[TextLine], walls: np.ndarray) -> list[list[TextLine]]:
    """`lines`, one text, in the texts it is made of when one of its lines
    runs across two others side by side: the OCR reads a row of two
    balloons touching each other as one line where their outlines meet
    ("...ATOMIC POWER" and "MY STRENGTH!...", page 12), and that line joins
    them. It is cut halfway between them, and each half read again."""
    for line in lines:
        rest = [other for other in lines if other is not line]
        x0, y0, x1, y1 = line.box
        reach = LINE_GAP * (y1 - y0)
        for a, b in itertools.combinations(_groups(rest, walls), 2):
            left, right = sorted((_box(a), _box(b)))
            if (
                min(len(a), len(b)) < MIN_COLUMN
                or not x0 < left[2] < right[0] < x1
                # Next to both, above, below or beside.
                or any(max(box[1] - y1, y0 - box[3]) > reach for box in (left, right))
            ):
                continue
            if halves := _halves(image, line, (left[2] + right[0]) // 2):
                texts = _groups(rest + halves, walls, apart=tuple(halves))
                return [part for text in texts for part in _columns(image, text, walls)]
    return [lines]


# Texts side by side a line is cut between have this many lines each.
MIN_COLUMN = 2


def _halves(image: np.ndarray, line: TextLine, cut: int) -> list[TextLine] | None:
    """`line` cut at `cut` (x), each side read again; None when a side reads
    nothing."""
    x0, y0, x1, y1 = line.box
    room = int((y1 - y0) / 4)
    halves = []
    for left, right in ((x0, cut), (cut, x1)):
        read = ocr.read_line(image[max(0, y0 - room) : y1 + room, max(0, left) : right])
        if read is None:
            return None
        halves.append(TextLine((left, y0, right, y1), read[0], read[1], line.angle, line.height))
    return halves


def _linked(a: TextLine, b: TextLine, walls: np.ndarray) -> bool:
    # A text's lines are parallel; a sound effect lettered across the art
    # next to a balloon is not (page 13's "BR-I-I-NG", 18 degrees).
    if abs(a.angle - b.angle) > NOT_PARALLEL:
        return False
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = a.box, b.box
    height = max(ay1 - ay0, by1 - by0)
    if min(ay1, by1) - max(ay0, by0) > height / 2:  # the same row
        gap = max(ax0, bx0) - min(ax1, bx1)
        if gap > LINE_GAP * height / 2:
            return False
        if gap <= 0:
            return True
        # Two balloons side by side, their lines close ("OH, NO! NO!" and
        # "WHAT YOU", page 6): the outline between them.
        crossed = walls[max(ay0, by0) : min(ay1, by1), min(ax1, bx1) : max(ax0, bx0)].any(axis=1)
        return crossed.mean() < WALLED
    left, right = max(ax0, bx0), min(ax1, bx1)
    top, bottom = (ay1, by0) if ay0 < by0 else (by1, ay0)
    # One under the other, lines of a text share much of their width; lines
    # of balloons side by side, a corner at most ("OH, NO! NO!" over "SEE?
    # BUT", page 6).
    if right - left < MIN_SHARED * min(ax1 - ax0, bx1 - bx0) or bottom - top > LINE_GAP * height:
        return False
    if bottom <= top:
        return True
    crossed = walls[top:bottom, left:right].any(axis=0)
    return crossed.mean() < WALLED


def _cut(inside: np.ndarray, own: tuple[int, int, int, int], other: tuple[int, int, int, int]) -> None:
    """Keep the side of `inside` toward `own`, halfway to `other`."""
    if own[2] <= other[0]:
        inside[:, (own[2] + other[0]) // 2 :] = False
    elif other[2] <= own[0]:
        inside[:, : (other[2] + own[0]) // 2] = False
    elif own[3] <= other[1]:
        inside[(own[3] + other[1]) // 2 :, :] = False
    elif other[3] <= own[1]:
        inside[: (other[3] + own[1]) // 2, :] = False


def _balloon(image: np.ndarray, lines: list[TextLine], inside: np.ndarray) -> Balloon:
    ys, xs = np.nonzero(inside)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    balloon = Balloon(_reading(lines), (x0, y0, x1, y1), _color(image, inside))
    tx0, ty0, tx1, ty1 = balloon.text_box
    # Leaked through a gap into the panel: a shape filling little of its
    # box (a balloon fills 70% of it, a caption all of it).
    leaked = len(xs) / max(1, (x1 - x0) * (y1 - y0)) < MIN_FILL
    if leaked or x1 - x0 > OPEN_WIDTH * (tx1 - tx0) + OPEN_MARGIN_X or y1 - y0 > OPEN_HEIGHT * (ty1 - ty0) + OPEN_MARGIN_Y:
        balloon.open = True
        balloon.box = balloon.text_box
        # The region runs into the panel: its most common color can be the
        # panel's ("yellow" for a white balloon on a yellow panel). The color
        # is the one around the text, in the region or not: text in a small
        # box the region surrounds is on the box's color ("JULY" on the
        # cover, in white over the sky).
        near = np.zeros_like(inside)
        near[max(0, ty0 - 8) : ty1 + 8, max(0, tx0 - 8) : tx1 + 8] = True
        balloon.color = _color(image, near)
        # Near its text the balloon still has its outline: the room is the
        # region there, not the text's box (whose corners cross it).
        reach = int(balloon.line_height)
        around = np.zeros_like(inside)
        ax0, ay0, ax1, ay1 = max(0, tx0 - reach), max(0, ty0 - reach), tx1 + reach, ty1 + reach
        # Of the balloon's color: a light panel around it is in its region
        # too, through the gaps of its outline (a thought balloon's, page 11).
        crop = image[ay0:ay1, ax0:ax1].astype(np.int16)
        around[ay0:ay1, ax0:ax1] = np.abs(crop - np.array(balloon.color, np.int16)).max(axis=2) <= COLOR_TOLERANCE
        balloon.room_mask = _solid(inside & around)
    else:
        balloon.mask = _solid(inside)
        # With its letters: a caption fills its box whatever its text.
        balloon.round = np.count_nonzero(balloon.mask) < RECTANGLE_FILL * (x1 - x0 + 1) * (y1 - y0 + 1)
    balloon.ink, balloon.cap_height = _lettering(image, balloon.lines, balloon.color)
    if balloon.angle:
        # Measured upright: slanted, its letters are taller in its lines' boxes.
        balloon.cap_height = 0.0
    return balloon


def letters(crop: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    """The letters of `crop` (1, the rest 0): the pixels far from the
    balloon's color, split from it by Otsu's threshold."""
    distance = np.linalg.norm(crop.astype(np.float32) - np.array(background, np.float32), axis=2)
    scaled = np.clip(distance, 0, 255).astype(np.uint8)
    threshold, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (scaled > max(threshold, INK_DISTANCE)).astype(np.uint8)


def _lettering(
    image: np.ndarray, lines: list[TextLine], background: tuple[int, int, int]
) -> tuple[tuple[int, int, int], float]:
    """The letters' color (the middle of their strokes: their edges blend
    into the balloon) and the height of their capitals: the median height
    of the shapes in each line's box that are letter-high and do not touch
    its top or bottom (an outline crossing the box does)."""
    cores, heights = [], []
    for line in lines:
        x0, y0, x1, y1 = (max(0, v) for v in line.box)
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        ink = letters(crop, background)
        cores.append(crop[cv2.erode(ink, np.ones((2, 2), np.uint8)) > 0])
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        box_height = y1 - y0
        for top, height in stats[1:count, [cv2.CC_STAT_TOP, cv2.CC_STAT_HEIGHT]]:
            if height >= MIN_CAP_SHARE * box_height and top > 0 and top + height < box_height:
                heights.append(height)
    pixels = np.concatenate(cores) if cores else np.empty((0, 3))
    ink = tuple(int(v) for v in np.median(pixels, axis=0)) if len(pixels) else (0, 0, 0)
    return ink, float(np.median(heights)) if heights else 0.0


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
    # Rows by the regions' heights; in a row, left to right by the texts: a
    # region holding two balloons side by side starts left of both (page 6).
    return [
        b
        for panel in sorted(groups, key=order.get)
        for b in _rows(groups[panel], lambda b: (b.text_box[0], b.box[1], b.text_box[2], b.box[3]))
    ]


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


# Lines sharing this share of their height are one row; lines one under
# the other share up to half of it ("IN THIS" over "EPIC TALE.", page 2).
SAME_ROW = 0.75


def _reading(lines: list[TextLine]) -> list[TextLine]:
    """A text's lines top to bottom; lines of one row ("AND NOW" "BEGIN!",
    a little apart in height) left to right. Consecutive lines overlap a
    few pixels: a row is lines sharing most of their height."""
    rows: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda l: l.box[1]):
        last = rows[-1][-1].box if rows else None
        height = min(line.box[3] - line.box[1], last[3] - last[1]) if last else 0
        if last and min(line.box[3], last[3]) - max(line.box[1], last[1]) > SAME_ROW * height:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda l: l.box[0])]


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
