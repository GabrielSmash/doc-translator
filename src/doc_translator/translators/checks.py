"""Checks shared by every translation engine, and the repairs they allow.

In a technical document a number, a unit sign or a code carries the fact;
a wrong word is a style problem, a wrong number is a wrong instruction
("192.168.8.140" became "190.168.8.140", "255.255.255.0" became
"25-255.255.255.0", "100Ω" became "100<tool_call>"). Engines retry what
these checks find; what is still wrong after the retry is repaired here,
the same way for every engine.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ..languages import EN_PT_BR
from .base import PROTECTED

NUMBER = re.compile(r"\d+(?:[.,/:]\d+)*")
# Signs that carry a value (units, legal marks): a translation keeps them.
SYMBOLS = frozenset("\u2126\u03a9®™©°\u00b5\u03bc±×≤≥≈∞")
# One sign, two characters: the OHM SIGN and the MICRO SIGN look like the
# Greek letters a translation writes ("100Ω" in the manual is U+2126).
SAME_SIGN = {"\u2126": "\u03a9", "\u00b5": "\u03bc"}
# Control tokens of chat models, leaked into an answer.
MODEL_TOKEN = re.compile(r"<\|[^<>|]*\|>|</?(?:tool_call|tool_response|think)>")
# A number written right before a sign ("100Ω", "25 °"), so the sign can be
# put back after the same number.
NUMBER_SYMBOL = re.compile(r"(\d+(?:[.,]\d+)*)\s?([" + "".join(SYMBOLS) + "])")
# A code: letters and digits in one word, maybe joined by "_-." ("RJ45",
# "CR2032", "fc_release-2-19-b1", "802.3u"). It has no translation; a
# changed letter ("RG45") is as wrong as a changed digit. Not "/": it joins
# words in formulas ("pos/10000/dmodel") and alternatives ("RS-232/RS-422").
CODE = re.compile(
    r"(?<![\w.\-])(?=[\w.\-]*[A-Za-z])(?=[\w.\-]*\d)[A-Za-z0-9]+(?:[_\-.][A-Za-z0-9]+)*"
)
ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)", re.IGNORECASE)
# A word typed against a number ("up to10 minutes") is a typo, not a code.
GLUED = re.compile(r"([a-z]+)\d+|\d+([a-z]+)")


def _digits(number: str) -> str:
    return re.sub(r"[.,]", "", number)


def numbers(text: str) -> set[str]:
    """Digits of each number, ignoring separators: "2.5" and "2,5" (pt-BR
    decimal comma), "1,000" and "1.000" are the same number; "½" is "1/2"."""
    plain = unicodedata.normalize("NFKC", text).replace("\u2044", "/")
    return {_digits(n) for n in NUMBER.findall(plain)}


def lost_numbers(source: str, translation: str) -> set[str]:
    """Numbers of `source` missing from `translation`."""
    return numbers(source) - numbers(translation)


def added_numbers(source: str, translation: str) -> set[str]:
    """Numbers of `translation` that `source` doesn't have."""
    return numbers(translation) - numbers(source)


def _signs(text: str) -> set[str]:
    return {SAME_SIGN.get(c, c) for c in text if c in SYMBOLS}


def lost_symbols(source: str, translation: str) -> set[str]:
    return _signs(source) - _signs(translation)


def codes(text: str, stopwords: frozenset[str] = EN_PT_BR.source_stopwords) -> list[str]:
    return [c for c in CODE.findall(text) if not ORDINAL.fullmatch(c) and not _glued(c, stopwords)]


def _glued(code: str, stopwords: frozenset[str]) -> bool:
    match = GLUED.fullmatch(code)
    return bool(match) and (match.group(1) or match.group(2)) in stopwords


def _plain(text: str) -> str:
    """Without the spacing a translation may change: "3V" is "3 V" in pt-BR,
    "RJ-45" is "RJ45", "2.5mm" is "2,5 mm"."""
    return re.sub(r"[\s\-]", "", text).replace(",", ".").lower()


def lost_codes(source: str, translation: str) -> list[str]:
    """Codes of `source` not found, as written, in `translation`."""
    plain = _plain(unicodedata.normalize("NFKC", translation))
    return [c for c in dict.fromkeys(codes(source)) if _plain(c) not in plain]


def missing_tokens(source: str, translation: str) -> list[str]:
    """What the translation dropped or changed, as the source writes it, to
    name it to the engine: the codes, then numbers that are not part of a
    lost code ("45" of "RJ45")."""
    lost = lost_codes(source, translation)
    digits = lost_numbers(source, translation)
    in_codes = {_digits(n) for c in lost for n in NUMBER.findall(c)}
    numbers = [n for n in dict.fromkeys(NUMBER.findall(source)) if _digits(n) in digits - in_codes]
    return lost + numbers


# All-capital words of 3 letters or more, standing alone ("BNC", "FEC";
# "DVB-S" is a code).
CAPITALS = re.compile(r"(?<![\w\-/])[A-Z]{3,}(?![\w\-/])")


