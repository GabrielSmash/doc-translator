"""What a run produced, whichever process translated the document."""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from .analysis import Block
from .fonts import FontLibrary
from .layout import Placement, TranslatedParagraph
from .translators import Unit, UnitTranslation

log = logging.getLogger(__name__)


@dataclass
class Result:
    """What each stage produced, so it can be inspected and measured.

    The measurement harness (tools/measure.py) reads this instead of
    re-implementing the stages, so it measures exactly what the CLI runs.
    """

    pages: list[list[Block]] = field(default_factory=list)  # every block, kept ones too
    units: list[Unit] = field(default_factory=list)
    translations: list[UnitTranslation] = field(default_factory=list)
    # Indices into units translated as one paragraph (pieces cut by a page
    # break or a block boundary), one list per paragraph.
    groups: list[list[int]] = field(default_factory=list)
    translated: list[TranslatedParagraph] = field(default_factory=list)
    fonts: FontLibrary | None = None
    placements: list[Placement] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    # Pages left in the original language because a stage failed on them
    # (page index -> reason), and the warnings logged during the run.
    failures: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # A comic's pages, read by OCR (modes.comic.ComicResult).
    images: object | None = None

    def fail(self, index: int, stage: str, error: Exception) -> None:
        self.failures[index] = f"{stage}: {type(error).__name__}: {error}"
        log.error("Page %d left untranslated (%s)", index + 1, self.failures[index])
        log.debug("Traceback", exc_info=error)

    @contextmanager
    def timed(self, stage: str):
        start = time.perf_counter()
        yield
        self.seconds[stage] = self.seconds.get(stage, 0.0) + time.perf_counter() - start


class _WarningCollector(logging.Handler):
    def __init__(self, messages: list[str]) -> None:
        super().__init__(logging.WARNING)
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno == logging.WARNING:  # errors are in Result.failures
            self.messages.append(record.getMessage())


@contextmanager
def collect_warnings(messages: list[str]):
    """Copy the warnings logged by this package into `messages`."""
    logger = logging.getLogger("doc_translator")
    handler = _WarningCollector(messages)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
