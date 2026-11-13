"""Stage 5: decide where and how each translated paragraph is written.

Layout draws nothing on the page: it returns `Placement`s (box, HTML, scale)
that `pdf/draw.py` executes. Fitting is measured on a scratch page, so a
placement decision can be tested without producing the output PDF.
"""

import html
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf

from .align import Run, base_style
from .analysis.model import Block, Leader
from .fonts import FontLibrary, generic_family

# Minimum distance kept between the grown text box and the next element.
GAP = 2.0
# Baselines measured on the scratch page differ from the original ones by
# rounding; a line within this distance (points) is where it was.
TOLERANCE = 0.5
# How much wider than the original a paragraph of several lines may get,
# into free space: first a little (a replacement font slightly wider than
# the original), then more before the font is reduced, since a little width
# is less visible than a smaller font.
WIDTH_SLACKS = (0.03, 0.10)
# Reduced sizes are rounded down to steps of this fraction, and paragraphs of
# one style on a page share the smallest: text of one kind in one size reads
# as ordered, mixed sizes side by side as a mess.
SCALE_STEP = 0.05
# A paragraph needing to shrink below this is reduced on its own, so one
# tight table cell does not shrink every paragraph of its style.
SHARED_SCALE_MIN = 0.85
# Lines starting within this distance (points) of each other are aligned.
INDENT_TOLERANCE = 2.0
# Built-in PDF fonts used to draw TOC leaders and page numbers.
BASE14 = {
    "sans-serif": ("helv", "hebo"),
    "serif": ("tiro", "tibo"),
    "monospace": ("cour", "cobo"),
}


@dataclass
class TranslatedParagraph:
    source: Block
    runs: list[Run]


@dataclass
class Placement:
    """Where a paragraph is written and how much it has to shrink."""

    paragraph: TranslatedParagraph
    rect: pymupdf.Rect
    html: str
    css: str
    scale: float  # 1.0 = original font size
    fitted: bool  # False: the text overflows the box even when scaled
    # Where the lines were written, as measured: drawing places leaders by
    # them, and quality checks compare them with the original lines.
    lines: list["WrittenLine"] = field(default_factory=list)


@dataclass
class WrittenLine:
    rect: pymupdf.Rect  # the line's box
    baseline: float


def layout_page(
    page_rect: pymupdf.Rect,
    paragraphs: list[TranslatedParagraph],
    blocks: list[Block],
    graphics: list[pymupdf.Rect],
    fonts: FontLibrary,
) -> list[Placement]:
    """`blocks`: every block of the page, kept ones too; `graphics`: images
    and filled areas. A paragraph must not grow into any of them."""
    obstacles = [Obstacle.of_block(b) for b in blocks] + [Obstacle(g, fill=True) for g in graphics]
    placements = [_place(page_rect, p, obstacles, fonts) for p in paragraphs]
    return _harmonize(page_rect, placements, obstacles, fonts)


