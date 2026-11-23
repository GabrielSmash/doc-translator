"""Find the recurring terms of a document, deterministically.

The LLM decides how to translate each term, but which terms get a decision
must not depend on the model's mood: otherwise the glossary (and so the
terminology) changes from one run to the next.
"""

import re
from collections import Counter

from .languages import EN_PT_BR
from .translators.base import QUOTES, Glossary

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


# Single domain words ("card", "jumper", "female"): ADR 0006. Decided once,
# as a word is otherwise rendered anew at each use, often in its everyday
# sense ("card" as "cartão", "End" as "Fim").
MAX_DOMAIN_WORDS = 40
MIN_DOMAIN_SPREAD = 3  # texts using the word
DOMAIN_EXAMPLES = 3  # two decide; a third breaks a tie
MAX_CELL_WORDS = 4


def domain_words(units, languages=EN_PT_BR, exclude=()) -> dict[str, list[str]]:
    """Single words naming the document's things, found by where it uses
    them, not by their case: after a determiner in running text ("the card",
    "a jumper"), or in short texts (cells, labels: "9 pin D Female").

    Only words the document also writes in lowercase in its running text:
    words it only writes capitalized are names ("Ethernet"). Returns {word:
    short passages using it}, used in most texts first.
    """
    texts = [u.text for u in units]
    prose = [t for t in texts if _is_prose(t)]
    lowercase = {w for t in prose for w in WORD.findall(t) if w.islower()}
    stopwords = languages.source_stopwords
    skipped = {e.lower() for e in exclude}
    determiner = "|".join(languages.source_determiners)
    # The determiner, an optional code ("the 7700FC card"), and up to two
    # words: "the card", "the card edge".
    noun_place = re.compile(
        rf"\b(?i:{determiner})\s+(?:[\w\-]*\d[\w\-]*\s+)?([a-z][a-z\-]{{2,}})(?:\s([a-z][a-z\-]{{2,}}))?"
    )
    spread: Counter[str] = Counter()
    # Texts where a word names the thing ("into the frame."), not describes
    # another ("the frame controller"): the examples that show its sense.
    heads: dict[str, list[tuple[int, str]]] = {}
    for text in texts:
        found = set()
        if text in prose:
            for match in noun_place.finditer(text):
                words = [w for w in match.groups() if w]
                # Participles and gerunds describe ("the selected", "the following").
                for index, word in enumerate(words):
                    if word in stopwords or word.endswith(("ed", "ing")):
                        words = words[:index]
                        break
                found.update(words)
                if words:
                    # "the card" shows the word better than "the white card".
                    heads.setdefault(words[-1], []).append((len(words), _window(text, match.start())))
        elif len(text.split()) <= MAX_CELL_WORDS and not FORMULA_SIGNS.search(text):
            found = {w.lower() for w in WORD.findall(text)} - stopwords
            for word in found:
                heads.setdefault(word, [])
        spread.update(w for w in found if w in lowercase and w not in skipped and len(w) > 2)

    # "the same pins", "the front panel": a word only seen before another
    # describes it.
    words = [w for w, n in spread.most_common() if n >= MIN_DOMAIN_SPREAD and w in heads]
    examples = {}
    for word in words[:MAX_DOMAIN_WORDS]:
        lowercase_use = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")
        any_use = re.compile(lowercase_use.pattern, re.IGNORECASE)
        # Then sentences writing the word in lowercase; a heading ("Card
        # Edge Serial Port") shows less.
        uses = sorted((t for t in texts if any_use.search(t)), key=lambda t: not lowercase_use.search(t))
        uses = [_window(t, (lowercase_use.search(t) or any_use.search(t)).start()) for t in uses]
        named = [passage for _, passage in sorted(heads[word], key=lambda h: h[0])]
        examples[word] = list(dict.fromkeys(named + uses))[:DOMAIN_EXAMPLES]
    return examples


FORMULA_SIGNS = re.compile(r"[<>=]")
EXAMPLE_CHARS = 160


def _window(text: str, position: int) -> str:
    """The sentence of `text` at `position`, shown as an example of use."""
    start = max(text.rfind(end, 0, position) for end in (". ", "? ", "! ", ": ")) + 1
    stops = [i for end in (". ", "? ", "! ") if (i := text.find(end, position)) >= 0]
    sentence = " ".join(text[start : min(stops, default=len(text)) + 1].split())
    return sentence[:EXAMPLE_CHARS]


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


def kept_terms(units, languages=EN_PT_BR) -> Glossary:
    """What both engines keep as written in this document: its interface
    labels, the names its screens show, the names its instructions quote,
    and the abbreviations of the language pair it uses."""
    text = "\n".join(u.text for u in units)
    abbreviations = [a for a in sorted(languages.kept_abbreviations) if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text)]
    labels = interface_labels(units)
    names = [n for n in dict.fromkeys(n for u in units for n in u.names) if n not in labels]
    # As for labels: "Main Menu" is a name, but "the main menu" is ordinary
    # text. A name also written as text is kept only with its capitals, and
    # only if it has two words or more: one capitalized word also starts
    # sentences ("Exit the menu").
    always = [n for n in names if _written_as_name(n, text)]
    exact = [n for n in names if n not in always and len(n.split()) >= 2 and n in text]
    kept = {k.lower() for k in labels + always + exact}
    quoted = [q for q in quoted_names(units) if q.lower() not in kept]
    return Glossary(keep=labels + always + abbreviations, exact=exact, quoted=quoted)


