"""Internal representation of the text extracted from a PDF page."""

from dataclasses import dataclass, field

import pymupdf

# PyMuPDF span flag bits (see pymupdf.TEXT_FONT_*).
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
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
    # PDFs draw underlines as separate vector lines, detected at extraction.
    underline: bool = False
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

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def baseline(self) -> float:
        # The largest span sets the baseline; superscripts sit above it.
        return max(self.spans, key=lambda s: s.size).baseline


@dataclass
class Leader:
    """Dot leader and page number ending a table-of-contents entry."""

    number: str
    font: str
    size: float
    color: int
    bold: bool
    right: float  # x where the page number ends


@dataclass
class Paragraph:
    """A unit of text that is translated as a whole.

    `prefix` holds list markers ("•", "1.") that must not be translated.
    `align` is a CSS text-align value: left, right, center or justify.
    """

    lines: list[Line] = field(default_factory=list)
    prefix: str = ""
    align: str = "left"
    leader: Leader | None = None

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
