"""Translations kept on disk.

Running a document again (after a layout change, or after an interrupted
run) reuses what was already translated instead of paying for it again;
with the LLM engine that is minutes per document.

Entries are keyed by the translator's `cache_id` (engine, model, target
language, glossary), the code that produces translations, the terms the
document keeps as written, and the source text, so a change to any of them
translates again instead of reusing a stale answer. Changing the layout or
drawing code keeps the cache: that is what it is for.
"""

import hashlib
import json
import logging
import os
import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..terms import kept_terms
from .base import Translator, Unit, UnitTranslation

log = logging.getLogger(__name__)

try:
    VERSION = version("doc-translator")
except PackageNotFoundError:
    VERSION = "dev"

# Code whose changes change translations: the engines, their checks and
# prompts, the terms kept as written. The package version alone missed
# every change made between releases, and stale translations came back
# (numbers reformatted, cells as sentences: 443 paragraphs reduced on the
# IRS form instead of 89).
_PACKAGE = Path(__file__).resolve().parent.parent
TRANSLATION_CODE = sorted(
    [*(_PACKAGE / "translators").glob("*.py"), _PACKAGE / "terms.py", _PACKAGE / "languages.py"]
)


def code_fingerprint() -> str:
    digest = hashlib.sha256(VERSION.encode())
    for path in TRANSLATION_CODE:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def default_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "doc-translator" / "translations.sqlite"


class TranslationCache:
    """Key-value store in SQLite: safe to write as work progresses, and an
    interrupted run leaves every committed entry readable."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT)")

    def namespace(self, name: str) -> "CacheNamespace":
        return CacheNamespace(self, name)

    def get_many(self, keys: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        unique = list(dict.fromkeys(keys))
        for start in range(0, len(unique), 500):  # SQLite caps bound parameters
            chunk = unique[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._db.execute(f"SELECT key, value FROM entries WHERE key IN ({marks})", chunk)
            found.update(rows)
        return found

    def put_many(self, entries: dict[str, str]) -> None:
        with self._db:
            self._db.executemany("INSERT OR REPLACE INTO entries VALUES (?, ?)", entries.items())

    def close(self) -> None:
        self._db.close()


class CacheNamespace:
    """The entries of one translator configuration, addressed by text."""

    def __init__(self, cache: TranslationCache, name: str) -> None:
        self._cache = cache
        self._prefix = hashlib.sha256(name.encode()).hexdigest()[:16]

    def _key(self, text: str) -> str:
        return self._prefix + hashlib.sha256(text.encode()).hexdigest()

    def get_many(self, texts: list[str]) -> dict[str, str]:
        keys = {self._key(t): t for t in texts}
        return {keys[k]: v for k, v in self._cache.get_many(list(keys)).items()}

    def put_many(self, entries: dict[str, str]) -> None:
        self._cache.put_many({self._key(t): v for t, v in entries.items()})


class CachedTranslator:
    """Answers the units already translated from the cache; sends only the
    others to `translator`, and stores what it returns."""

    def __init__(self, translator: Translator, cache: TranslationCache) -> None:
        self.translator = translator
        self.cache = cache
        self.name = f"{code_fingerprint()}|{translator.cache_id}"

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        # Terms the document keeps as written (its italic labels, "N/A")
        # change how every paragraph using them is translated.
        languages = getattr(self.translator, "languages", None)
        kept = kept_terms(units, languages) if languages else kept_terms(units)
        name = f"{self.name}|{json.dumps(kept, ensure_ascii=False)}"
        self.store = self.cache.namespace("units|" + name)
        # Engines that translate for minutes save their progress as they go,
        # so an interrupted run resumes where it stopped.
        if hasattr(self.translator, "keep_progress"):
            self.translator.keep_progress(self.cache.namespace("texts|" + name))
        # Labels decide which words stay untranslated, so they are part of the key.
        keys = [json.dumps([u.text, u.segments, u.labels], ensure_ascii=False) for u in units]
        found = self.store.get_many(keys)
        missing = {k: u for k, u in zip(keys, units) if k not in found}
        if found:
            log.info("Cache: %d of %d distinct paragraphs already translated", len(found), len(found) + len(missing))
        if missing:
            translations = self.translator.translate_units(list(missing.values()))
            new = {
                key: json.dumps([t.text, t.segments], ensure_ascii=False)
                for key, t in zip(missing, translations)
            }
            self.store.put_many(new)
            found |= new
        results = []
        for key in keys:
            text, segments = json.loads(found[key])
            results.append(UnitTranslation(text, segments))
        return results


def glossary_id(glossary) -> str:
    if glossary is None:
        return "-"
    data = json.dumps([sorted(glossary.keep), sorted(glossary.terms.items())], ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()[:16]
