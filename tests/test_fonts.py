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
    assert library.family("LiberationSans") == "sans-serif"
