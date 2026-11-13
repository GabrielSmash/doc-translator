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


MAX_LABEL_WORDS = 4


def interface_labels(units) -> list[str]:
    """Italic phrases naming parts of the software ("Network Setup",
    "IP Address"): they stay as the screen shows them.

    Manuals set screen, menu and field names in italics; so are emphasis
    and variables, which are lowercase or single letters. A label starts
    with a capital, has a few words of letters and recurs in the document.
    """
    # One italic run may list several names: "IP Address, Netmask, Gateway,".
    names = (part.strip(" .:;()") for u in units for l in u.labels for part in re.split(r"[,;]", l))
    labels = []
    for label in dict.fromkeys(n for n in names if n):
        words = label.split()
        if not (1 <= len(words) <= MAX_LABEL_WORDS and label[0].isupper() and len(label) > 2):
            continue
        if not all(any(c.isalpha() for c in w) for w in words):
            continue
        # Written always the same way: "IP Address" is a field, but in "the
        # IP address of the card" the same words are ordinary text.
        found = re.findall(re.escape(label), "\n".join(u.text for u in units), re.IGNORECASE)
        if len(found) >= MIN_OCCURRENCES and all(f == label for f in found):
            labels.append(label)
    return labels


def kept_terms(units, languages=EN_PT_BR) -> list[str]:
    """What both engines keep as written in this document: its interface
    labels, and the abbreviations of the language pair it uses."""
    text = "\n".join(u.text for u in units)
    abbreviations = [a for a in sorted(languages.kept_abbreviations) if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text)]
    return interface_labels(units) + abbreviations


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
