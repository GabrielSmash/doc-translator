"""Stage 5: decide where and how each translated paragraph is written.

Layout draws nothing on the page: it returns `Placement`s (box, HTML, scale)
that `pdf/draw.py` executes. Fitting is measured on a scratch page, so a
placement decision can be tested without producing the output PDF.
"""

import html
import re
from dataclasses import dataclass

import pymupdf

from .align import Run
from .analysis.model import Block, Leader
from .fonts import FontLibrary, generic_family

# Minimum distance kept between the grown text box and the next element.
GAP = 2.0
# Baselines measured on the scratch page differ from the original ones by
# rounding; a line within this distance (points) is where it was.
TOLERANCE = 0.5
# How much wider than the original a paragraph of several lines may get.
WIDTH_SLACK = 0.03
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
    return [_place(page_rect, p, obstacles, fonts) for p in paragraphs]


def _place(
    page_rect: pymupdf.Rect,
    paragraph: TranslatedParagraph,
    obstacles: list["Obstacle"],
    fonts: FontLibrary,
) -> Placement:
    source = paragraph.source
    rect = _available_rect(page_rect, source, obstacles)
    if source.leader:
        # Keep room for a few dots and the page number.
        leader = source.leader
        rect.x1 = leader.right - text_width(leader.number, leader) - 4 * leader.size
    prefix = [Run(source.prefix, paragraph.runs[0].style)] if source.prefix else []
    body = "".join(_run_html(run, fonts) for run in [*prefix, *paragraph.runs])
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
        return Placement(paragraph, rect, html_text, css, 1.0, fitted=True)
    shift = first_baseline - measured.first_baseline
    top = rect.y0 + shift
    bottom_part = measured.bottom - measured.last_baseline  # descent and leading
    last_baseline = measured.last_baseline + shift
    limit = _last_baseline_limit(source, rect, obstacles, page_rect)

    rect = pymupdf.Rect(rect.x0, top, rect.x1, max(limit, last_baseline) + bottom_part + GAP)
    if last_baseline <= limit + TOLERANCE:
        return Placement(paragraph, rect, html_text, css, 1.0, fitted=True)
    # Too long for the room: the HTML engine shrinks it to the allowed height.
    rect.y1 = limit + bottom_part + TOLERANCE
    spare_height, scale = _fit(page_rect, rect, html_text, css, fonts.archive)
    return Placement(paragraph, rect, html_text, css, scale, fitted=spare_height >= 0)


@dataclass
class _Measured:
    first_baseline: float
    last_baseline: float
    bottom: float  # bottom of the written content


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
    # Each line's baseline is its largest span's: superscripts sit above it.
    baselines = [
        max(spans, key=lambda s: s["size"])["origin"][1]
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        if (spans := [s for s in line["spans"] if s["text"].strip()])
    ]
    if not baselines:
        return None
    return _Measured(baselines[0], max(baselines), rect.y1 - spare_height)


def _fit(
    page_rect: pymupdf.Rect,
    rect: pymupdf.Rect,
    html_text: str,
    css: str,
    archive: pymupdf.Archive,
) -> tuple[float, float]:
    """(spare height, scale) of the paragraph written into `rect`."""
    return _scratch_page(page_rect).insert_htmlbox(rect, html_text, css=css, archive=archive)


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
    """Filled areas the paragraph's text sits on: a table header band, a
    colored box. The translation must stay inside them, or white text on a
    dark band lands on the white page and disappears (defect 10).

    Line boxes may stick out of a tight band (they include room for accents),
    so the middle of the lines is what must be inside.
    """
    rect = paragraph.bbox
    ink = pymupdf.Rect(rect.x0 + 1, rect.y0 + rect.height / 4, rect.x1 - 1, rect.y1 - rect.height / 4)
    return [o.rect for o in obstacles if o.fill and o.rect.contains(ink)]


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
    page_rect: pymupdf.Rect, paragraph: Block, obstacles: list[Obstacle]
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
        slack = WIDTH_SLACK * rect.width
        room_right, room_left = min(room_right, slack), min(room_left, slack)
    if paragraph.align == "right":
        rect.x0 -= room_left
    elif paragraph.align == "center":
        rect.x0 -= min(room_left, room_right)
        rect.x1 += min(room_left, room_right)
    else:
        rect.x1 += room_right
    return rect


def _run_html(run: Run, fonts: FontLibrary) -> str:
    style = run.style
    css = [
        f"font-family: {fonts.family(style.font)}",
        f"font-size: {style.size}px",
        f"color: #{style.color:06x}",
        f"font-weight: {'bold' if style.bold else 'normal'}",
        f"font-style: {'italic' if style.italic else 'normal'}",
    ]
    if style.underline:
        css.append("text-decoration: underline")
    if style.superscript:
        css.append("vertical-align: super")
    content = (
        _small_caps_html(run.text, style.size * style.small_caps)
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
