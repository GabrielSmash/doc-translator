"""What a page holds, and what will be done with each part of it.

`PageContent` is a faithful copy of the page (stage 1, `pdf/read.py`).
Analysis turns it into `Block`s: every line of text ends up in exactly one
block, and each block says what it is (`kind`) and what will be done with
it (`action`), so nothing is dropped without a trace.
"""

from dataclasses import dataclass, field
from enum import StrEnum

import pymupdf

# PyMuPDF span flag bits (see pymupdf.TEXT_FONT_*).
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_MONOSPACED = 8
FLAG_BOLD = 16


@dataclass
class Span:
    """A run of text sharing the same font, size, color and link."""

    text: str
    font: str
    size: float
    flags: int
    color: int
    bbox: pymupdf.Rect
    baseline: float
    link: str | None = None
    # PDFs draw underlines as separate vector lines, detected at analysis.
    underline: bool = False
    # Indexes (in page.get_drawings()) of drawings made only of this text's
    # underlines: the renderer removes those and redraws the underline.
    underline_drawings: frozenset[int] = frozenset()
    # Small caps are drawn as capitals in two sizes ("P" + "ROCEDURES"). Both
    # spans get the larger size; this keeps the small/large ratio (0 = none).
    small_caps: float = 0.0

    @property
    def bold(self) -> bool:
        return bool(self.flags & FLAG_BOLD)

    @property
    def italic(self) -> bool:
        return bool(self.flags & FLAG_ITALIC)

    @property
    def superscript(self) -> bool:
        return bool(self.flags & FLAG_SUPERSCRIPT)


@dataclass
class Line:
    spans: list[Span]
    bbox: pymupdf.Rect
    # Writing direction (cos, -sin of the angle): (1, 0) is horizontal.
    direction: tuple[float, float] = (1.0, 0.0)
    # (block, line) of the PyMuPDF line it comes from: lets tests check that
    # every line of the page ended up in a block.
    origin: tuple[int, int] = (-1, -1)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def baseline(self) -> float:
        # The largest span sets the baseline; superscripts sit above it.
        return max(self.spans, key=lambda s: s.size).baseline

    @property
    def horizontal(self) -> bool:
        return abs(self.direction[1]) < 0.01 and self.direction[0] > 0


@dataclass
class TextBlock:
    """A PyMuPDF text block: lines PyMuPDF found close together."""

    bbox: pymupdf.Rect
    lines: list[Line]


@dataclass
class PageContent:
    """Stage 1: the page as read, before any interpretation."""

    blocks: list[TextBlock]
    drawings: list[dict]  # page.get_drawings(); spans refer to them by index
    links: list[tuple[pymupdf.Rect, str]]  # URI links


@dataclass
class Leader:
    """Dot leader and page number ending a table-of-contents entry."""

    number: str
    font: str
    size: float
    color: int
    bold: bool
    right: float  # x where the page number ends


class Kind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list item"
    TABLE_CELL = "table cell"
    TOC_ENTRY = "toc entry"
    CAPTION = "caption"
    CODE = "code"  # monospaced: commands, terminal output
    MARKER = "marker"  # a bullet or number standing apart from its text
    ROTATED = "rotated"  # text not written left to right


class Action(StrEnum):
    TRANSLATE = "translate"
    KEEP = "keep"  # left untouched on the page
    SKIP = "skip"  # reported, not handled


@dataclass
class Block:
    """A unit of the page: translated as a whole, or kept as it is.

    `prefix` holds list markers ("•", "1.") that must not be translated.
    `align` is a CSS text-align value: left, right, center or justify.
    """

    lines: list[Line] = field(default_factory=list)
    prefix: str = ""
    align: str = "left"
    leader: Leader | None = None
    kind: Kind = Kind.PARAGRAPH
    action: Action = Action.TRANSLATE

    @property
    def bbox(self) -> pymupdf.Rect:
        rect = pymupdf.Rect(self.lines[0].bbox)
        for line in self.lines[1:]:
            rect |= line.bbox
        return rect

    @property
    def spans(self) -> list[Span]:
        return [s for line in self.lines for s in line.spans]

    @property
    def text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines)
