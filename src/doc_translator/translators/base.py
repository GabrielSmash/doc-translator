"""Contract shared by every translation backend."""

import re
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


class Translator(Protocol):
    def translate(self, texts: list[str]) -> list[str]:
        """Translate each text independently, preserving order."""
        ...


def protect(text: str, extra: re.Pattern | None = None) -> tuple[str, list[str]]:
    """Swap URLs, emails and `extra` matches for placeholders."""
    originals: list[str] = []

    def replace(match: re.Match) -> str:
        originals.append(match.group(0))
        return PLACEHOLDER.format(len(originals) - 1)

    text = PROTECTED.sub(replace, text)
    if extra is not None:
        text = extra.sub(replace, text)
    return text, originals


def restore(text: str, originals: list[str]) -> str:
    # Reverse order so "XURL1" is not clobbered when restoring "XURL10".
    for index in reversed(range(len(originals))):
        text = text.replace(PLACEHOLDER.format(index), originals[index])
    return text
