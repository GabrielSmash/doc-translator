"""Contract shared by every translation backend."""

import re
from typing import Protocol

# Text the model must not touch. The placeholder format was chosen empirically:
# Opus-MT copies "XURL0" verbatim, while "{0}", "__0__" etc. get mangled.
PROTECTED = re.compile(
    r"(?:https?://|www\.)\S+[^\s.,;:!?)]"  # URLs
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # emails
)
PLACEHOLDER = "XURL{}"


class Translator(Protocol):
    def translate(self, texts: list[str]) -> list[str]:
        """Translate each text independently, preserving order."""
        ...


def protect(text: str) -> tuple[str, list[str]]:
    originals: list[str] = []

    def replace(match: re.Match) -> str:
        originals.append(match.group(0))
        return PLACEHOLDER.format(len(originals) - 1)

    return PROTECTED.sub(replace, text), originals


def restore(text: str, originals: list[str]) -> str:
    # Reverse order so "XURL1" is not clobbered when restoring "XURL10".
    for index in reversed(range(len(originals))):
        text = text.replace(PLACEHOLDER.format(index), originals[index])
    return text
