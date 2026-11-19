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


class Spelling:
    def __init__(self, dictionary) -> None:
        self._dictionary = dictionary
        self._known: dict[str, bool] = {}

    def unknown(self, source: str, translation: str, source_words: frozenset[str] = frozenset()) -> list[str]:
        """Words of `translation` that are neither words of the target
        language nor taken from the source: the words of `source`, and
        `source_words`, the lowercase words of the whole document ("jumpers"
        kept from "jumper")."""
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

    def _exists(self, word: str) -> bool:
        if word not in self._known:
            # "MONITORADAS" and "Placa" are looked up as written in the dictionary.
            forms = dict.fromkeys([word, word.lower(), word.capitalize()])
            self._known[word] = any(self._dictionary.lookup(form) for form in forms)
        return self._known[word]


@cache
def spelling(languages: LanguagePair) -> Spelling | None:
    """The target language's dictionary, or None when it can't be had (no
    dictionary for the language, or no network on first use)."""
    if not languages.target_dictionary:
        return None
    from spylls.hunspell import Dictionary

    try:
        stem = _download(languages)
    except OSError as error:
        log.warning("No %s dictionary (%s); words are not checked", languages.target, error)
        return None
    return Spelling(Dictionary.from_files(str(stem)))


def _download(languages: LanguagePair) -> Path:
    """The dictionary's files in the cache, as `<dir>/<code>` (+ .dic, .aff)."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    folder = base / "doc-translator" / "dictionaries"
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / languages.target_code
    for url, sha256 in languages.target_dictionary:
        path = stem.with_suffix(Path(url).suffix)
        if path.is_file() and _sha256(path.read_bytes()) == sha256:
            continue
        log.info("Downloading the %s dictionary: %s", languages.target, url)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if _sha256(data) != sha256:
            raise OSError(f"unexpected content at {url}")
        path.write_bytes(data)
    return stem


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
