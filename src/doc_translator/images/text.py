"""The words of a balloon, as the OCR read them, put back together.

The OCR drops most spaces of hand lettering ("THEX-RAYSSHOW AN UNCERTAIN
AMOUNT"), and lines end in words cut without a hyphen ("THE RADIA" /
"TION HAS"). The text is split again into the most likely words of the
source language: the most used ones, by a list of words ranked by use
(Zipf's law: a word's probability is about 1 / (rank * log N)). Without
the list, known words, long ones first: "MYNAME" is then "MYNA ME".
"""

import math
import re
from functools import cache

from ..languages import EN_PT_BR, LanguagePair
from ..spelling import Spelling, source_spelling, source_word_ranks
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
    ranks = source_word_ranks(languages)

    def split(match: re.Match) -> str:
        # A letter before a hyphen is a word ("X" of "THEX-RAYS").
        hyphen = text[match.end() : match.end() + 1] == "-"
        if ranks:
            return " ".join(_split_by_use(match.group(0), dictionary, ranks, hyphen, common))
        return " ".join(_split(match.group(0), dictionary, common, hyphen))

    text = re.sub(r"[A-Za-z0-9']*[A-Za-z][A-Za-z0-9']*", lambda m: _digits_as_letters(m.group(0), dictionary, ranks), text)
    return re.sub(rf"[A-Za-z']{{{MIN_RUN},}}", split, text)


# Digits the OCR reads for letters of hand lettering ("I5THAT", "151T").
LETTER_LIKE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"})


def _digits_as_letters(word: str, dictionary: Spelling, ranks: dict[str, int] | None) -> str:
    """`word`, letters and digits, with its digits read as the letters they
    look like when that makes known words ("I5THAT": "ISTHAT"); else as
    it is ("12T", "A7OH")."""
    if not any(c.isdigit() for c in word):
        return word
    letters = word.translate(LETTER_LIKE)
    if any(c.isdigit() for c in letters):
        return word
    words = _split_by_use(letters, dictionary, ranks) if ranks else _split(letters, dictionary, frozenset())
    known = all(
        w.lower() in "ai" if len(w) == 1 else dictionary.has(w.lower()) or dictionary.has(w.capitalize())
        for w in words
    )
    return letters if known else word


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


# Endings joined by an apostrophe to any word: "THEY'RE", "WIRING'LL"
# ("DON'T", "ISN'T" are in the dictionary).
CONTRACTIONS = frozenset({"s", "ll", "re", "ve", "d", "m"})
# The ranks come from Wikipedia, where speech is rare: "me" is the 600th
# word, and "TOME" beat "TO ME". The language's common words are taken as
# used at least this much.
COMMON_RANK = 100
# Letters the OCR reads for another in hand lettering: a lone "L" is "I"
# ("L MUST RETURN").
MISREAD = {"l": "I"}


def _split_by_use(
    run: str, dictionary: Spelling, ranks: dict[str, int], hyphen: bool = False, common: frozenset[str] = frozenset()
) -> list[str]:
    """The words of `run` most likely together: the least total cost, a
    word's cost being log(rank * log N). A word the dictionary has and the
    list lacks costs as the rarest; one only the list has (frequent
    misspellings and joined words: "noone", "thankyou") or neither has,
    much more."""
    scale = math.log(len(ranks))
    rarest = math.log(len(ranks) * scale)
    unknown = 2 * rarest

    def known(word: str) -> bool:
        return dictionary.has(word) or dictionary.has(word.capitalize())

    def rank_of(word: str) -> int | None:
        rank = ranks.get(word)
        return min(rank, COMMON_RANK) if rank is not None and word in common else rank

    @cache
    def cost(piece: str, last: bool) -> float:
        # An apostrophe ends a word ("OCTOPUS' ARMS", "PICKIN' ON") or joins
        # an ending to it; it does not start one.
        if piece.startswith("'"):
            return unknown * len(piece)
        word = piece.rstrip("'")
        if len(word) == 1:
            return math.log(((rank_of(word) or 0) + 1) * scale) if word in "ai" or (last and hyphen) else unknown
        if "'" in word:
            # Contractions are at the list's end: "isn't" is as used as "is";
            # "ain't" is only in the list.
            base, _, ending = word.rpartition("'")
            if known(word) or word in ranks:
                rank = rank_of(word.split("'")[0])
                return rarest if rank is None else math.log((rank + 1) * scale)
            # "WIRING'LL": a word and an ending.
            if ending in CONTRACTIONS and "'" not in base and len(base) > 1:
                return cost(base, False)
            return unknown * len(word)
        rank = rank_of(word)
        if not known(word):
            return unknown * (1 if rank is not None else len(word))
        return rarest if rank is None else math.log((rank + 1) * scale)

    lower = run.lower()
    best: list[tuple[float, int, list[str]] | None] = [(0.0, 0, [])] + [None] * len(run)
    for end in range(1, len(run) + 1):
        options = []
        for start in range(max(0, end - MAX_WORD), end):
            if (before := best[start]) is not None:
                total, words, parts = before
                piece = lower[start:end]
                written = run[start:end]
                if piece in MISREAD:
                    piece, written = MISREAD[piece].lower(), MISREAD[piece]
                options.append((total + cost(piece, end == len(run)), words + 1, [*parts, written]))
        best[end] = min(options, key=lambda option: option[:2])
    return best[-1][2]
