"""Checks shared by every translation engine."""

import re

NUMBER = re.compile(r"\d+(?:[.,/:]\d+)*")


def numbers(text: str) -> set[str]:
    """Digits of each number, ignoring separators: "2.5" and "2,5" (pt-BR
    decimal comma), "1,000" and "1.000" are the same number."""
    return {re.sub(r"[.,]", "", n) for n in NUMBER.findall(text)}


def lost_numbers(source: str, translation: str) -> set[str]:
    """Numbers of `source` missing from `translation`."""
    return numbers(source) - numbers(translation)
