"""Pages that are images: comics, scanned documents (ADR 0008).

Needs the optional dependencies: `pip install doc-translator[images]`.
"""


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


# An image covering this share of a page is the page.
PAGE_SHARE = 0.9


def image_pages(doc) -> list[int]:
    """Pages with no text whose content is one image covering them."""
    import pymupdf

    found = []
    for index, page in enumerate(doc):
        if page.get_text("text").strip():
            continue
        infos = page.get_image_info(xrefs=True)
        if len(infos) == 1 and pymupdf.Rect(infos[0]["bbox"]).get_area() >= PAGE_SHARE * page.rect.get_area():
            found.append(index)
    return found
