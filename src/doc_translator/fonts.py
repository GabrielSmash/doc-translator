"""Reuse the fonts embedded in the source PDF for the translated text.

A font is reused only if it can draw every letter the translation needs.
Subset fonts usually can't (they only hold the glyphs of the original text),
and those fall back to a generic family of the same kind. Other characters
it can't draw (a bullet, a digit) are taken from the fallback font alone.
"""

import io
import logging
import re

import numpy as np
import pymupdf

log = logging.getLogger(__name__)

# Families recognized by name, checked in this order. Names win over the PDF
# flags because producers set those wrongly (the IRS forms declare Helvetica
# serif); flags decide for names not listed (Computer Modern, Nimbus...).
# "Roman" is not a hint: it names a weight ("HelveticaNeue-Roman").
MONO_NAMES = ("mono", "courier", "consol", "menlo")
SANS_NAMES = (
    "sans", "helvetica", "arial", "franklin", "calibri", "verdana", "tahoma",
    "segoe", "frutiger", "univers", "futura", "gill", "myriad", "roboto", "lato",
)
SERIF_NAMES = (
    "serif", "times", "georgia", "garamond", "cambria", "palatino", "bookman",
    "charis", "minion", "baskerville", "century", "tiro",
)
FLAG_SERIF = 4
FLAG_MONO = 8
SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
DEFAULT_STYLE_SUFFIX = re.compile(r"(regular|normal|book)$")
# Coverage probe: characters are drawn at this size and resolution, and a
# character counts as drawn when its cell holds this much contrast.
PROBE_SIZE = 24
PROBE_DPI = 150
MIN_INK_CONTRAST = 8
# CSS family used when a font can't be reused. MuPDF's "serif" is Charis SIL,
# 10% wider than Times; "Times" gives Nimbus Roman, metric-compatible with
# Times New Roman, so the translation takes the room the original took.
FALLBACK_CSS = {"serif": "Times", "sans-serif": "Helvetica", "monospace": "Courier"}
# Ligature characters. The HTML engine replaces "fi" with U+FB01 whenever the
# font maps that code, with no way to turn it off; in Word subsets the glyph
# is often empty, and either way the output text reads "oﬃce", not "office".
LIGATURES = {chr(code) for code in range(0xFB00, 0xFB07)}
# TrueType and OpenType files start with one of these; their character map
# can be edited. Type 1 and bare CFF fonts can't.
SFNT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true")


class FontLibrary:
    def __init__(self, flags: dict[str, int] | None = None) -> None:
        self.archive = pymupdf.Archive()
        self._families: dict[str, str] = {}
        self._faces: list[str] = []
        self._flags = flags or {}

    @classmethod
    def from_document(
        cls,
        doc: pymupdf.Document,
        needed: dict[str, set[str]],
        flags: dict[str, int] | None = None,
    ) -> "FontLibrary":
        """`needed` maps each span font name to the characters it must draw;
        `flags` to its PDF font flags, used to pick a fallback family."""
        library = cls(flags)
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
            try:
                prepared = _prepare(buffer, chars, name)
            except Exception as error:  # a damaged font must not stop the document
                log.warning("Font %s: cannot be read (%s), using fallback", name, error)
                continue
            if prepared is not None:
                library._add(name, prepared, ext)
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
    def reused(self) -> list[str]:
        """Names of the source fonts whose embedded file is reused."""
        return list(self._families)

    @property
    def css(self) -> str:
        return "\n".join(self._faces)

    def family(self, font_name: str) -> str:
        """CSS font-family for text originally in `font_name`.

        A reused font lists its fallback after it: characters removed from
        it (see _prepare) are drawn by a font of the same kind.
        """
        fallback = FALLBACK_CSS[generic_family(font_name, self._flags.get(font_name))]
        if family := self._families.get(font_name):
            return f"{family}, {fallback}"
        return fallback


