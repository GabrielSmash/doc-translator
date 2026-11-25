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

    # A "!" between letters, or starting a word before them, is a misread I.
    # A J before a period is an initial ("J.JONAH"), not a misread I.
    text = re.sub(
        r"(?<![A-Za-z])!?[A-Za-z0-9']*[A-Za-z][A-Za-z0-9']*(?<!J(?=\.))",
        lambda m: _as_letters(m.group(0), dictionary, ranks),
        text,
    )
    text = CENTS.sub(r"\1¢", text)
    return re.sub(rf"[A-Za-z']{{{MIN_RUN},}}", split, text)


# A price in cents, its sign read as a letter ("12t" for the cover's "12¢").
CENTS = re.compile(r"\b(\d{1,2})[tTcC]\b")

# Characters the OCR reads for letters of hand lettering: digits ("I5THAT",
# "151T"), and a J or an exclamation mark for an I ("SPEAK-JNG", "!T'S").
LETTER_LIKE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "J": "I", "!": "I"})
LOOK_ALIKE = set("01589J!")


def _as_letters(word: str, dictionary: Spelling, ranks: dict[str, int] | None) -> str:
    """`word` with characters the OCR misreads read as the letters they look
    like, when it is not made of known words as it is and that makes it
    known words ("I5THAT": "ISTHAT", "SPEAKJNG": "SPEAKING"); else as it is
    ("12T", "A7OH", "JUST")."""
    # A letter alone is an initial ("J.J."), not a misread word.
    if sum(c.isalnum() for c in word) < 2:
        return word
    if not LOOK_ALIKE & set(word.upper()) or _known_words(word, dictionary, ranks):
        return _one_letter_swapped(word, dictionary, ranks) or word
    letters = word.upper().translate(LETTER_LIKE) if word.isupper() or not word.isalpha() else word
    if any(c.isdigit() for c in letters):
        return word
    if letters == word or not _known_words(letters, dictionary, ranks):
        # Common letters read for others ("STUOIES": STUDIES): one at a time.
        return _one_letter_swapped(letters, dictionary, ranks) or word
    # An "!" ends a sentence as often as it is an I: read so, it must make one
    # word ("!T'S": "IT'S"; "!I'M" stays, "II'M" is no word).
    if "!" in word:
        if not _known_words(letters, dictionary, ranks):
            return word
        pieces = _split_by_use(letters, dictionary, ranks) if ranks else _split(letters, dictionary, frozenset())
        first = pieces[0].lower()
        return letters if len(first) > 1 and dictionary.has(first) else word
    return letters if _known_words(letters, dictionary, ranks) else word


# Letters of hand lettering the OCR reads for others: tried one at a time.
LETTER_SWAPS = {"O": "D", "D": "O", "L": "I", "I": "L"}
# A swap is taken when it makes the run this much likelier (the sum of
# log(rank) of its words, see _split_by_use), on runs this long.
# On the comic, the right ones gained 20 or more ("STUDIES", "BORED WITH"),
# the wrong ones 4 to 7 ("I OWE" -> "LOWE", "DR" -> "OR").
SWAP_GAIN = 10.0
SWAP_MIN = 5


def _one_letter_swapped(run: str, dictionary: Spelling, ranks: dict[str, int] | None) -> str | None:
    """`run` with one letter read as the one it looks like, when that makes
    it much more likely words ("STUOIES": "STU OI ES" are words, rare ones;
    "STUDIES" is common). Needs the word ranks."""
    if not ranks or not run.isupper() or not run.replace("'", "").isalpha() or len(run) < SWAP_MIN:
        return None
    cost, _ = _split_by_use(run, dictionary, ranks, common=COMMON, with_cost=True)
    best = None
    for index, char in enumerate(run):
        if swap := LETTER_SWAPS.get(char):
            candidate = run[:index] + swap + run[index + 1 :]
            candidate_cost, _ = _split_by_use(candidate, dictionary, ranks, common=COMMON, with_cost=True)
            if candidate_cost < cost - SWAP_GAIN and (best is None or candidate_cost < best[0]):
                best = (candidate_cost, candidate)
    return best[1] if best else None


def _known_words(run: str, dictionary: Spelling, ranks: dict[str, int] | None) -> bool:
    """Whether `run` splits into known words (letters only)."""
    if not run.replace("'", "").isalpha():
        return False
    words = _split_by_use(run, dictionary, ranks) if ranks else _split(run, dictionary, frozenset())
    return all(_known(w, dictionary) for w in words)


def _known(word: str, dictionary: Spelling) -> bool:
    """A word of the dictionary, a contraction of one ("IT'S"), or "a", "I"."""
    base = word.lower().split("'")[0] or word.lower()
    if len(base) == 1:
        return base in "ai"
    return dictionary.has(base) or dictionary.has(base.capitalize())


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
    run: str,
    dictionary: Spelling,
    ranks: dict[str, int],
    hyphen: bool = False,
    common: frozenset[str] = frozenset(),
    with_cost: bool = False,
):
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
    return (best[-1][0], best[-1][2]) if with_cost else best[-1][2]