def _harmonize(
    page_rect: pymupdf.Rect,
    placements: list[Placement],
    obstacles: list["Obstacle"],
    fonts: FontLibrary,
) -> list[Placement]:
    """Give the paragraphs of one style on the page one size: the smallest
    any of them needs, rounded down to a step."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, placement in enumerate(placements):
        paragraph = placement.paragraph
        # Text the translation left as it was (numbers, names) fits where it
        # was: shrinking it with its style would only make it smaller.
        if "".join(r.text for r in paragraph.runs).split() == paragraph.source.text.split():
            continue
        groups[_style_key(paragraph)].append(index)
    for indexes in groups.values():
        # Less than half a step is a rounding difference with the original
        # (its text fitted there): that paragraph alone takes it.
        shared = [i for i in indexes if SHARED_SCALE_MIN <= placements[i].scale < 1 - SCALE_STEP / 2]
        if not shared:
            continue
        size = _step_down(min(placements[i].scale for i in shared))
        for i in indexes:
            if placements[i].scale >= SHARED_SCALE_MIN:
                placements[i] = _place(page_rect, placements[i].paragraph, obstacles, fonts, size)
    return placements


def _style_key(paragraph: TranslatedParagraph) -> tuple:
    """Text that looks the same: list items and paragraphs of the body text
    share a key, so a list does not end up larger than the text around it."""
    if not paragraph.runs:
        return ()
    style = base_style(paragraph.runs)
    return (style.font, style.size, style.bold)


def _step_down(scale: float) -> float:
    return max(SCALE_STEP, math.floor(scale / SCALE_STEP + 1e-6) * SCALE_STEP)


def _place(
    page_rect: pymupdf.Rect,
    paragraph: TranslatedParagraph,
    obstacles: list["Obstacle"],
    fonts: FontLibrary,
    size: float = 1.0,
) -> Placement:
    """`size`: font size to write with, as a fraction of the original."""
    for slack in WIDTH_SLACKS:
        placement = _place_in(page_rect, paragraph, obstacles, fonts, size, slack)
        if placement.scale >= size:
            break
    return placement


def _place_in(
    page_rect: pymupdf.Rect,
    paragraph: TranslatedParagraph,
    obstacles: list["Obstacle"],
    fonts: FontLibrary,
    size: float,
    slack: float,
) -> Placement:
    source = paragraph.source
    rect = _available_rect(page_rect, source, obstacles, slack)
    if source.leader:
        # Keep room for a few dots and the page number.
        leader = source.leader
        rect.x1 = leader.right - text_width(leader.number, leader) - 4 * leader.size
    prefix = [Run(source.prefix, paragraph.runs[0].style)] if source.prefix else []
    body = "".join(_run_html(run, fonts, size) for run in [*prefix, *paragraph.runs])
    html_text = f"<p>{body}</p>"
    # Explicit selectors: the HTML engine's default `p` margins (1em) would
    # win over a `*` rule.
    css = (
        f"{fonts.css}\n"
        f"body, p {{ margin: 0; padding: 0; }} "
        f"p {{ line-height: {_line_height(source)}; text-align: {source.align}; {_indent(source)}}}"
    )

    # Write the text in a box as tall as needed, then see where its lines
    # fall. The first baseline is aligned with the original one: the offset
    # depends on the font metrics used by the HTML engine, so it is measured
    # instead of computed.
    first_baseline = source.lines[0].baseline
    tall = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + 3 * page_rect.height)
    measured = _measure(page_rect, tall, html_text, css, fonts.archive)
    if measured is None:  # nothing visible to write
        return Placement(paragraph, rect, html_text, css, size, fitted=True)
    shift = first_baseline - measured.first_baseline
    top = rect.y0 + shift
    bottom_part = measured.bottom - measured.last_baseline  # descent and leading
    last_baseline = measured.last_baseline + shift
    limit = _last_baseline_limit(source, rect, obstacles, page_rect)

    rect = pymupdf.Rect(rect.x0, top, rect.x1, max(limit, last_baseline) + bottom_part + GAP)
    if last_baseline <= limit + TOLERANCE:
        lines = [WrittenLine(line.rect + (0, shift, 0, shift), line.baseline + shift) for line in measured.lines]
        return Placement(paragraph, rect, html_text, css, size, fitted=True, lines=lines)
    # Too long for the room: the HTML engine shrinks it to the allowed height.
    rect.y1 = limit + bottom_part + TOLERANCE
    spare_height, scale, lines = _fit(page_rect, rect, html_text, css, fonts.archive)
    return Placement(paragraph, rect, html_text, css, size * scale, fitted=spare_height >= 0, lines=lines)


@dataclass
class _Measured:
    first_baseline: float
    last_baseline: float
    bottom: float  # bottom of the written content
    lines: list[WrittenLine]


def _scratch_page(page_rect: pymupdf.Rect) -> pymupdf.Page:
    return pymupdf.open().new_page(width=page_rect.width, height=page_rect.height)


def _measure(
    page_rect: pymupdf.Rect,
    rect: pymupdf.Rect,
    html_text: str,
    css: str,
    archive: pymupdf.Archive,
) -> _Measured | None:
    """Write the paragraph on a scratch page and read where its lines fell."""
    page = _scratch_page(page_rect)
    spare_height, _ = page.insert_htmlbox(rect, html_text, css=css, archive=archive)
    lines = _written_lines(page)
    if not lines:
        return None
    baselines = [line.baseline for line in lines]
    return _Measured(baselines[0], max(baselines), rect.y1 - spare_height, lines)


def _fit(
    page_rect: pymupdf.Rect,
    rect: pymupdf.Rect,
    html_text: str,
    css: str,
    archive: pymupdf.Archive,
) -> tuple[float, float, list[WrittenLine]]:
    """(spare height, scale, lines) of the paragraph written into `rect`."""
    page = _scratch_page(page_rect)
    spare_height, scale = page.insert_htmlbox(rect, html_text, css=css, archive=archive)
    return spare_height, scale, _written_lines(page)


def _written_lines(page: pymupdf.Page) -> list[WrittenLine]:
    """The lines on a scratch page, in reading order. Each line's baseline is
    its largest span's: superscripts sit above it."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if spans:
                rect = pymupdf.Rect(spans[0]["bbox"])
                for span in spans[1:]:
                    rect |= span["bbox"]
                lines.append(WrittenLine(rect, max(spans, key=lambda s: s["size"])["origin"][1]))
    return lines


