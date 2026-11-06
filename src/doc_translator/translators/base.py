"""Contract shared by every translation backend."""

import json
import re
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


@dataclass
class UnitTranslation:
    text: str
    segments: list[str]


@dataclass
class Glossary:
    """Terms to keep in English and terms with a fixed translation."""

    keep: list[str] = field(default_factory=list)
    terms: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        """Read `{"keep": [...], "terms": {"source": "target"}}`."""
        data = json.loads(path.read_text())
        return cls(keep=list(data.get("keep", [])), terms=dict(data.get("terms", {})))

    def merged(self, other: "Glossary") -> "Glossary":
        """`other` wins on conflicts (e.g. the user's glossary)."""
        terms = {k: v for k, v in self.terms.items() if k not in other.keep}
        terms.update(other.terms)
        keep = [k for k in dict.fromkeys(self.keep + other.keep) if k not in other.terms]
        return Glossary(keep=keep, terms=terms)

    def relevant(self, text: str) -> str:
        """Glossary lines for the terms that occur in `text` (for prompts)."""
        lower = text.lower()
        lines = [f'- "{term}": keep in English' for term in self.keep if term.lower() in lower]
        lines += [
            f'- "{source}" -> "{target}"'
            for source, target in self.terms.items()
            if source.lower() in lower
        ]
        return "\n".join(lines)

    def pattern(self) -> re.Pattern | None:
        """Regex matching every glossary term, longest first."""
        words = sorted([*self.keep, *self.terms], key=len, reverse=True)
        if not words:
            return None
        return re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE
        )

    def replacement(self, term: str) -> str:
        """What a protected glossary term becomes in the translation."""
        for source, target in self.terms.items():
            if source.lower() == term.lower():
                return target
        return term


class Translator(Protocol):
    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]: ...


def translate_independently(translate, units: list[Unit]) -> list[UnitTranslation]:
    """For sentence-level models: translate texts and segments on their own.

    `translate(list[str]) -> list[str]` gets every text of the document in a
    single call, so the backend can batch and deduplicate.
    """
    texts = [text for unit in units for text in (unit.text, *unit.segments)]
    translations = iter(translate(texts))
    results = []
    for unit in units:
        text = next(translations)
        segments = [next(translations) for _ in unit.segments]
        results.append(UnitTranslation(text, segments))
    return results


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
