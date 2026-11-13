"""The form of short texts: what a translation must not change around the words.

Table cells and labels are a word or two, and models translate them as
sentences: "no" became "Não." (capital, final period), "Transmit –" became
"Transmita..." (the sign lost). Both engines get the same treatment, so
the rule holds whatever the engine:

- Signs standing apart at the ends ("Transmit –", "Pin #") are kept out of
  the translation and put back.
- A short text keeps its own final punctuation and the case of its first
  letter.
"""

import re
from dataclasses import replace

from .base import Unit, UnitTranslation

# Tokens without letters or digits at the ends, apart from the words.
LEADING = re.compile(r"^(\s*(?:[^\w\s]+\s+)+)(?=\w)")
TRAILING = re.compile(r"(?<=\w)((?:\s+[^\w\s]+)+\s*)$")
# Up to this many words, a text is a label, not a sentence.
SHORT_WORDS = 4
FINAL_PUNCTUATION = ".!?;:"


def strip_affixes(unit: Unit) -> tuple[Unit, str, str]:
    """The unit without signs standing apart at its ends, and those signs."""
    leading = LEADING.match(unit.text)
    trailing = TRAILING.search(unit.text)
    lead = leading.group(1) if leading else ""
    trail = trailing.group(1) if trailing else ""
    core = unit.text[len(lead) : len(unit.text) - len(trail)]
    if not (lead or trail) or not all(segment in core for segment in unit.segments):
        return unit, "", ""
    return replace(unit, text=core), lead, trail


def restore_form(source: str, translation: UnitTranslation, lead: str, trail: str) -> UnitTranslation:
    """Put the signs back and give a short text its original form.

    `source`: the text that was translated (without the signs)."""
    text = translation.text.strip()
    if len(source.split()) <= SHORT_WORDS:
        while text and text[-1] in FINAL_PUNCTUATION + "…" and not source.rstrip().endswith(text[-1]):
            text = text[:-1].rstrip()
        first = source.lstrip()[:1]
        if not text:
            text = translation.text.strip()
        # Only a plain word: "iPhone", "IP" keep their case.
        if first.islower() and text[:1].isupper() and text[1:2].islower():
            text = text[0].lower() + text[1:]
    return UnitTranslation(lead + text + trail, translation.segments)
