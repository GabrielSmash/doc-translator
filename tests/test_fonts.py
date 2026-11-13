import pymupdf
import pytest

from doc_translator.fonts import FontLibrary, _normalize
from tools.fixtures import LIBERATION_SANS as LIBERATION


@pytest.mark.parametrize(
    ("basefont", "span_font"),
    [
        ("ABCDEF+Charis SIL Bold", "CharisSIL-Bold"),
        ("Liberation Sans Regular", "LiberationSans"),
        ("XYZABC+Arial-BoldMT", "Arial-BoldMT"),
    ],
)
def test_pdf_and_span_font_names_match(basefont, span_font):
    assert _normalize(basefont) == _normalize(span_font)


def _pdf_with_font(subset: bool) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F1", fontfile=str(LIBERATION))
    page.insert_text((50, 80), "The team", fontname="F1")
    if subset:
        doc.subset_fonts()
    return pymupdf.open("pdf", doc.tobytes())


def test_full_embedded_font_is_reused():
    library = FontLibrary.from_document(_pdf_with_font(subset=False), {"LiberationSans": set("ação")})
    assert library.family("LiberationSans").startswith("embedded")


def test_subset_font_missing_glyphs_falls_back():
    library = FontLibrary.from_document(_pdf_with_font(subset=True), {"LiberationSans": set("ação")})
    assert library.family("LiberationSans") == "Helvetica"  # metric-compatible fallback


@pytest.mark.parametrize(
    ("name", "flags", "family"),
    [
        # Names of known families decide, whatever the flags say.
        ("HelveticaNeueLTStd-Roman", 4, "sans-serif"),  # IRS: declared serif
        ("ITCFranklinGothicStd-Demi", 4, "sans-serif"),
        ("TimesNewRomanPSMT", 0, "serif"),
        ("CourierNewPSMT", 0, "monospace"),
        ("ArialMT", None, "sans-serif"),
        # Unknown names follow the flags (arXiv's LaTeX fonts).
        ("NimbusRomNo9L-Regu", 4, "serif"),
        ("CMR10", 4, "serif"),
        ("SFTT1000", 4 | 8, "monospace"),
        # Neither: sans-serif.
        ("SymbolMT", None, "sans-serif"),
    ],
)
def test_generic_family(name, flags, family):
    from doc_translator.fonts import generic_family

    assert generic_family(name, flags) == family
