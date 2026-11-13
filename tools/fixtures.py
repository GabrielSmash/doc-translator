"""Synthetic PDFs reproducing, in a few lines each, the layouts that broke in
the audit (docs/adr/0001-pipeline-audit-and-correction-plan.md).

They are generated rather than stored: no third-party documents in the
repository, and each case stays small enough to understand at a glance.
"""

import io
from pathlib import Path

import pymupdf

# Bundled (SIL OFL, see fonts/OFL.txt) so fixtures are identical on every
# machine: a system copy of another version would change glyph widths and
# with them every metric measured on the fixture.
LIBERATION_SANS = Path(__file__).resolve().parent / "fonts" / "LiberationSans-Regular.ttf"
A4 = pymupdf.paper_rect("a4")

PARAGRAPH = (
    "The frame controller provides a single point of access to every card in the "
    "frame. It handles the network communication and serves as a gateway between "
    "the network manager and the individual cards."
)


STYLED_HTML = """
<h1>Quarterly Product Report</h1>
<p>This document summarizes the <b>main results</b> of the third quarter.
Our team shipped <b>four new features</b> and fixed more than <i>two hundred</i> bugs.
For details, visit <a href="https://example.com/report">the full report</a>.</p>
<h2>Highlights</h2>
<ul>
<li>Revenue grew by <b>12%</b> compared to the previous quarter.</li>
<li>Customer satisfaction reached an all-time high.</li>
<li>The new onboarding flow reduced churn significantly.</li>
</ul>
<h2>Next Steps</h2>
<p style="font-family: serif">In the next quarter we will focus on <b>performance</b>,
<b>reliability</b> and a better experience for new users. Please send feedback to
<a href="mailto:team@example.com">team@example.com</a>.</p>
"""


def styled_text() -> bytes:
    """Bold and italic inside sentences, underlined links, a list, headings.

    Also written to samples/sample.pdf by scripts/make_sample.py.
    """
    content = A4 + (50, 50, -50, -50)
    story = pymupdf.Story(html=STYLED_HTML)
    # write_with_links keeps <a href> as real PDF link annotations.
    doc = story.write_with_links(lambda number, filled: (A4, content, None))
    return doc.tobytes()


def bordered_table() -> bytes:
    """Defect 2: a table with a rule under every row, close to the text.

    The rules sit where an underline would, so they are taken for underlines
    and removed together with the white-on-black header.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((60, 80), "Additional IP Address Tables", fontname="helv", fontsize=12)

    left, middle, right, top, row = 150, 330, 510, 110, 13.5
    page.draw_rect(pymupdf.Rect(left, top, right, top + row), color=None, fill=(0, 0, 0))
    page.insert_text((left + 6, top + 10.5), "Frame Controller Parameters", fontname="hebo", fontsize=10.5, color=(1, 1, 1))
    page.insert_text((middle + 60, top + 10.5), "IP Address", fontname="hebo", fontsize=10.5, color=(1, 1, 1))
    for index, label in enumerate(["Hostname", "IP Address", "Netmask", "Gateway", "Broadcast"], 1):
        y = top + index * row
        page.insert_text((left + 2, y + 10.5), label, fontname="helv", fontsize=10.5)
        # Thin filled rectangles, as Word and Acrobat draw table rules.
        page.draw_rect(pymupdf.Rect(left, y + row - 0.5, right, y + row), color=None, fill=(0, 0, 0))
    bottom = top + 6 * row
    for x in (left, middle, right):
        page.draw_rect(pymupdf.Rect(x - 0.25, top, x + 0.25, bottom), color=None, fill=(0, 0, 0))
    return doc.tobytes()


def rotated_text() -> bytes:
    """Defect 6: a vertical line in the margin, next to a normal paragraph."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_textbox(pymupdf.Rect(80, 80, 520, 200), PARAGRAPH, fontname="tiro", fontsize=11)
    page.insert_text(
        (40, 600), "This publication is available free of charge", fontname="tiro", fontsize=8, rotate=90
    )
    return doc.tobytes()