def lost_abbreviations(
    source: str, translation: str, known: "Vocabulary | None", languages=EN_PT_BR
) -> list[str]:
    """Abbreviations of `source` missing from `translation`: dropped ("BNC
    Female" -> "Conector fêmea") or spelled out ("FEC" -> "CORREÇÃO DE
    ERRO"). Abbreviations are capital words that are not words of the
    source language ("NOTE", "STANDARD" are), nor of the target ("CA" for
    "AC" is not checked: two letters)."""
    if known is None:
        return []
    found = [w for w in CAPITALS.findall(source) if w.lower() not in known.ordinary]
    own = dict(languages.translated_abbreviations)
    return [
        w
        for w in dict.fromkeys(found)
        if not any(re.search(rf"(?<!\w){re.escape(form)}(?!\w)", translation) for form in (w, own.get(w, w)))
    ]


def model_tokens(source: str, translation: str) -> list[str]:
    return [t for t in MODEL_TOKEN.findall(translation) if t not in source]


def token_problems(source: str, translation: str) -> list[str]:
    """What a translation changed that it must keep as written."""
    problems = []
    if lost := lost_numbers(source, translation):
        problems.append("numbers changed: " + ", ".join(sorted(lost)))
    if lost := lost_codes(source, translation):
        problems.append("codes changed: " + ", ".join(lost))
    if added := added_numbers(source, translation):
        problems.append("numbers added: " + ", ".join(sorted(added)))
    if lost := lost_symbols(source, translation):
        problems.append("symbols lost: " + "".join(sorted(lost)))
    if tokens := model_tokens(source, translation):
        problems.append("model tokens: " + ", ".join(tokens))
    return problems


def repair_tokens(source: str, translation: str) -> str:
    """`translation` with model tokens removed, numbers the engine altered
    put back and lost signs written again after their number.

    Only unambiguous repairs: a number of the translation is replaced only
    if the source doesn't have it and the source's number in its place is
    missing from the translation (a reordered sentence is left alone)."""
    text = MODEL_TOKEN.sub(lambda m: "" if m.group(0) not in source else m.group(0), translation)
    text = _repair_numbers(source, _repair_glued(source, text))
    lost = lost_symbols(source, text)
    for match in NUMBER_SYMBOL.finditer(source):
        number, symbol = match.groups()
        if SAME_SIGN.get(symbol, symbol) not in lost:
            continue
        text = _put_sign(source[: match.start()], number, symbol, text, source)
    return re.sub(r"  +", " ", text).strip() if text != translation else translation


def _put_sign(before: str, number: str, symbol: str, text: str, source: str) -> str:
    """`symbol` written again after the same occurrence of `number` (the
    third "100" of the source is the third of the translation: "100Base-TX
    (100 Mbps) ... 100Ω"). Letters the engine glued in its place, which the
    source doesn't have, go ("100ósito")."""
    standalone = re.compile(rf"(?<![\d.,]){re.escape(number)}(?![\d.,]*\d)")
    found = list(standalone.finditer(text))
    if not found:
        return text
    spot = found[min(len(standalone.findall(before)), len(found) - 1)]
    glued = re.match(r"[^\W\d_]+", text[spot.end() :])
    end = spot.end() + (len(glued.group(0)) if glued and glued.group(0) not in source else 0)
    return text[: spot.end()] + symbol + text[end:]


