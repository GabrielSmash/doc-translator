"""Internal representation of the text extracted from a PDF page."""

from dataclasses import dataclass, field

import pymupdf

# PyMuPDF span flag bits (see pymupdf.TEXT_FONT_*).
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

    @property
    def bold(self) -> bool:
        return bool(self.flags & FLAG_BOLD)

    @property
    def italic(self) -> bool:
        return bool(self.flags & FLAG_ITALIC)


@dataclass
class Line:
    spans: list[Span]
    bbox: pymupdf.Rect

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Paragraph:
    """A unit of text that is translated as a whole.

    `prefix` holds list markers ("•", "1.") that must not be translated.
    """

    lines: list[Line] = field(default_factory=list)
    prefix: str = ""

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
