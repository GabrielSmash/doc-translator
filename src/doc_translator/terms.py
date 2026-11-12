"""Find the recurring terms of a document, deterministically.

The LLM decides how to translate each term, but which terms get a decision
must not depend on the model's mood: otherwise the glossary (and so the
terminology) changes from one run to the next.
"""

import re
from collections import Counter

from .languages import EN_PT_BR

WORD = re.compile(r"[A-Za-z][\w®/\-]*")
MIN_PROSE_WORDS = 8  # paragraphs shorter than this are headings, labels...
MIN_OCCURRENCES = 2
MAX_TERMS = 40
# Longer capitalized runs are table headers or lists glued together.
MAX_TERM_WORDS = 3


def recurring_terms(
    texts: list[str], stopwords: frozenset[str] = EN_PT_BR.source_stopwords
) -> dict[str, str]:
    """Multi-word Capitalized terms recurring in running text.

    Returns {term: an example paragraph using it}, most frequent first.
    """
    prose = [t for t in texts if _is_prose(t)]
    # Words seen in lowercase are ordinary words: capitalized only because
    # they start a sentence ("Once HyperTerminal is connected...").
    lowercase = {w for text in texts for w in WORD.findall(text) if w.islower()}
    counts: Counter[str] = Counter()
    canonical: dict[str, str] = {}
    for text in prose:
        for term in set(_capitalized_runs(text, lowercase, stopwords)):
            key = term.lower()
            counts[key] += 1
            canonical.setdefault(key, term)

    # Occurrences in lowercase ("the community strings") count too.
    for key in counts:
        counts[key] = sum(key in text.lower() for text in prose)

    terms = [k for k, n in counts.most_common() if n >= MIN_OCCURRENCES]
    terms = _drop_contained(terms, counts)[:MAX_TERMS]
    return {
        canonical[key]: next(t for t in prose if key in t.lower())
        for key in terms
    }


QUOTED = re.compile(r"[\"“”‘’'`]([^\"“”‘’'`]{2,60})[\"“”‘’'`]")


def quoted_terms(texts: list[str], terms: list[str]) -> set[str]:
    """Terms that appear quoted somewhere: labels of the software's screens,
    menus and buttons ('Add/Update Frame'), which stay as the software shows."""
    quoted = {m.group(1).strip().lower() for text in texts for m in QUOTED.finditer(text)}
    return {t for t in terms if t.lower() in quoted}


def _is_prose(text: str) -> bool:
    words = text.split()
    if len(words) < MIN_PROSE_WORDS:
        return False
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) < 0.5 * len(letters)


def _capitalized_runs(text: str, lowercase: set[str], stopwords: frozenset[str]) -> list[str]:
    """Maximal runs of 2+ capitalized words ("7700FC Frame Controller" -> "Frame Controller")."""
    runs, current = [], []
    for match in WORD.finditer(text):
        word = match.group(0).rstrip("-/")
        capitalized = word[0].isupper() and not word.isupper()
        contiguous = current and text[current[-1][2] : match.start()].strip() == ""
        entry = (word, match.start(), match.end())
        if capitalized and (not current or contiguous):
            current.append(entry)
            continue
        runs.append(current)
        current = [entry] if capitalized else []
    runs.append(current)

    terms = []
    for run in runs:
        if run and _starts_sentence(text, run[0][1]) and run[0][0].lower() in lowercase:
            run = run[1:]
        words = [w for w, _, _ in run]
        while words and words[0].lower() in stopwords:
            words.pop(0)
        while words and words[-1].lower() in stopwords:
            words.pop()
        if 2 <= len(words) <= MAX_TERM_WORDS:
            terms.append(" ".join(words))
    return terms


def _starts_sentence(text: str, position: int) -> bool:
    before = text[:position].rstrip()
    return not before or before[-1] in ".!?:"


def _drop_contained(terms: list[str], counts: Counter[str]) -> list[str]:
    """Drop a term that only ever appears inside a longer kept term."""
    kept = []
    for term in sorted(terms, key=len, reverse=True):
        longer = [k for k in kept if term in k]
        if longer and counts[term] <= max(counts[k] for k in longer):
            continue
        kept.append(term)
    return sorted(kept, key=lambda t: -counts[t])
