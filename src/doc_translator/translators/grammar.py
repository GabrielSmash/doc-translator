"""Grammar errors of a translation, found by LanguageTool (ADR 0008).

The local model gets agreement wrong ("A SUPER-HERÓI", "SUA DESEJO", "MEUS
BRAÇOS NÃO É") and, asked to review its own text, does not see it.
LanguageTool's rules do. Its server (LGPL 2.1, Java) is downloaded once,
pinned and checked like the dictionaries, and run on this machine for the
length of a run. Only its grammar rules are used: its spelling and
capitalization rules mistake the comic's names and capitals for errors.
"""

import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ..languages import LanguagePair

log = logging.getLogger(__name__)

# Rule categories kept: agreement, verb forms, words in the wrong place.
CATEGORIES = frozenset({"GRAMMAR", "SYNTAX", "CONFUSED_WORDS", "SEMANTICS", "DOC_TRANSLATOR"})
STARTUP_SECONDS = 60


@dataclass
class GrammarError:
    excerpt: str  # the words found wrong, as in the text
    message: str
    replacements: list[str]
    offset: int
    length: int

    def describe(self) -> str:
        suggestion = f" (maybe {self.replacements[0]!r})" if self.replacements else ""
        return f"{self.excerpt!r}: {self.message}{suggestion}"


class Grammar:
    """A LanguageTool server for one language, started on first use."""

    def __init__(self, languages: LanguagePair) -> None:
        self.code, self.url, self.sha256 = languages.grammar_checker
        self._process: subprocess.Popen | None = None
        self._port = 0

    def errors(self, text: str) -> list[GrammarError]:
        """The grammar errors of `text` (offsets in `text`)."""
        if not text.strip():
            return []
        checked = sentence_case(text)
        data = urllib.parse.urlencode({"language": self.code, "text": checked}).encode()
        with urllib.request.urlopen(f"http://127.0.0.1:{self._server()}/v2/check", data, timeout=60) as response:
            matches = json.loads(response.read())["matches"]
        return [
            GrammarError(
                text[m["offset"] : m["offset"] + m["length"]],
                m["message"],
                [r["value"] for r in m["replacements"][:3]],
                m["offset"],
                m["length"],
            )
            for m in matches
            if m["rule"]["category"]["id"] in CATEGORIES
        ]

    def spelled(self, word: str) -> str:
        """LanguageTool's first spelling suggestion for `word`, or ""."""
        data = urllib.parse.urlencode({"language": self.code, "text": word.lower()}).encode()
        with urllib.request.urlopen(f"http://127.0.0.1:{self._server()}/v2/check", data, timeout=60) as response:
            matches = json.loads(response.read())["matches"]
        for match in matches:
            if match["rule"]["category"]["id"] == "TYPOS" and match["replacements"]:
                return match["replacements"][0]["value"]
        return ""

    def corrected(self, text: str) -> str:
        """`text` with each error replaced by LanguageTool's first suggestion,
        in the text's case."""
        result = text
        for error in sorted(self.errors(text), key=lambda e: -e.offset):
            if error.replacements:
                fix = error.replacements[0]
                fix = fix.upper() if error.excerpt.isupper() else fix
                result = result[: error.offset] + fix + result[error.offset + error.length :]
        return result

    def _server(self) -> int:
        if self._process is not None and self._process.poll() is None:
            return self._port
        jar = _install(self.url, self.sha256)
        _add_rules(jar.parent, self.code)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self._port = probe.getsockname()[1]
        self._process = subprocess.Popen(
            ["java", "-cp", str(jar), "org.languagetool.server.HTTPServer", "--port", str(self._port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(self.close)
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self._port}/v2/languages", timeout=2).close()
                return self._port
            except OSError:
                time.sleep(0.5)
        self.close()
        raise OSError("LanguageTool did not start")

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
        self._process = None


@cache
def grammar(languages: LanguagePair) -> Grammar | None:
    """The checker of `languages`' target, or None (no Java, or no checker
    for the language): translations are then not checked for grammar."""
    if not languages.grammar_checker:
        return None
    if shutil.which("java") is None:
        log.warning("Java not found: translations are not checked for grammar (LanguageTool needs it)")
        return None
    return Grammar(languages)


def sentence_case(text: str) -> str:
    """`text` in sentence case when it is in capitals, as LanguageTool reads
    best; the same length, so offsets hold."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return text
    lowered = text.lower()
    return re.sub(r"(^|[.!?…]\s*)(\w)", lambda m: m.group(1) + m.group(2).upper(), lowered)


def _install(url: str, sha256: str) -> Path:
    """The server's jar, downloaded and unpacked into the cache once."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "doc-translator" / "languagetool"
    base.mkdir(parents=True, exist_ok=True)
    archive = base / Path(urllib.parse.urlparse(url).path).name
    folder = base / archive.stem
    jar = folder / "languagetool-server.jar"
    if jar.is_file():
        return jar
    if not archive.is_file() or _file_sha256(archive) != sha256:
        log.info("Downloading the grammar checker (LanguageTool, ~250 MB, once): %s", url)
        partial = archive.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        if _file_sha256(partial) != sha256:
            partial.unlink()
            raise OSError(f"unexpected content at {url}")
        partial.replace(archive)
    with zipfile.ZipFile(archive) as unpacked:
        unpacked.extractall(base)
    return jar


# Rules of our own, for what LanguageTool's miss (grammar_pt.xml).
RULES = {"pt": "grammar_pt.xml"}


def _add_rules(folder: Path, code: str) -> None:
    """Copy our rules for the language into LanguageTool's, as its custom
    rules (it reads grammar_custom.xml beside its own)."""
    language = code.split("-")[0]
    if name := RULES.get(language):
        target = folder / "org" / "languagetool" / "rules" / language / "grammar_custom.xml"
        target.write_bytes((Path(__file__).parent / name).read_bytes())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
