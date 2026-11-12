"""Translations kept on disk.

Running a document again (after a layout change, or after an interrupted
run) reuses what was already translated instead of paying for it again;
with the LLM engine that is minutes per document.

Entries are keyed by the translator's `cache_id` (engine, model, target
language, glossary, program version) and the source text, so a change to
any of them translates again instead of reusing a stale answer.
"""

import hashlib
import json
import logging
import os
import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .base import Translator, Unit, UnitTranslation

log = logging.getLogger(__name__)

try:
    # A new version may translate differently (new checks, new prompts).
    VERSION = version("doc-translator")
except PackageNotFoundError:
    VERSION = "dev"


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
        name = f"{VERSION}|{translator.cache_id}"
        self.store = cache.namespace("units|" + name)
        # Engines that translate for minutes save their progress as they go,
        # so an interrupted run resumes where it stopped.
        if hasattr(translator, "keep_progress"):
            translator.keep_progress(cache.namespace("texts|" + name))

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
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
