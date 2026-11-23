"""Contract shared by every translation backend."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Text the model must not touch. The placeholder format was chosen empirically:
# Opus-MT copies "XURL0" verbatim, while "{0}", "__0__" etc. get mangled.
PROTECTED = re.compile(
    r"(?:https?://|www\.)\S+[^\s.,;:!?)]"  # URLs
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # emails
    # Numbers with separators: IPs, versions, "(1/2)". Besides being
    # untranslatable, "(1/2)" makes Opus-MT hallucinate whole sentences.
    r"|\(\d+(?:[./:]\d+)+\)|\b\d+(?:[./:]\d+)+\b"
)
PLACEHOLDER = "XURL{}"


@dataclass
class Unit:
    """A paragraph to translate, plus its styled phrases (bold, links...).

    For each phrase the backend returns its counterpart in the translation,
    so the style can be carried over. Units arrive in reading order.
    """

    text: str
    segments: list[str] = field(default_factory=list)
    # The heading of the section the paragraph is in (context for the LLM).
    section: str = ""
    # Phrases set in italics: names of screens, menus and fields are, in
    # manuals ("the Network Setup menu"); see terms.interface_labels.
    labels: list[str] = field(default_factory=list)
    # Names the software shows on the document's screens (menu items, field
    # names) that this paragraph mentions; see terms.screen_names.
    names: list[str] = field(default_factory=list)
    # The whole table a cell belongs to, one row per line (context).
    table: str = ""
    # "balloon": a comic's speech, thought or caption, read from an image
    # (ADR 0008).
    kind: str = ""


@dataclass
class UnitTranslation:
    text: str
    segments: list[str]


# Quotation marks around a name the reader finds on screen ("the ‘Control’ tab").
QUOTES = "\"“”‘’'`"


@dataclass
class Glossary:
    """Terms to keep in the source language and terms with a fixed translation.

    `keep` terms are kept however they are written; `exact` ones only with
    their capitals ("Main Menu", while "the main menu" is ordinary text);
    `quoted` ones only between quotation marks ("press the ‘Enter’ key",
    while "Enter the address" is a sentence).
    """

    keep: list[str] = field(default_factory=list)
    terms: dict[str, str] = field(default_factory=dict)
    exact: list[str] = field(default_factory=list)
    quoted: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        """Read `{"keep": [...], "terms": {"source": "target"}}`."""
        data = json.loads(path.read_text())
        return cls(keep=list(data.get("keep", [])), terms=dict(data.get("terms", {})))

    def merged(self, other: "Glossary") -> "Glossary":
        """`other` wins on conflicts (e.g. the user's glossary)."""
        kept = {k.lower() for k in [*other.keep, *other.exact, *other.quoted]}
        terms = {k: v for k, v in self.terms.items() if k.lower() not in kept}
        terms.update(other.terms)
        keep = [k for k in dict.fromkeys(self.keep + other.keep) if k not in other.terms]
        return Glossary(
            keep=keep,
            terms=terms,
            exact=list(dict.fromkeys(self.exact + other.exact)),
            quoted=list(dict.fromkeys(self.quoted + other.quoted)),
        )

    def relevant(self, text: str, source_language: str) -> str:
        """Glossary lines for the terms that occur in `text` (for prompts)."""
        lines = [
            f'- "{term}": keep in {source_language}' for term in self.keep if _word(term, re.IGNORECASE).search(text)
        ]
        lines += [
            f'- "{term}": keep in {source_language} where written with these capitals'
            for term in self.exact
            if _word(term).search(text)
        ]
        lines += [
            f'- "{term}": keep in {source_language} where quoted'
            for term in self.quoted
            if _quoted(term).search(text)
        ]
        free = self._without_kept(text)
        lines += [
            f'- "{source}" -> "{target}"'
            for source, target in self.terms.items()
            if _word_or_plural(source).search(free)
        ]
        return "\n".join(lines)

    def violations(self, source: str, translation: str) -> list[str]:
        """Glossary terms used in `source` but not rendered as decided.

        Translations are compared by word stems so that inflections
        ("controladores de quadro" for "controlador de quadro") still match.
        """
        translated = _fold(translation)
        # Kept terms as whole words: "IP Address" is not in "IP addresses".
        missing = [t for t in self.keep if _word(t, re.IGNORECASE).search(source) and _fold(t) not in translated]
        # Compared as whole words ("Controle" holds "Control"), in Unicode's
        # compatible form ("Browse…" written "Browse...").
        plain = unicodedata.normalize("NFKC", translation)
        missing += [t for t in self.exact if _word(t).search(source) and not _word(t).search(plain)]
        missing += [t for t in self.quoted if _quoted(t).search(source) and not _word(t).search(plain)]
        # "network" -> "rede", but not in the kept name "Network Setup".
        free = self._without_kept(source)
        for term, target in self.terms.items():
            if _word_or_plural(term).search(free) and not all(
                _stem(word) in translated for word in _fold(target).split() if len(word) > 3
            ):
                missing.append(term)
        return missing

    def pattern(self) -> re.Pattern | None:
        """Regex matching every glossary term, longest first."""
        alternatives = [(w, re.escape(w)) for w in [*self.keep, *self.terms]]
        alternatives += [(w, f"(?-i:{re.escape(w)})") for w in self.exact]
        alternatives += [(w, f"(?<=[{QUOTES}])(?-i:{re.escape(w)})(?=[{QUOTES}])") for w in self.quoted]
        if not alternatives:
            return None
        ordered = sorted(alternatives, key=lambda a: len(a[0]), reverse=True)
        return re.compile(r"(?<!\w)(?:" + "|".join(a for _, a in ordered) + r")(?!\w)", re.IGNORECASE)

    def replacement(self, term: str) -> str:
        """What a protected glossary term becomes in the translation."""
        for source, target in self.terms.items():
            if source.lower() == term.lower():
                return target
        return term

    def kept(self) -> list[str]:
        return [*self.keep, *self.exact, *self.quoted]

    def _without_kept(self, text: str) -> str:
        kept = Glossary(keep=self.keep, exact=self.exact, quoted=self.quoted).pattern()
        return kept.sub(" ", text) if kept else text


def _word(term: str, flags: int = 0) -> re.Pattern:
    term = unicodedata.normalize("NFKC", term)
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)