def _liberation_without(blank, family: str, layout_tables: bool = True) -> bytes:
    """Liberation Sans with the outlines of the `blank` characters emptied.

    Their slot and width stay: what Word does to glyphs it leaves out of a
    subset. Word also drops the layout tables (layout_tables=False): without
    GSUB, the shaping engine falls back to composing ligatures from the
    U+FB0x codes of the character map. A modified font may not keep the
    Reserved Font Name "Liberation" (SIL OFL), so it is renamed.
    """
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._g_l_y_f import Glyph

    font = TTFont(str(LIBERATION_SANS))
    for codepoint, name in font.getBestCmap().items():
        if blank(chr(codepoint)):
            font["glyf"][name] = Glyph()  # no contours, advance width kept
    for record in font["name"].names:
        if record.nameID in (1, 3, 4, 6, 16):
            record.string = family if record.nameID != 6 else f"{family}-Regular"
    if not layout_tables:
        for table in ("GSUB", "GPOS", "GDEF"):
            if table in font:
                del font[table]
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def _page_with_font(font: bytes, title: str, body: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_font(fontname="WordSubset", fontbuffer=font)
    page.insert_text((80, 80), title, fontname="WordSubset", fontsize=16)
    page.insert_textbox(pymupdf.Rect(80, 100, 520, 260), body, fontname="WordSubset", fontsize=11)
    return doc.tobytes()


def blank_accents() -> bytes:
    """Defect 3: an embedded font whose accented glyphs exist but are empty.

    Word subsets fonts this way: the glyph keeps its slot and width, so a
    coverage check that asks the font accepts it, and every accented letter
    of the translation comes out blank.
    """
    font = _liberation_without(lambda c: ord(c) > 127 and c.isalpha(), "WordSubsetTest")
    return _page_with_font(font, "Network Configuration", PARAGRAPH)


def blank_ligatures() -> bytes:
    """Defect 3, extended: empty ligature and bullet glyphs.

    The HTML engine writes "fi" as the ligature U+FB01 whenever the font maps
    it, so a reused Word subset printed "o ce" for "office". The bullet is a
    symbol the fallback font must draw while the rest keeps the original font.
    """
    font = _liberation_without(lambda c: c in "\ufb01\ufb02•", "WordLigatureTest", layout_tables=False)
    body = (
        "The office staff first configured the flow of traffic.\n"
        "• Find the official files.\n"
        "• Fill in the final fields."
    )
    return _page_with_font(font, "Office Workflow", body)


def internal_links() -> bytes:
    """Defect 5: a link to another page next to a link to a website."""
    doc = pymupdf.open()
    # Each page is finished before the next is created: new_page() leaves
    # earlier Page objects detached from the document.
    second = doc.new_page(width=A4.width, height=A4.height)
    second.insert_text((80, 100), "Configuration", fontname="hebo", fontsize=16)
    second.insert_text((80, 130), "Set the address of the frame controller before use.", fontname="helv", fontsize=11)
    first = doc.new_page(pno=0, width=A4.width, height=A4.height)
    first.insert_text((80, 100), "See the configuration section for the network settings.", fontname="helv", fontsize=11)
    first.insert_text((80, 130), "Updates are published on the project website.", fontname="helv", fontsize=11)
    goto = first.search_for("configuration section")[0]
    first.insert_link({"kind": pymupdf.LINK_GOTO, "from": goto, "page": 1, "to": pymupdf.Point(80, 80)})
    uri = first.search_for("project website")[0]
    first.insert_link({"kind": pymupdf.LINK_URI, "from": uri, "uri": "https://example.com"})
    return doc.tobytes()


def dark_band() -> bytes:
    """Defect 10: white text on a black band, with no room to spare.

    A longer translation that grows past the band, sideways or onto a new
    line, lands on the white page and disappears.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    title = "Frame Controller Parameters"
    width = pymupdf.get_text_length(title, fontname="hebo", fontsize=11)
    band = pymupdf.Rect(80, 100, 80 + width + 8, 116)
    page.draw_rect(band, color=None, fill=(0, 0, 0))
    page.insert_text((band.x0 + 4, band.y0 + 11.5), title, fontname="hebo", fontsize=11, color=(1, 1, 1))
    page.insert_textbox(pymupdf.Rect(80, 130, 520, 220), PARAGRAPH, fontname="helv", fontsize=10)
    return doc.tobytes()


def split_line() -> bytes:
    """A justified line written one word at a time, as some word processors
    do: the PDF gives each word its own block, and each was translated alone
    and written over the paragraph (7700 manual, page 6)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    left, right, size = 80, 380, 10
    page.insert_text((left, 100), "The rear panel kit consists of a screw and a lock washer,", fontname="helv", fontsize=size)
    words = ["and", "two", "small", "subassemblies,", "the", "connector", "module"]
    widths = [pymupdf.get_text_length(w, fontname="helv", fontsize=size) for w in words]
    gap = (right - left - sum(widths)) / (len(words) - 1)
    x = left
    for word, width in zip(words, widths):
        page.insert_text((x, 112), word, fontname="helv", fontsize=size)
        x += width + gap
    page.insert_text((left, 124), "and the rear plate that holds the connectors.", fontname="helv", fontsize=size)
    return doc.tobytes()


def ruled_table() -> bytes:
    """A table ruled on every side: labels on the left, centered values (some
    bold), a label that just fits its cell (7700 manual, pages 9 and 14).

    Text must stay inside its cell, centered values stay centered, and bold
    values are cells, not section headings.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((80, 90), "Configure the port settings of the terminal program as follows:", fontname="helv", fontsize=10)
    columns = [150, 214, 270, 360]
    rows = [
        ("Baud", "57600", "Transmit +"),
        ("Data bits", "8", "Transmit -"),
        ("Parity", "None", "N/A"),
        ("Stop bits", "2", "Receive +"),
        ("Flow Control", "Hardware", "N/A"),
    ]
    top, height, size = 104, 11.5, 9
    for index, cells in enumerate(rows):
        baseline = top + index * height + 8.8
        page.insert_text((columns[0] + 2, baseline), cells[0], fontname="helv", fontsize=size)
        for column, text in enumerate(cells[1:], 1):
            font = "hebo" if column == 1 else "helv"
            width = pymupdf.get_text_length(text, fontname=font, fontsize=size)
            center = (columns[column] + columns[column + 1]) / 2
            page.insert_text((center - width / 2, baseline), text, fontname=font, fontsize=size)
    bottom = top + len(rows) * height
    for index in range(len(rows) + 1):
        y = top + index * height
        page.draw_line((columns[0], y), (columns[-1], y), width=0.5)
    for x in columns:
        page.draw_line((x, top), (x, bottom), width=0.5)
    page.insert_text((80, bottom + 24), "Install the card into the frame after the settings are saved.", fontname="helv", fontsize=10)
    return doc.tobytes()


def toc_leaders() -> bytes:
    """A table of contents: dot leaders and page numbers, some bold. Each
    number must stay on its own entry's row (7700 manual, page 2)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    entries = [
        (80, "1.  OVERVIEW", "1", True),
        (80, "2.  INSTALLATION", "2", True),
        (100, "2.1.  Installing the Module Rear Plate", "2", False),
        (100, "2.2.  Rear Panel Connections", "3", False),
        (120, "2.2.1.  Ethernet Connection", "3", False),
        (120, "2.2.2.  Serial Port Connections", "4", False),
        (80, "3.  SPECIFICATIONS", "6", True),
    ]
    right, size = 500, 10
    for index, (left, title, number, bold) in enumerate(entries):
        font = "hebo" if bold else "helv"
        baseline = 100 + index * 13
        title_width = pymupdf.get_text_length(title + " ", fontname=font, fontsize=size)
        number_width = pymupdf.get_text_length(number, fontname=font, fontsize=size)
        dot = pymupdf.get_text_length(".", fontname=font, fontsize=size)
        dots = "." * int((right - number_width - left - title_width) / dot - 1)
        line = f"{title} {dots}"
        page.insert_text((left, baseline), line, fontname=font, fontsize=size)
        page.insert_text((right - number_width, baseline), number, fontname=font, fontsize=size)
    return doc.tobytes()


FIXTURES = {
    "styled_text": styled_text,
    "bordered_table": bordered_table,
    "rotated_text": rotated_text,
    "blank_accents": blank_accents,
    "blank_ligatures": blank_ligatures,
    "internal_links": internal_links,
    "dark_band": dark_band,
    "split_line": split_line,
    "ruled_table": ruled_table,
    "toc_leaders": toc_leaders,
}


def build(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, make in FIXTURES.items():
        path = directory / f"{name}.pdf"
        path.write_bytes(make())
        paths[name] = path
    return paths
