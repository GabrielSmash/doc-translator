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
from difflib import SequenceMatcher

from ..languages import EN_PT_BR

NUMBER = re.compile(r"\d+(?:[.,/:]\d+)*")
# Signs that carry a value (units, legal marks): a translation keeps them.
SYMBOLS = frozenset("Ω®™©°µ±×≤≥≈∞")
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


def lost_symbols(source: str, translation: str) -> set[str]:
    return {c for c in source if c in SYMBOLS} - set(translation)


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
    for number, symbol in NUMBER_SYMBOL.findall(source):
        if symbol in text:
            continue
        after_number = rf"(?<![\d.,]){re.escape(number)}(?![\d.,]*\d)"
        text = re.sub(after_number, lambda m, sign=symbol: m.group(0) + sign, text, count=1)
    return re.sub(r"  +", " ", text).strip() if text != translation else translation


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