def _word_or_plural(term: str) -> re.Pattern:
    """`term` as whole words, also in the plural: "port" is in "ports", not
    in "support"."""
    return re.compile(rf"(?<!\w){re.escape(term)}(?:s|es)?(?!\w)", re.IGNORECASE)


def _quoted(term: str) -> re.Pattern:
    return re.compile(f"[{QUOTES}]{re.escape(term)}[{QUOTES}]")


def _fold(text: str) -> str:
    """Lowercase without accents."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem(word: str) -> str:
    return word[: max(4, len(word) - 3)]


class Translator(Protocol):
    # Identifies everything that changes the output (engine, model, target
    # language, glossary), so cached translations are reused only when valid.
    cache_id: str

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]: ...


def translate_independently(translate, units: list[Unit]) -> list[UnitTranslation]:
    """For sentence-level models: translate texts and segments on their own.

    `translate(list[str]) -> list[str]` gets every text of the document in a
    single call, so the backend can batch and deduplicate.
    """
    texts = [text for unit in units for text in (unit.text, *unit.segments)]
    wordy = [text for text in texts if has_words(text)]
    translated = dict(zip(wordy, translate(wordy)))
    translations = iter(translated.get(text, text) for text in texts)
    results = []
    for unit in units:
        text = next(translations)
        segments = [next(translations) for _ in unit.segments]
        results.append(UnitTranslation(text, segments))
    return results


# Capital words a value can carry: its units and abbreviations.
MAX_UNIT_WORDS = 2


def has_words(text: str) -> bool:
    """Whether `text` has anything to translate.

    Text without letters (amounts, dates, codes) has not, and an engine
    would reformat it ("1,020" became "1.020" in some table cells and not in
    others, "$700" became "$700."). Neither has a value with its units
    ("1 kΩ", "43~63 Hz", "68 BTU/h", "BNC-F / 75 Ω"): the LLM wrote "1 k"
    and "1 kcorlib" for "1 kΩ". Words are what is left besides tokens with
    digits: one of 3 letters or more not in capitals, or a lowercase one of
    2 ("1 to 13"), or more capital words than a value carries ("UPGRADING
    MODULE FIRMWARE THROUGH THE 7700FC FRAME CONTROLLER").
    """
    if not any(c.isalpha() for c in text):
        return False
    if not any(c.isdigit() for c in text):
        return True
    words = [
        word
        for token in text.split()
        if not any(c.isdigit() for c in token)
        for word in re.findall(r"[^\W\d_]+", token)
    ]
    if any((len(word) >= 3 and not word.isupper()) or (len(word) == 2 and word.islower()) for word in words):
        return True
    # Words in capitals are units and abbreviations beside a value ("3V3
    # LVTTL", "BNC-F / 75 Ω"); several of them are a heading ("UPGRADING
    # MODULE FIRMWARE THROUGH THE 7700FC FRAME CONTROLLER").
    return sum(len(word) >= 3 for word in words) > MAX_UNIT_WORDS


def protect(
    text: str, extra: re.Pattern | None = None, glossary: Glossary | None = None
) -> tuple[str, list[str]]:
    """Swap URLs, emails, `extra` matches and glossary terms for placeholders.

    Returns the text and what each placeholder must be restored to: the
    original text, or the fixed translation of a glossary term.
    """
    originals: list[str] = []

    def replace(match: re.Match) -> str:
        originals.append(match.group(0))
        return PLACEHOLDER.format(len(originals) - 1)

    text = PROTECTED.sub(replace, text)
    if extra is not None:
        text = extra.sub(replace, text)
    if glossary is not None and (terms := glossary.pattern()):

        def replace_term(match: re.Match) -> str:
            originals.append(glossary.replacement(match.group(0)))
            return PLACEHOLDER.format(len(originals) - 1)

        text = terms.sub(replace_term, text)
    return text, originals


def restore(text: str, originals: list[str]) -> str:
    # Reverse order so "XURL1" is not clobbered when restoring "XURL10".
    for index in reversed(range(len(originals))):
        text = text.replace(PLACEHOLDER.format(index), originals[index])
    return text