def _prepare(buffer: bytes, chars: set[str], name: str) -> bytes | None:
    """The font file to reuse for `chars`, or None to use the fallback."""
    missing = _undrawable(buffer, chars)
    if letters := {c for c in missing if c.isalpha()}:
        # Mixing fonts inside words ("ação" with a foreign "ç") is worse
        # than a similar font for the whole text.
        log.info("Font %s: cannot draw %r, using fallback", name, "".join(sorted(letters))[:20])
        return None

    if buffer[:4] in SFNT_MAGIC:
        try:
            return _without_chars(buffer, missing | LIGATURES)
        except Exception as error:  # damaged subset tables
            log.info("Font %s: cannot be edited (%s), using fallback", name, error)
            return None

    # Type 1 / CFF: the map can't be edited, so the engine will compose
    # ligatures; accept the font only if they are drawn and nothing else is
    # missing.
    font = pymupdf.Font(fontbuffer=buffer)
    ligatures = {c for c in LIGATURES if font.has_glyph(ord(c))}
    if missing or _undrawable(buffer, ligatures):
        log.info("Font %s: cannot draw %r, using fallback", name, "".join(sorted(missing))[:20] or "ligatures")
        return None
    return buffer


def _without_chars(buffer: bytes, chars: set[str]) -> bytes:
    """`buffer` with `chars` removed from its character map.

    Removed characters are drawn by the next font in the CSS font-family
    list; removed ligatures make the engine keep "f" and "i" apart.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(buffer))
    codes = {ord(c) for c in chars}
    changed = False
    for table in font["cmap"].tables:
        for code in codes & set(table.cmap):
            del table.cmap[code]
            changed = True
    if not changed:
        return buffer
    output = io.BytesIO()
    font.save(output)
    return output.getvalue()


def _embedded_fonts(doc: pymupdf.Document) -> dict[str, int]:
    fonts: dict[str, int] = {}
    for page in doc:
        for xref, ext, _, basefont, _, _ in page.get_fonts():
            if ext != "n/a":  # "n/a" = not embedded (e.g. base-14 fonts)
                fonts.setdefault(_normalize(basefont), xref)
    return fonts


def _undrawable(buffer: bytes, chars: set[str]) -> set[str]:
    """Characters this font cannot draw, found by drawing them.

    Asking the font is not enough: Word subsets keep the slot, width and box
    of glyphs they drop, so has_glyph() succeeds and the letter comes out
    blank. Each character is drawn with this font alone (TextWriter: no
    fallback font, no ligatures) at a known spot, which is checked for ink.
    """
    font = pymupdf.Font(fontbuffer=buffer)
    probe = sorted(c for c in chars if not c.isspace())
    missing = {c for c in probe if not font.has_glyph(ord(c))}
    probe = [c for c in probe if c not in missing]
    if not probe:
        return missing

    step = PROBE_SIZE * 2  # one cell per character, generous for wide glyphs
    columns = 10
    doc = pymupdf.open()
    page = doc.new_page(width=step * columns, height=step * (1 + len(probe) // columns))
    writer = pymupdf.TextWriter(page.rect)
    cells = []
    for index, char in enumerate(probe):
        x, y = (index % columns) * step, (index // columns) * step
        writer.append((x + PROBE_SIZE / 2, y + PROBE_SIZE * 1.4), char, font=font, fontsize=PROBE_SIZE)
        cells.append((char, pymupdf.Rect(x, y, x + step, y + step)))
    writer.write_text(page)

    pixmap = page.get_pixmap(dpi=PROBE_DPI, colorspace=pymupdf.csGRAY)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)
    zoom = PROBE_DPI / 72
    for char, cell in cells:
        x0, y0, x1, y1 = (int(v * zoom) for v in cell)
        if image[y0:y1, x0:x1].std() < MIN_INK_CONTRAST:
            missing.add(char)
    return missing


def _normalize(name: str) -> str:
    """'ABCDEF+Charis SIL Bold' and 'CharisSIL-Bold' -> 'charissilbold'.

    The default style suffix is dropped: 'Liberation Sans Regular' and
    'LiberationSans' name the same font.
    """
    compact = re.sub(r"[\s\-_,]", "", SUBSET_PREFIX.sub("", name)).lower()
    return DEFAULT_STYLE_SUFFIX.sub("", compact)


def generic_family(font_name: str, flags: int | None = None) -> str:
    """serif, sans-serif or monospace: by family name, else by PDF flags."""
    name = _normalize(font_name)
    for hints, family in (
        (MONO_NAMES, "monospace"),
        (SANS_NAMES, "sans-serif"),
        (SERIF_NAMES, "serif"),
    ):
        if any(hint in name for hint in hints):
            return family
    if flags is not None:
        if flags & FLAG_MONO:
            return "monospace"
        if flags & FLAG_SERIF:
            return "serif"
    return "sans-serif"
