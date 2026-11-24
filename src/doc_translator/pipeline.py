"""The general flow every document goes through: open, choose its process
(modes/), translate, save."""

from pathlib import Path

import pymupdf

from .modes import AUTO, DocumentError, Mode, choose
from .result import Result, collect_warnings
from .translators import Translator

__all__ = ["DocumentError", "Result", "open_pdf", "save", "translate", "translate_pdf"]


def open_pdf(source: Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(source)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise DocumentError(f"not a readable PDF ({error})") from error
    if not doc.is_pdf:
        raise DocumentError("not a PDF")
    if doc.needs_pass:
        raise DocumentError("the PDF is password-protected")
    return doc


def translate_pdf(source: Path, destination: Path, translator: Translator, mode: str = AUTO) -> Result:
    doc = open_pdf(source)
    return translate(doc, destination, translator, choose(doc, mode))


def translate(doc: pymupdf.Document, destination: Path, translator: Translator, mode: Mode) -> Result:
    """Translate `doc` with the process `mode` and save it to `destination`.

    A page on which reading or drawing fails is left as it was and recorded
    in `Result.failures`; the other pages are still translated. Translation
    errors stop the run: they concern the whole document (the engine is down).
    """
    result = Result()
    with collect_warnings(result.warnings):
        mode.translate(doc, translator, result)
    with result.timed("save"):
        save(doc, destination)
    return result


def save(doc: pymupdf.Document, destination: Path) -> None:
    doc.subset_fonts()
    # garbage=4 also merges duplicate objects: every inserted paragraph adds
    # its own copy of the same ToUnicode table (675 copies on a 29-page
    # manual, making the file four times larger).
    doc.save(destination, garbage=4, deflate=True)
