"""Reuse the fonts embedded in the source PDF for the translated text.

A font is reused only if it can draw every character the translation needs
with it. Subset fonts usually can't (they only hold the glyphs of the original
text), and those fall back to a generic family of the same kind.
"""

import logging
import re

import pymupdf

log = logging.getLogger(__name__)

SERIF_HINTS = ("times", "serif", "roman", "charis", "georgia", "garamond", "cambria", "tiro")
MONO_HINTS = ("mono", "courier", "consol", "code")
SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
DEFAULT_STYLE_SUFFIX = re.compile(r"(regular|normal|book)$")


class FontLibrary:
    def __init__(self) -> None:
        self.archive = pymupdf.Archive()
        self._families: dict[str, str] = {}
        self._faces: list[str] = []

    @classmethod
    def from_document(
        cls, doc: pymupdf.Document, needed: dict[str, set[str]]
    ) -> "FontLibrary":
        """`needed` maps each span font name to the characters it must draw."""
        library = cls()
        embedded = _embedded_fonts(doc)
        for name, chars in needed.items():
            xref = embedded.get(_normalize(name))
            if xref is None:
                log.info("Font %s: not embedded, using fallback", name)
                continue
            _, ext, _, buffer = doc.extract_font(xref)
            if not buffer:
                log.info("Font %s: cannot be extracted, using fallback", name)
                continue
            missing = _missing_chars(pymupdf.Font(fontbuffer=buffer), chars)
            if missing:
                log.info("Font %s: lacks %r, using fallback", name, "".join(sorted(missing))[:20])
                continue
            library._add(name, buffer, ext)
        return library

    def _add(self, name: str, buffer: bytes, ext: str) -> None:
        family = f"embedded{len(self._families)}"
        filename = f"{family}.{ext}"
        self.archive.add(buffer, filename)
        # The file already is the bold/italic face: declaring every weight and
        # style for it keeps the engine from synthesizing bold on top of it.
        for weight in ("normal", "bold"):
            for style in ("normal", "italic"):
                self._faces.append(
                    f"@font-face {{ font-family: {family}; src: url({filename}); "
                    f"font-weight: {weight}; font-style: {style}; }}"
                )
        self._families[name] = family
        log.info("Font %s: reusing embedded font", name)

    @property
    def css(self) -> str:
        return "\n".join(self._faces)

    def family(self, font_name: str) -> str:
        if family := self._families.get(font_name):
            return family
        return _generic_family(font_name)


def _embedded_fonts(doc: pymupdf.Document) -> dict[str, int]:
    fonts: dict[str, int] = {}
    for page in doc:
        for xref, ext, _, basefont, _, _ in page.get_fonts():
            if ext != "n/a":  # "n/a" = not embedded (e.g. base-14 fonts)
                fonts.setdefault(_normalize(basefont), xref)
    return fonts


def _missing_chars(font: pymupdf.Font, chars: set[str]) -> set[str]:
    missing = set()
    for char in chars:
        if char.isspace():
            continue
        # Some subsetters keep the glyph slot but drop its outline.
        if not font.has_glyph(ord(char)) or font.glyph_bbox(ord(char)).is_empty:
            missing.add(char)
    return missing


def _normalize(name: str) -> str:
    """'ABCDEF+Charis SIL Bold' and 'CharisSIL-Bold' -> 'charissilbold'.

    The default style suffix is dropped: 'Liberation Sans Regular' and
    'LiberationSans' name the same font.
    """
    compact = re.sub(r"[\s\-_,]", "", SUBSET_PREFIX.sub("", name)).lower()
    return DEFAULT_STYLE_SUFFIX.sub("", compact)


def _generic_family(font_name: str) -> str:
    name = font_name.lower()
    if any(hint in name for hint in MONO_HINTS):
        return "monospace"
    if any(hint in name for hint in SERIF_HINTS):
        return "serif"
    return "sans-serif"
