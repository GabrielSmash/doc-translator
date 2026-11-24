"""The processes a document can go through (ADR 0009).

Each kind of document has its own process, built on the shared parts
(engines, glossary, cache, checks, report): a document with a text layer
keeps its layout; a comic is read by OCR and lettered again. A process
also chooses the defaults that suit it (a comic's balloons need the
context-aware engine).
"""

from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from .. import images
from ..result import Result
from ..translators import Translator


class DocumentError(Exception):
    """A document this tool cannot translate."""


@dataclass(frozen=True)
class Mode:
    name: str
    engine: str  # default engine: "fast" or "llm"
    llm_model: str  # default Ollama model
    translate: Callable[[pymupdf.Document, Translator, Result], None]


def _document(doc, translator, result):
    from .document import translate

    translate(doc, translator, result)


def _comic(doc, translator, result):
    from .comic import translate

    translate(doc, translator, result)


# The 8b model is slower (it does not fit a 4 GB card) but follows a story:
# on a whole comic it kept "arms" as "braços" where the 4b wrote "pernas".
MODES = {
    "document": Mode("document", "fast", "qwen3:4b-instruct", _document),
    "comic": Mode("comic", "llm", "qwen3:8b", _comic),
}
AUTO = "auto"


def choose(doc: pymupdf.Document, requested: str = AUTO) -> Mode:
    """The process for `doc`: the one `requested`, or by its pages (no text
    and page images: a comic)."""
    pictures = images.image_pages(doc)
    has_text = any(page.get_text("text").strip() for page in doc)
    if requested == AUTO:
        if has_text:
            requested = "document"
        elif pictures:
            requested = "comic"
        else:
            raise DocumentError("the PDF has no text and no page images to read")
    mode = MODES[requested]
    if mode.name == "document" and not has_text:
        raise DocumentError("the PDF has no text layer; for a comic, use --mode comic")
    if mode.name == "comic":
        if not pictures:
            raise DocumentError("no page of the PDF is an image to read")
        if not images.available():
            raise DocumentError(
                f"{len(pictures)} pages are images; install the OCR support to translate them: "
                "pip install 'doc-translator[images]'"
            )
    if mode.name == "document" and pictures:
        import logging

        logging.getLogger(__name__).warning(
            "%d pages are images and are left as they are; a comic is translated with --mode comic",
            len(pictures),
        )
    return mode