def _repair_numbers(source: str, translation: str) -> str:
    wanted = NUMBER.findall(source)
    found = list(NUMBER.finditer(translation))
    in_source = {_digits(n) for n in wanted}
    in_translation = {_digits(m.group(0)) for m in found}
    matcher = SequenceMatcher(
        a=[_digits(n) for n in wanted], b=[_digits(m.group(0)) for m in found], autojunk=False
    )
    edits: list[tuple[int, int, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for i, j in zip(range(i1, i2), range(j1, j2)):
                novel = _digits(found[j].group(0)) not in in_source
                missing = _digits(wanted[i]) not in in_translation
                if novel and missing:
                    edits.append((found[j].start(), found[j].end(), wanted[i]))
    for start, end, replacement in sorted(edits, reverse=True):
        translation = translation[:start] + replacement + translation[end:]
    return translation


def _repair_glued(source: str, translation: str) -> str:
    """Numbers the engine split or stuttered with a hyphen: "258B" written
    "25-8B" is joined back; "255.255.255.0" written "25-255.255.255.0" loses
    the stray piece. A hyphen between two numbers of the source ("10-20")
    is left alone."""
    in_source = numbers(source)

    def fix(match: re.Match) -> str:
        first, second = match.group(1), match.group(2)
        if _digits(first) in in_source:
            return match.group(0)
        joined = _digits(first) + _digits(second)
        if joined in in_source and _digits(second) not in in_source:
            return next(n for n in NUMBER.findall(source) if _digits(n) == joined)
        if _digits(second) in in_source:
            return second
        return match.group(0)

    return re.sub(r"(?<![\d.,])(\d+)[-–](\d+(?:[.,]\d+)*)", fix, translation)


# A word of the source returned as written: letters only, two or more.
WORD = re.compile(r"[^\W\d_]{2,}")
# All-capital words this short are abbreviations ("GND", "LED"), kept.
ABBREVIATION_LETTERS = 4
# Below this many words a text is a label, not a sentence.
SENTENCE_WORDS = 4


# Formulas are written in letters too ("PE(pos,2i) = sin(...)", "√dk").
FORMULA = re.compile(r"[=∑∏∫√∈≤≥]")


# Below this many words a text is a label or a cell, not running text.
PROSE_WORDS = 8


@dataclass(frozen=True)
class Vocabulary:
    """What a document tells about its words, for the checks of one text."""

    # Words of its language: those its running text writes in lowercase,
    # and those the language's dictionary has in lowercase ("blue", though
    # only written "Blue" in a table). Other words written capitalized are
    # names ("Vaswani", "Ethernet"); formulas are left out ("dmodel", "dk").
    ordinary: frozenset[str] = frozenset()
    # Names it shows on screens or keeps ("Netmask", "login"), lowercase: a
    # text that is one of them, whole, is a reference to it.
    names: frozenset[str] = frozenset()
    # Its words the target language has too, lowercase ("flash", "status",
    # "software"): kept, they are not left untranslated.
    shared: frozenset[str] = frozenset()
    # Every word it writes, lowercase: a translation may take one as it is
    # ("jumpers", "broadcast"), and it is no invented word.
    source_words: frozenset[str] = frozenset()


def vocabulary(units, dictionary=None, target=None) -> Vocabulary:
    """`dictionary`: the source language's (spelling.source_spelling);
    `target`: the target language's (spelling.spelling)."""
    from ..terms import kept_terms

    # Addresses are not words: "kokusai-denki.com.br" does not make
    # "KOKUSAI DENKI" ordinary words.
    prose = [
        PROTECTED.sub(" ", u.text) for u in units if len(u.text.split()) >= PROSE_WORDS and not FORMULA.search(u.text)
    ]
    names = [n for u in units for n in u.names] + kept_terms(units).kept()
    ordinary = {w for text in prose for w in WORD.findall(text) if w.islower()}
    written = {w.lower() for u in units if not FORMULA.search(u.text) for w in WORD.findall(PROTECTED.sub(" ", u.text))}
    if dictionary is not None:
        ordinary |= {w for w in written - ordinary if w.isalpha() and dictionary.has(w)}
    shared = {w for w in ordinary if target.has(w)} if target is not None else set()
    return Vocabulary(
        ordinary=frozenset(ordinary),
        shared=frozenset(shared),
        names=frozenset(n.lower() for n in names),
        source_words=frozenset(w.lower() for u in units for w in WORD.findall(u.text)),
    )


def unchanged_words(
    source: str, translation: str, kept: re.Pattern | None, known: Vocabulary | None = None
) -> list[str]:
    """Ordinary words of a short text the engine returned exactly as
    written ("Voltage:", "Ribbon Cable"): not kept terms (`kept` matches
    them), codes, abbreviations, dates or formulas. With what the document
    tells (`known`), only words it also writes as ordinary words count, and
    a text that is a name it shows ("Gateway") is a name. Longer texts are
    checked by the share of their words left in the source language."""
    if translation.strip() != source.strip() or FORMULA.search(source):
        return []
    if known and source.strip(" :.#").lower() in known.names:
        return []
    text = kept.sub(" ", source) if kept else source
    # Note marks are digits: "rate³" is "rate".
    text = CODE.sub(" ", _without_marks(text))
    text = re.sub(r"\b[A-Z][a-z]{2,3}\.?(?=\s*\d)", " ", text)  # "Aug 01": see localize_dates
    words = [w for w in WORD.findall(text) if not (w.isupper() and len(w) <= ABBREVIATION_LETTERS)]
    if known is None:
        return words
    # A name among the words makes the text a name ("Google Brain", "EVERTZ
    # 7700FC"): "Brain" is a word, said of the lab.
    if any(w.lower() not in known.ordinary for w in words):
        return []
    # Words of both languages ("64 MB Flash") are not left in English.
    return [w for w in words if w.lower() not in known.shared]


def _without_marks(text: str) -> str:
    """Superscript and subscript digits (note marks: "access³") as spaces."""
    return re.sub(r"[\u00b2\u00b3\u00b9\u2070-\u209f]", " ", text)


def leading_source_word(
    source: str, translation: str, kept: re.Pattern | None, starts: frozenset[str] = EN_PT_BR.source_only_starts
) -> str:
    """The first word of a sentence left in the source language before a
    translated rest ("The 7700FC controlador...", "Press W se você...")."""
    words, translated = source.split(), translation.split()
    if len(words) < SENTENCE_WORDS or not translated or translation.strip() == source.strip():
        return ""
    first = words[0]
    if translated[0] != first or first.lower() not in starts:
        return ""
    if kept and (match := kept.match(source)) and match.end() >= len(first):
        return ""
    return first