# Where letters reach, in font sizes, from the baseline: capitals and
# ascenders above it, descenders below. Line boxes are larger (they hold
# room for accents and leading), and neighboring boxes overlap in normal
# text, so they can't be used as limits.
ASCENT = 0.8
DESCENT = 0.25


def _last_baseline_limit(
    paragraph: Block,
    rect: pymupdf.Rect,
    obstacles: list["Obstacle"],
    page_rect: pymupdf.Rect,
) -> float:
    """The lowest baseline the paragraph's last line may sit on.

    Below the text, the next element starts where its letters start (text)
    or at its edge (images, fills). The original last baseline is always
    allowed: the original text fitted there.
    """
    size = max(s.size for s in paragraph.lines[-1].spans)
    own = paragraph.bbox
    descent = DESCENT * size
    limit = _content_area(page_rect, obstacles, own).y1 - descent
    for container in _containers(paragraph, obstacles):
        limit = min(limit, container.y1 - descent)
    for o in obstacles:
        if o.rect.contains(own) or o.rect == own:
            continue
        is_below = o.rect.y0 > own.y0 + own.height / 2
        if not (is_below and o.rect.x0 < rect.x1 and o.rect.x1 > rect.x0):
            continue
        top = o.baseline - ASCENT * o.size if o.baseline is not None else o.rect.y0
        limit = min(limit, top - descent - GAP / 2)
    return max(limit, paragraph.lines[-1].baseline)


@dataclass
class Obstacle:
    """Something on the page a translation must not grow into."""

    rect: pymupdf.Rect
    # For horizontal text: its first baseline and font size, so the room
    # above it is measured from its letters instead of its box.
    baseline: float | None = None
    size: float = 0.0
    fill: bool = False  # an image or filled area: text may sit inside it

    @classmethod
    def of_block(cls, block: Block) -> "Obstacle":
        first = block.lines[0]
        if not first.horizontal:
            return cls(block.bbox)
        return cls(block.bbox, first.baseline, max(s.size for s in first.spans))


def _containers(paragraph: Block, obstacles: list[Obstacle]) -> list[pymupdf.Rect]:
    """Areas the paragraph's text must stay inside: its cell (the box of
    rules around it), and filled areas it sits on, a table header band, a
    colored box: white text on a dark band that runs onto the white page
    disappears (defect 10).

    Line boxes may stick out of a tight band (they include room for accents),
    so the middle of the lines is what must be inside.
    """
    rect = paragraph.bbox
    ink = pymupdf.Rect(rect.x0 + 1, rect.y0 + rect.height / 4, rect.x1 - 1, rect.y1 - rect.height / 4)
    containers = [o.rect for o in obstacles if o.fill and o.rect.contains(ink)]
    if paragraph.cell is not None:
        containers.append(paragraph.cell)
    return containers


def _content_area(
    page_rect: pymupdf.Rect, obstacles: list[Obstacle], rect: pymupdf.Rect
) -> pymupdf.Rect:
    """The part of the page in use: margins mirrored from the top-left
    corner, widened to whatever the page actually uses (margins are often
    asymmetric)."""
    left = min((o.rect.x0 for o in obstacles), default=rect.x0)
    top = min((o.rect.y0 for o in obstacles), default=rect.y0)
    right = max([page_rect.x1 - left, *(o.rect.x1 for o in obstacles)])
    bottom = max([page_rect.y1 - top, *(o.rect.y1 for o in obstacles)])
    return pymupdf.Rect(left, top, right, bottom)


