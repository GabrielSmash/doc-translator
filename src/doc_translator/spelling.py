"""Words of a translation that the target language does not have (ADR 0007).

A small model invents words ("RETIFOS", "MONITADAS", "reinsertado"); no
other check sees them. They are looked up in a Hunspell dictionary of the
target language, read by spylls (pure Python). The dictionary is downloaded
on first use, checked against its hash, and kept in the user's cache.
"""

import hashlib
import logging
import os
import re
import urllib.request
from functools import cache
from pathlib import Path

from .languages import LanguagePair

log = logging.getLogger(__name__)

# Words of a translation: letters, joined by hyphens ("sub-rede").
WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
MIN_LETTERS = 3
# All-capital words this short are abbreviations ("LED", "GND").
ABBREVIATION_LETTERS = 4


def _without_marks(text: str) -> str:
    return re.sub(r"[\u00b2\u00b3\u00b9\u2070-\u209f]", " ", text)


# Letters an invented word and a source word must start with alike to be
# the same word ("promp" of "prompção" and "prompt").
MIN_STEM = 5


def _common_start(a: str, b: str) -> int:
    length = 0
    for x, y in zip(a, b):
        if x != y:
            break
        length += 1
    return length


class Spelling:
    def __init__(self, dictionary) -> None:
        self._dictionary = dictionary
        self._known: dict[str, bool] = {}

    def unknown(self, source: str, translation: str, source_words: frozenset[str] = frozenset()) -> list[str]:
        """Words of `translation` that are neither words of the target
        language nor taken from the source: the words of `source`, and
        `source_words`, the lowercase words of the whole document ("jumpers"
        kept from "jumper")."""
        # Note marks are digits, not letters: "remoto³" is "remoto".
        source, translation = _without_marks(source), _without_marks(translation)
        written = {w.lower() for w in WORD.findall(source)} | source_words
        written |= {part for w in written for part in w.split("-")}
        found = []
        for match in WORD.finditer(translation):
            word = match.group(0)
            lower = word.lower()
            if len(word) < MIN_LETTERS or lower in written or lower.removesuffix("s") in written:
                continue
            if word.isupper() and len(word) <= ABBREVIATION_LETTERS:
                continue
            # Capitalized inside a text: a name ("François", "Berkeley").
            if word[0].isupper() and not word.isupper() and translation[: match.start()].strip():
                continue
            # A compound is wrong only by a wrong part: "auto-atenção" is
            # made of words, "short-circuite" is not.
            parts = [p for p in word.split("-") if len(p) >= MIN_LETTERS]
            known = [p.lower() in written or self._exists(p) for p in parts or [word]]
            if not self._exists(word) and not all(known):
                found.append(word)
        return list(dict.fromkeys(found))

    def has(self, word: str) -> bool:
        """`word` is in the dictionary as written: "blue" is, "ethernet" is
        not (the dictionary has "Ethernet")."""
        return bool(self._dictionary.lookup(word))

    def source_words_for(self, source: str, translation: str, invented: list[str]) -> str:
        """`translation` with each invented word that is a source word made
        Portuguese-looking ("prompção" from "prompt", "defaulta" from
        "default") written as the source word. The model writes these
        again even when told they do not exist; the term in the source
        language is a real word, and technical Portuguese uses many of
        them ("prompt", "default")."""
        words = {w.lower() for w in WORD.findall(source) if len(w) >= MIN_STEM}
        for word in invented:
            lower = word.lower()
            matches = [w for w in words if _common_start(lower, w) >= max(MIN_STEM, round(0.6 * len(w)))]
            if not matches:
                continue
            original = max(matches, key=lambda w: _common_start(lower, w))
            if word[0].isupper():
                original = original.upper() if word.isupper() else original.capitalize()
            translation = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", original, translation)
        return translation

    def _exists(self, word: str) -> bool:
        if word not in self._known:
            # "MONITORADAS" and "Placa" are looked up as written in the dictionary.
            forms = dict.fromkeys([word, word.lower(), word.capitalize()])
            self._known[word] = any(self._dictionary.lookup(form) for form in forms)
        return self._known[word]


def spelling(languages: LanguagePair) -> Spelling | None:
    """The target language's dictionary, or None when it can't be had (no
    dictionary for the language, or no network on first use)."""
    return _dictionary(languages.target, languages.target_code, languages.target_dictionary)


def source_spelling(languages: LanguagePair) -> Spelling | None:
    """The source language's dictionary, or None (see `spelling`)."""
    return _dictionary(languages.source, languages.source_code, languages.source_dictionary)


def source_word_ranks(languages: LanguagePair) -> dict[str, int] | None:
    """Each word of the source language and its rank by use (0: the most
    used), or None when the list can't be had."""
    return _word_ranks(languages.source, languages.source_code, languages.source_word_ranks)


@cache
def _word_ranks(language: str, code: str, files: tuple[tuple[str, str], ...]) -> dict[str, int] | None:
    if not files:
        return None
    import gzip

    try:
        stem = _download(f"{language} word list", code, files, "word-ranks")
    except OSError as error:
        log.warning("No %s word list (%s)", language, error)
        return None
    path = stem.with_suffix(Path(files[0][0]).suffix)
    words = gzip.decompress(path.read_bytes()).decode().split()
    ranks: dict[str, int] = {}
    for rank, word in enumerate(words):
        ranks.setdefault(word, rank)
    return ranks


@cache
def _dictionary(language: str, code: str, files: tuple[tuple[str, str], ...]) -> Spelling | None:
    if not files:
        return None
    from spylls.hunspell import Dictionary

    try:
        stem = _download(f"{language} dictionary", code, files)
    except OSError as error:
        log.warning("No %s dictionary (%s); words are not checked", language, error)
        return None
    return Spelling(Dictionary.from_files(str(stem)))


def _download(label: str, code: str, files: tuple[tuple[str, str], ...], kind: str = "dictionaries") -> Path:
    """The files in the cache, as `<dir>/<code>` (+ .dic, .aff)."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    folder = base / "doc-translator" / kind
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / code
    for url, sha256 in files:
        path = stem.with_suffix(Path(url).suffix)
        if path.is_file() and _sha256(path.read_bytes()) == sha256:
            continue
        log.info("Downloading the %s: %s", label, url)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if _sha256(data) != sha256:
            raise OSError(f"unexpected content at {url}")
        path.write_bytes(data)
    return stem


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