# A quoted phrase an instruction points at is a name on screen: "select
# ‘View configuration’", "at the “login” prompt", "the ‘Control’ tab",
# "will display “226 Transfer complete”".
QUOTE = re.compile(rf"(?<!\w)[{QUOTES}]([^{QUOTES}]{{1,60}})[{QUOTES}](?!\w)")
QUOTE_JOINER = re.compile(r"\s*(?:,|and|or|/)\s*$")
POINTED_BEFORE = re.compile(
    r"\b(?:enter|type|select|click|press|choose|using|displays?|displayed|default|at the)\s*$", re.IGNORECASE
)
POINTED_AFTER = re.compile(
    r"^\s*(?:prompts?|(?:when\s+)?prompted|tabs?|buttons?|keys?|menus?|commands?|fields?|options?|header|"
    r"window|screen|dialog|mode|position|item)\b",
    re.IGNORECASE,
)
MAX_QUOTED_WORDS = 5


def quoted_names(units) -> list[str]:
    """Quoted names an instruction points at, by a verb before them or the
    kind of thing after them; they stay as the reader finds them on screen.
    A quoted word that is just a word ("the “subnet mask” of the network",
    "a “crossover” cable") is translated."""
    names = []
    for unit in units:
        matches = list(QUOTE.finditer(unit.text))
        # Quotes joined by "and", "or" or commas share their context: Enter
        # “customer” for both “User” and “Password” prompts.
        groups: list[list[re.Match]] = []
        for match in matches:
            if groups and QUOTE_JOINER.match(unit.text[groups[-1][-1].end() : match.start()]):
                groups[-1].append(match)
            else:
                groups.append([match])
        for group in groups:
            before = unit.text[: group[0].start()]
            after = unit.text[group[-1].end() :]
            if not (POINTED_BEFORE.search(before) or POINTED_AFTER.match(after)):
                continue
            for match in group:
                name = match.group(1).strip()
                if 1 <= len(name.split()) <= MAX_QUOTED_WORDS and "(" not in name and any(c.isalpha() for c in name):
                    names.append(name)
    return list(dict.fromkeys(names))


# Names shown by a screen: "<1> Network Setup", "(2) Hostname [fc-default]",
# "**** SNMP Setup ****".
SCREEN_NAME = [
    re.compile(r"^<\w{1,3}>\s+(.+?)(?:\s{2,}.*|\s*\[.*)?$"),
    re.compile(r"^\(\d{1,2}\)\s+(.+?)\s*\[.*$"),
    re.compile(r"^([*=#~-])\1{2,}\s*(?P<banner>.+?)\s*([*=#~-])\3{2,}$"),
]


def screen_names(lines: list[str]) -> list[str]:
    """Menu items and screen titles of the screens a document reproduces:
    the reader finds them as written on the device, so they stay as written."""
    names = []
    for line in lines:
        for pattern in SCREEN_NAME:
            if match := pattern.match(line.strip()):
                name = match.groupdict().get("banner") or match.group(1)
                name = name.strip(" .:")
                if len(name) > 2 and any(c.isalpha() for c in name):
                    names.append(name)
                break
    return list(dict.fromkeys(names))


def _written_as_name(name: str, text: str) -> bool:
    found = re.findall(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
    return bool(found) and all(f == name for f in found)


QUOTED = re.compile(r"[\"“”‘’'`]([^\"“”‘’'`]{2,60})[\"“”‘’'`]")


def quoted_terms(texts: list[str], terms: list[str]) -> set[str]:
    """Terms that appear quoted somewhere: labels of the software's screens,
    menus and buttons ('Add/Update Frame'), which stay as the software shows."""
    quoted = {m.group(1).strip().lower() for text in texts for m in QUOTED.finditer(text)}
    return {t for t in terms if t.lower() in quoted}


# Words that end a company's name ("KOKUSAI DENKI Electric Linear S/A").
COMPANY_ENDINGS = ("S/A", "S.A.", "Inc", "Ltd", "LLC", "GmbH", "Corp", "Co.", "Ltda")


def name_fragments(texts: list[str], terms: list[str]) -> set[str]:
    """Terms that are always part of a longer name: beside an all-capital
    word or a company ending wherever they occur ("Electric Linear" in
    "KOKUSAI DENKI Electric Linear S/A"). Translated, they broke the name
    ("KOKUSAI DENKI elétrico linear S/A")."""
    fragments = set()
    for term in terms:
        found = [
            (text[: m.start()], text[m.end() :])
            for text in texts
            for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text)
        ]
        if found and all(_beside_name(before, after) for before, after in found):
            fragments.add(term)
    return fragments


def _beside_name(before: str, after: str) -> bool:
    previous = re.search(r"(\S+) $", before)
    following = re.match(r" (\S+)", after)
    if previous and re.fullmatch(r"[A-Z][A-Z0-9&-]{1,}", previous.group(1)):
        return True
    return bool(following) and following.group(1).rstrip(".,;:") in {e.rstrip(".") for e in COMPANY_ENDINGS}


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