def _available_rect(
    page_rect: pymupdf.Rect, paragraph: Block, obstacles: list[Obstacle], slack: float
) -> pymupdf.Rect:
    """Grow the paragraph box sideways into the empty space around it.

    Single-line paragraphs (titles, list items) grow as far as there is
    room, since their width says nothing about the column width: away from
    the side they are aligned to. Longer paragraphs fill their column, so
    they only get a little slack: a replacement font slightly wider than the
    original would otherwise wrap their text onto one more line. How far
    down the text may go is decided by `_last_baseline_limit`.
    """
    rect = pymupdf.Rect(paragraph.bbox)
    others = [o.rect for o in obstacles if not o.rect.contains(rect) and o.rect != paragraph.bbox]
    content = _content_area(page_rect, obstacles, rect)
    left_margin, content_right = content.x0, content.x1
    for container in _containers(paragraph, obstacles):
        left_margin = max(left_margin, container.x0 + GAP / 2)
        content_right = min(content_right, container.x1 - GAP / 2)

    same_row = [o for o in others if o.y0 < rect.y1 and o.y1 > rect.y0]
    right = content_right
    left = left_margin
    for o in same_row:
        if o.x0 >= rect.x1 - 1:
            right = min(right, o.x0 - GAP)
        if o.x1 <= rect.x0 + 1:
            left = max(left, o.x1 + GAP)
    room_right = max(0.0, right - rect.x1)
    room_left = max(0.0, rect.x0 - left)
    if len(paragraph.lines) > 1:
        room_right, room_left = min(room_right, slack * rect.width), min(room_left, slack * rect.width)
    if paragraph.align == "right":
        rect.x0 -= room_left
    elif paragraph.align == "center":
        rect.x0 -= min(room_left, room_right)
        rect.x1 += min(room_left, room_right)
    else:
        rect.x1 += room_right
    return rect


def _run_html(run: Run, fonts: FontLibrary, size: float = 1.0) -> str:
    style = run.style
    css = [
        f"font-family: {fonts.family(style.font)}",
        f"font-size: {style.size * size:.2f}px",
        f"color: #{style.color:06x}",
        f"font-weight: {'bold' if style.bold else 'normal'}",
        f"font-style: {'italic' if style.italic else 'normal'}",
    ]
    if style.underline:
        css.append("text-decoration: underline")
    if style.superscript:
        css.append("vertical-align: super")
    content = (
        _small_caps_html(run.text, style.size * size * style.small_caps)
        if style.small_caps
        else html.escape(run.text)
    )
    return f'<span style="{"; ".join(css)}">{content}</span>'


def _small_caps_html(text: str, small_size: float) -> str:
    """Emulate small caps: words start with a full-size capital, the rest of
    the letters are capitals at the smaller size. Numbers stay full size."""
    small = f'<span style="font-size: {small_size:.1f}px">{{}}</span>'
    parts = []
    for token in re.split(r"(\s+)", text):
        if not token.strip() or not token[0].isalpha():
            parts.append(html.escape(token))
        elif len(token) > 3 or not parts:
            parts.append(html.escape(token[0].upper()) + small.format(html.escape(token[1:].upper())))
        else:
            parts.append(small.format(html.escape(token.upper())))
    return "".join(parts)


def _line_height(paragraph: Block) -> str:
    """Reproduce the original baseline-to-baseline distance."""
    lines = paragraph.lines
    # CSS applies the factor to each span's own size, and the largest span
    # sets the line's height: the largest of the paragraph is the reference,
    # or a line holding it would be spaced wider than the original.
    size = max(s.size for s in paragraph.spans)
    if len(lines) < 2 or size <= 0:
        return "1.2"
    spacing = (lines[-1].baseline - lines[0].baseline) / (len(lines) - 1)
    return f"{spacing / size:.3f}"


def _indent(paragraph: Block) -> str:
    """Reproduce where the first line starts relative to the others.

    Indented: after a run-in heading ("Encoder:  The encoder is..."), which
    is a block of its own. Hanging: list items, whose text lines up after
    the marker.
    """
    lines = paragraph.lines
    if len(lines) < 2 or paragraph.align in ("center", "right"):
        return ""
    indent = lines[0].bbox.x0 - min(line.bbox.x0 for line in lines[1:])
    if indent > INDENT_TOLERANCE:
        return f"text-indent: {indent:.1f}px; "
    if indent < -INDENT_TOLERANCE:
        return f"padding-left: {-indent:.1f}px; text-indent: {indent:.1f}px; "
    return ""


def base14(leader: Leader) -> str:
    regular, bold = BASE14[generic_family(leader.font)]
    return bold if leader.bold else regular


def text_width(text: str, leader: Leader) -> float:
    return pymupdf.get_text_length(text, fontname=base14(leader), fontsize=leader.size)
