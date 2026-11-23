"""The words of a balloon, as the OCR read them, put back together.

The OCR drops most spaces of hand lettering ("THEX-RAYSSHOW AN UNCERTAIN
AMOUNT"), and lines end in words cut without a hyphen ("THE RADIA" /
"TION HAS"). The text is split again into the most likely words of the
source language.
"""

import re
from functools import cache

from ..languages import EN_PT_BR, LanguagePair
from ..spelling import Spelling, source_spelling
from ..terms import COMMON

MAX_WORD = 18
# A run of letters this long or longer may hold several words.
MIN_RUN = 4


def join(lines: list[str]) -> str:
    """A balloon's lines as one text: a word cut at a line's end is joined
    to its rest; `respace` puts the spaces back."""
    text = lines[0] if lines else ""
    for line in lines[1:]:
        if text.endswith("-") and not text.endswith("--"):
            text = text[:-1] + line
        elif text[-1:].isalpha() and line[:1].isalpha():
            text += line
        else:
            text += " " + line
    return text


def respace(text: str, languages: LanguagePair = EN_PT_BR) -> str:
    """`text` with the spaces of its runs of letters put back."""
    dictionary = source_spelling(languages)
    if dictionary is None:
        return text
    common = COMMON | languages.source_stopwords
    def split(match: re.Match) -> str:
        # A letter before a hyphen is a word ("X" of "THEX-RAYS").
        hyphen = text[match.end() : match.end() + 1] == "-"
        return " ".join(_split(match.group(0), dictionary, common, hyphen))

    return re.sub(rf"[A-Za-z']{{{MIN_RUN},}}", split, text)


def _split(run: str, dictionary: Spelling, common: frozenset[str], hyphen: bool = False) -> list[str]:
    """The words of `run`: as many letters as possible in known words, long
    words before short ones, and common words in a tie."""

    @cache
    def known(word: str) -> bool:
        if len(word) == 1:
            return word in "ai"
        return dictionary.has(word) or dictionary.has(word.capitalize())

    def score(piece: str, last: bool) -> int:
        word = piece.strip("'")
        if last and hyphen and len(word) == 1:
            return 1
        if not known(word):
            return -4 * len(piece) - 6
        return len(piece) ** 2 + (6 if word in common else 0)

    lower = run.lower()
    best: list[tuple[int, int, list[str]] | None] = [(0, 0, [])] + [None] * len(run)
    for end in range(1, len(run) + 1):
        options = []
        for start in range(max(0, end - MAX_WORD), end):
            if (before := best[start]) is not None:
                total, words, parts = before
                piece = lower[start:end]
                options.append((total + score(piece, end == len(run)), words - 1, [*parts, run[start:end]]))
        best[end] = max(options)
    return best[-1][2]
