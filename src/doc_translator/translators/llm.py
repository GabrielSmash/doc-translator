"""Context-aware translation with a local LLM served by Ollama.

Unlike sentence-level models, the LLM:
1. reads the document once and builds a glossary (technical terms, product
   names, UI labels) so terminology stays consistent;
2. translates paragraphs in batches, in reading order, seeing the text that
   precedes them;
3. translates each styled phrase (bold, links...) on its own, after the
   paragraphs; align.py finds it in the paragraph's translation, as for
   the fast engine.

Answers are requested as JSON (Ollama structured outputs) and validated;
anything that doesn't check out is retried one paragraph at a time.
"""

import hashlib
import json
import logging
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from ..languages import EN_PT_BR, LanguagePair
from ..spelling import Spelling, source_spelling, spelling
from ..terms import (
    domain_words,
    kept_terms,
    name_fragments,
    quoted_terms,
    recurring_terms,
    repeated_phrases,
)
from .base import Glossary, Unit, UnitTranslation, has_words
from .cache import glossary_id
from .grammar import grammar
from .checks import (
    NUMBER,
    Vocabulary,
    codes,
    leading_source_word,
    lost_abbreviations,
    missing_tokens,
    token_problems,
    unchanged_words,
    vocabulary,
)

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
CONTEXT_UNITS = 3  # preceding paragraphs shown as context
# A comic's balloons are short and a scene runs over pages: three balloons
# did not say who was speaking or what was going on ("arms" written as
# "pernas"). About two pages before, and the next balloons (an answer tells
# what a question meant).
STORY_CONTEXT = 30
STORY_FOLLOWING = 6
# How much of the document the model reads to describe it: its headings and
# its opening text, up to this many characters.
BRIEF_CHARS = 3000
BRIEF_NAMES = 30  # names of the document listed in the description
EXAMPLE_CHARS = 160  # usage example shown with each glossary term
# Batches trade prompt overhead (instructions/context repeated per call)
# against the cost of a bad answer, which sends the whole batch to retry.
BATCH_CHARS = 1500
BATCH_UNITS = 10
# Cells are short, and a table split into batches was translated batch by
# batch: colors in English in one, in Portuguese in the next. A table goes
# in one batch up to this many cells (and BATCH_CHARS).
TABLE_BATCH_UNITS = 60
# A translation this much longer/shorter than the source is probably not a
# translation (an explanation, a refusal, a truncated answer).
MAX_LENGTH_RATIO = 2.5
MIN_LENGTH_RATIO = 0.3

SYSTEM_PROMPT = """You are a professional translator of technical documents from \
{source} to {target} ({target_code}).
Rules:
- Translate the meaning faithfully; never add, omit or explain information.
- Keep product names, model numbers, code, commands, file names, numbers, \
units, URLs and emails exactly as in the original.
- Use the standard {target} terminology of the document's field. \
Keep in {source} the technical terms {speakers} professionals keep in {source}.
- Follow the glossary when one is given.
- Be concise: each translation must fit in roughly the same space as the original.
- Preserve the form: a heading stays a heading, a fragment stays a fragment, \
uppercase stays uppercase."""

# Plain lines rather than a JSON schema: with some documents, schema-constrained
# decoding made the model emit whitespace until the token limit.
TERMS_PROMPT = """These terms recur in a technical document being translated \
from {source} into {target}. Decide how each one must be written \
everywhere in the translation, so the terminology stays consistent.
Translate by default. Answer KEEP only for: product and brand names, names of \
screens, menus, buttons or options shown by the software (in {source}), and \
technical terms {speakers} professionals really use in {source}. Capital letters \
alone do not make a name.
Answer one line per term, in the same order, exactly as in these examples:
{examples}

Terms, each with an example of use:
{terms}"""
# A comic's repeated phrases (ADR 0008): names or words?
NAMES_PROMPT = """These phrases recur in a comic book's balloons. For each, say \
whether it is a NAME (of a character, place, team, organization or named \
thing) or common WORDS.
Answer one line per phrase, in the same order, as:
Doctor Doom => NAME
Secret Formula => WORDS

Phrases, each with an example of use:
{phrases}"""
# Single words: see _build_glossary.
WORDS_PROMPT = """Translate this sentence of a technical document into \
{target}, using the standard terminology of the document's field:
{sentence}
Answer with {{"translation": "...", "word": "..."}}: "word" is the {target} \
word you used for "{word}", or "{word}" if you kept it in {source}."""
TERM_LINE = re.compile(r"^\s*[-*\d.]*\s*(.+?)\s*=>\s*(.+?)\s*$")

# Written once per document and shown with every batch: a paragraph alone
# does not say whether "Setup" is a menu or "frame" a video frame.
# No list of names to keep: asked for one, the model listed ordinary words
# ("STANDARD", "Modulation", "Jumpers"), and told to keep "STANDARD" while
# translating it, wrote Spanish ("ESTÁNDAR"). Names are found in the
# document itself (terms.kept_terms, quoted and name pieces).
BRIEF_PROMPT = """Below are the headings and the opening text of a document \
that will be translated. In at most 4 short lines, in {source}, describe: what \
the document is and its field; who it is written for; the meaning that \
ambiguous words have in it. No preamble.

{excerpt}"""

# A comic read whole before it is translated (ADR 0008): who its characters
# are. Without it the model wrote "A SUPER-HERÓI" for Spider-Man.
CAST_PROMPT = """Below is part {part} of {parts} of a comic book's text \
(speech balloons and captions, in reading order). List its characters, \
those who appear and those who are named, adding to the list so far: for \
each, the name as written, the gender ("male", "female", "group" or \
"unknown"), the other names the text calls them by, as written (a short \
form, a nickname, a title with the name), and in a few words who they are \
(their role), and whether the name is a proper name ("Spider-Man", "Aunt \
May": true) or only a role or a description ("the governor", "a guard": \
false). Keep the entries of the list so far, completing them.
Answer with {{"characters": [{{"name": "...", "gender": "...", "aliases": ["..."], "proper_name": true, "about": "..."}}]}}.

List so far:
{known}

Text:
{text}"""
CAST_CHARS = 6000  # text read per call
CAST_SIZE = 25  # characters kept in the list


# Balloons read from a comic's pages (ADR 0008).
COMIC_STYLE = (
    "The texts are a comic book's speech balloons, thought balloons and narration captions, in "
    "reading order. Translate speech as natural, colloquial {target} dialogue, as {target} comics "
    "do: short and lively, keeping exclamations and the tone; captions as narration. Keep the names "
    "of characters, places and teams as in the original. Words for a character (hero, villain, "
    "adjectives, articles) agree with the character's gender. The texts come from OCR: a letter may be "
    "misread (\"L MUST\" for \"I MUST\")."
)

BATCH_PROMPT = """Translate each item of the JSON array below into \
{target}. Answer with {{"translations": [...]}}: exactly {count} strings, \
in the same order, one per item.

{items}"""

def _string_array(count: int | None = None) -> dict:
    schema: dict = {"type": "array", "items": {"type": "string"}}
    if count is not None:
        schema |= {"minItems": count, "maxItems": count}
    return schema


class LLMTranslator:
    def __init__(
        self,
        model: str,
        host: str = DEFAULT_HOST,
        glossary: Glossary | None = None,
        languages: LanguagePair = EN_PT_BR,
        context: str = "",
    ) -> None:
        self.model = model
        # What the user says about the document ("Doctor Octopus has four
        # mechanical arms"): read before describing it, and shown with every
        # prompt.
        self.user_context = " ".join(context.split())
        self.languages = languages
        self.host = host.rstrip("/")
        self.user_glossary = glossary or Glossary()
        self._cache: dict[str, str] = {}
        # Ollama's memory estimate is conservative and leaves layers on the
        # CPU even when they fit: asking for every layer on the GPU made
        # generation ~1.75x faster on a 4 GB card. Dropped if it runs out of
        # memory.
        self._all_layers_on_gpu = True
        self._progress = None
        self._comic = False
        self._grammar = None
        # The fast engine's translation of each balloon: what it is about
        # (ADR 0008).
        self._references: dict[str, str] = {}
        # The comic's characters' names, as the cast sheet gives them.
        self._cast_names: list[str] = []
        self._brief = ""
        self._names = ""
        self._kept_words: set[str] = set()
        self._sections: dict[str, str] = {}
        self._known: Vocabulary | None = None
        self._spelling: Spelling | None = None
        self._tables: dict[str, str] = {}
        self._table_cells: dict[str, list[str]] = {}
        self._context_units = CONTEXT_UNITS
        self._following_units = 0
        digest = self._check_model()
        # The digest changes when `ollama pull` brings a new version.
        self.cache_id = f"llm|{model}|{digest}|{languages.target_code}|{glossary_id(glossary)}"
        if self.user_context:
            self.cache_id += "|" + hashlib.sha256(self.user_context.encode()).hexdigest()[:16]

    def keep_progress(self, store) -> None:
        """Save each translated batch to `store` (a cache namespace) and
        skip the paragraphs it already holds."""
        self._progress = store

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        if not units:
            return []
        # Names of screens and fields, set in italics, shown on screens or
        # quoted by instructions, and abbreviations used as they are ("N/A").
        kept = kept_terms(units, self.languages)
        # Described first: the terminology is decided knowing the field ("card"
        # in a frame is a circuit board).
        self._brief = self._describe(units)
        if any(unit.kind == "balloon" for unit in units):
            cast = self._cast([u.text for u in units if u.kind == "balloon"])
            self._brief = "\n".join(part for part in (self._format(COMIC_STYLE), cast, self._brief) if part)
            self._comic = True
            # Agreement the model gets wrong and cannot see (ADR 0008).
            self._grammar = self._load_grammar()
            self._context_units, self._following_units = STORY_CONTEXT, STORY_FOLLOWING
        # The names the document itself shows to be names (its screens'
        # items, labels, quoted names), not a list asked of the model: asked,
        # it listed ordinary words ("STANDARD") and then wrote them in
        # Spanish. Shown with every batch, they keep "Network Setup" whole.
        # Only with the texts to translate: shown when deciding the
        # terminology, they made the model keep "frame" and lose "Frame
        # Controller" -> "controlador de quadro".
        if names := [*kept.exact, *kept.keep, *kept.quoted][:BRIEF_NAMES]:
            self._names = f"Names kept as written ({self.languages.source}): " + ", ".join(names)
        self._spelling = self._load_spelling()
        self._known = vocabulary(units, self._load_source_spelling(), self._spelling)
        glossary = self._build_glossary(units, kept).merged(kept)
        if self._cast_names:
            # As the cast sheet writes them: the OCR's "SPIDER MAN" and
            # "DR.OCTOPUS" are "SPIDER-MAN" and "DR. OCTOPUS".
            texts = [u.text for u in units]
            written = names_written([*self._cast_names, *short_forms(self._cast_names, texts)], texts)
            keep = [form for form, name in written.items() if form.lower() == name.lower()]
            terms = {form: name.upper() if form.isupper() else name for form, name in written.items() if form.lower() != name.lower()}
            log.info("Characters' names kept: %s; written as the cast sheet does: %s", keep, terms)
            glossary = glossary.merged(Glossary(keep=keep, terms=terms))
            glossary.keep[:] = [k for k in glossary.keep if k.lower() not in {t.lower() for t in terms}]
        glossary = glossary.merged(self.user_glossary)
        if self._comic:
            self._references = self._reference_translations([u.text for u in units if u.kind == "balloon"], glossary)
        # Words of kept names are asked capitalized (_asked).
        self._kept_words = {w.lower() for name in glossary.kept() for w in re.findall(r"[^\W\d_]+", name)}
        for unit in units:
            self._sections.setdefault(unit.text, unit.section)
            if unit.table:
                self._tables.setdefault(unit.text, unit.table)
                self._table_cells.setdefault(unit.table, []).append(unit.text)
        paragraphs = [u.text for u in units]
        self._translate_all(paragraphs, glossary, with_context=True)
        # Styled phrases on their own, after the paragraphs, as the fast
        # engine does; align.py finds them in the paragraph translations.
        # Put in the paragraphs' batches, or shown their own paragraph as
        # context, they made the model copy whole paragraphs in English.
        self._translate_all([s for u in units for s in u.segments], glossary, with_context=False)

        return [
            UnitTranslation(self._cache.get(u.text, u.text), [self._cache.get(s, s) for s in u.segments])
            for u in units
        ]

    def _load_grammar(self):
        return grammar(self.languages)

    def _reference_translations(self, texts: list[str], glossary: Glossary) -> dict[str, str]:
        """Each text translated by the fast engine (Opus-MT), its names kept.
        Its style is poor, but it does not change the subject: the local model
        did ("THE WORLD'S MOST DREADED SUPER-VILLAIN" -> "A MAIS FAMOSA ESTAÇÃO
        DE RÁDIO DO MUNDO"), or gave a balloon the next one's translation."""
        texts = list(dict.fromkeys(t for t in texts if has_words(t)))
        if not texts or (engine := self._load_reference(glossary)) is None:
            return {}
        log.info("Translating the balloons with the fast engine too: each translation must say what it says")
        return dict(zip(texts, engine.translate(texts)))

    def _load_reference(self, glossary: Glossary):
        try:
            from huggingface_hub import snapshot_download

            from .opus import OpusTranslator

            model_dir = Path(snapshot_download(self.languages.opus_model))
            # On the CPU: the GPU holds the local model.
            return OpusTranslator(model_dir, "cpu", Glossary(keep=glossary.kept()), self.languages)
        except Exception as error:  # no model, no download: the check is skipped
            log.warning("Fast engine unavailable (%s): translations not compared with it", error)
            return None

    def _load_spelling(self) -> Spelling | None:
        return spelling(self.languages)

    def _load_source_spelling(self) -> Spelling | None:
        return source_spelling(self.languages)

    def _translate_all(self, texts: list[str], glossary: Glossary, with_context: bool) -> None:
        """Translate each distinct text once into self._cache (headers and
        footers repeat on every page), batched in reading order, with the
        preceding text as context."""
        first_seen: dict[str, int] = {}
        for index, text in enumerate(texts):
            if has_words(text):
                first_seen.setdefault(text, index)
        pending = list(first_seen)
        if self._progress is not None:
            resumed = self._progress.get_many([t for t in pending if t not in self._cache])
            self._cache.update(resumed)
            if resumed:
                log.info("Resuming: %d texts translated by an earlier run", len(resumed))
        done = sum(text in self._cache for text in pending)
        pending = [text for text in pending if text not in self._cache]
        # The cells of one table go together, with the table as context.
        for batch in _batches(pending, lambda text: self._tables.get(text, "")):
            start = first_seen[batch[0]]
            context = texts[max(0, start - self._context_units) : start] if with_context else []
            end = first_seen[batch[-1]] + 1
            following = texts[end : end + self._following_units] if with_context else []
            translated = dict(zip(batch, self._translate_batch(batch, context, glossary, following)))
            self._cache.update(translated)
            if self._progress is not None:
                self._progress.put_many(translated)
            done += len(batch)
            log.info("Translated %d/%d %s", done, len(first_seen), "paragraphs" if with_context else "styled phrases")

    def _describe(self, units: list[Unit]) -> str:
        """A few lines on what the document is, for every batch's prompt."""
        headings = list(dict.fromkeys(u.section for u in units if u.section))
        excerpt = "Headings:\n" + "\n".join(headings) + "\n\nText:\n"
        excerpt += "\n".join(u.text for u in units if u.text not in headings)
        excerpt = excerpt[:BRIEF_CHARS]
        if self.user_context:
            excerpt = f"Said by the user who asked for the translation:\n{self.user_context}\n\n{excerpt}"
        try:
            brief = self._chat(
                [{"role": "user", "content": self._format(BRIEF_PROMPT, excerpt=excerpt)}],
                max_tokens=200,
            )
        except LLMError as error:
            log.warning("Could not describe the document (%s); continuing without it", error)
            return self._with_user_context("")
        log.info("Document: %s", " ".join(brief.split()))
        return self._with_user_context(brief.strip())

    def _cast(self, texts: list[str]) -> str:
        """The comic's characters, read from all its text in parts: one line
        each, with its gender, for every batch's prompt."""
        parts, current = [], ""
        for text in texts:
            if current and len(current) + len(text) > CAST_CHARS:
                parts.append(current)
                current = ""
            current += text + "\n"
        if current:
            parts.append(current)
        schema = {
            "type": "object",
            "properties": {
                "characters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "gender": {"type": "string", "enum": ["male", "female", "group", "unknown"]},
                            "aliases": {"type": "array", "items": {"type": "string"}},
                            "proper_name": {"type": "boolean"},
                            "about": {"type": "string"},
                        },
                        "required": ["name", "gender", "aliases", "proper_name", "about"],
                    },
                },
            },
            "required": ["characters"],
        }
        cast: list[dict] = []
        for number, text in enumerate(parts, 1):
            known = "\n".join(_cast_line(c) for c in cast) or "(none yet)"
            task = self._format(CAST_PROMPT, part=number, parts=len(parts), known=known, text=self._asked(text))
            if self.user_context:
                task = f"Said by the user who asked for the translation:\n{self.user_context}\n\n{task}"
            try:
                answer = json.loads(self._chat([{"role": "user", "content": task}], schema=schema, max_tokens=1500))
                cast = [c for c in answer["characters"] if str(c.get("name", "")).strip()][:CAST_SIZE] or cast
            except (ValueError, KeyError, LLMError) as error:
                log.warning("Could not read the comic's characters, part %d (%s)", number, error)
        if not cast:
            return ""
        # Their names, kept as in the original (the user's decision): the
        # model kept "SPIDER-MAN" in one balloon, and wrote "O FOGO DO
        # TORCEDOR" for the Human Torch called "TORCH" in another.
        # A role is no name: kept, "THE GOVERNOR" was "O GOVERNOR".
        self._cast_names = [
            name.strip()
            for c in cast
            if c.get("proper_name", True) is not False
            for name in [c["name"], *(c.get("aliases") or [])]
            if isinstance(name, str) and name.strip()
        ]
        sheet = "Characters (words for each agree with their gender):\n" + "\n".join(_cast_line(c) for c in cast)
        log.info("%s", sheet.replace("\n", "; "))
        return sheet

    def _with_user_context(self, brief: str) -> str:
        if not self.user_context:
            return brief
        return f"From the user (follow it): {self.user_context}\n{brief}".strip()

    # -- glossary ------------------------------------------------------------

    def _build_glossary(self, units: list[Unit], kept: Glossary | None = None) -> Glossary:
        """Ask the model to decide, once, how each recurring term and each
        domain word ("card", "jumper") is rendered (ADR 0006)."""
        texts = [u.text for u in units]
        terms = recurring_terms(texts, self.languages.source_stopwords)
        comic_names = self._comic_names(texts) if any(unit.kind == "balloon" for unit in units) else []
        decided = [*(kept.kept() if kept else []), *self.user_glossary.kept(), *self.user_glossary.terms]
        words = domain_words(units, self.languages, exclude=[*decided, *terms])
        glossary = Glossary(keep=list(comic_names))
        if terms:
            listing = "\n".join(f'- {term} (e.g. "{_excerpt(example, term)}")' for term, example in terms.items())
            answer = self._ask_terminology(self._format(TERMS_PROMPT, terms=listing), len(terms))
            self._read_decisions(answer, list(terms), glossary)
        # Asked what a word is, the model answers with its everyday sense or
        # keeps it ("card" -> "cartão", "pin" -> KEEP); asked to translate a
        # sentence using it, it writes the field's word ("placa", "pino").
        # One word per request: given a list, the model drops lines.
        # Decided in two of its uses that agree: asked once, the model
        # sometimes reports a word of another part of the sentence
        # ("standard" -> "transmissão", "mode" -> "hierárquicos"), and that
        # decision is then imposed on the whole document. When two answers
        # differ ("porta", "porto"), a third use breaks the tie; without two
        # alike, each sentence decides.
        for word, uses in words.items():
            decisions = [self._word_used(word, use) for use in uses[:2]]
            if not _agreed(decisions) and len(uses) > 2:
                decisions.append(self._word_used(word, uses[2]))
            if decision := _agreed(decisions):
                self._read_decisions(f"{word} => {decision}", [word], glossary)
            else:
                log.info("Terminology: %r left to each sentence (%s)", word, decisions)
        # The model can't reliably tell UI labels from common terms; the
        # document can: labels are the terms it puts between quotes, and
        # pieces of a company's name sit beside the rest of it.
        for term in quoted_terms(texts, list(terms)) | name_fragments(texts, list(terms)):
            glossary.terms.pop(term, None)
            if term not in glossary.keep:
                glossary.keep.append(term)
        # A decision with a word the language does not have ("máscara de
        # subrede") would put that word everywhere.
        if self._spelling is not None:
            for term, decision in list(glossary.terms.items()):
                if invented := self._spelling.unknown(term, decision):
                    log.info("Terminology: %r -> %r dropped (%s)", term, decision, ", ".join(invented))
                    del glossary.terms[term]
        log.info("Terminology: keep %s; translate %s", glossary.keep, glossary.terms)
        return glossary

    def _comic_names(self, texts: list[str]) -> list[str]:
        """The phrases of a comic that are names, kept as in the original
        (ADR 0008). In capitals, case tells nothing: candidates are found
        by repetition ("DOCTOR OCTOPUS", but also "ARTIFICIAL ARMS"), and the
        model says which are names. Asked how to render them, it translated
        names ("Dr. Ocupo")."""
        candidates = repeated_phrases(texts, self.languages.source_stopwords)
        if not candidates:
            return []
        listing = "\n".join(f'- {phrase} (e.g. "{_excerpt(example.title(), phrase)}")' for phrase, example in candidates.items())
        answer = self._ask_terminology(self._format(NAMES_PROMPT, phrases=listing), len(candidates))
        names = []
        for line in answer.splitlines():
            if (match := TERM_LINE.match(line)) and match.group(2).strip("\"'` *").upper() == "NAME":
                phrase = match.group(1).strip("\"'`* -")
                names += [c for c in candidates if c.lower() == phrase.lower()]
        log.info("Names kept: %s", names)
        return names

    def _word_used(self, word: str, sentence: str) -> str:
        task = self._format(WORDS_PROMPT, sentence=sentence, word=word)
        if self._brief:
            task = f"About the document:\n{self._brief}\n\n{task}"
        schema = {
            "type": "object",
            "properties": {"translation": {"type": "string"}, "word": {"type": "string"}},
            "required": ["translation", "word"],
        }
        try:
            answer = json.loads(self._chat([{"role": "user", "content": task}], schema=schema, max_tokens=64 + len(sentence)))
        except (ValueError, LLMError) as error:
            log.debug("No decision for %r (%s)", word, error)
            return ""
        used = str(answer.get("word", "")).strip()
        # A word the translation does not use was not its rendering.
        return used if used.lower() in str(answer.get("translation", "")).lower() else ""

    def _ask_terminology(self, task: str, lines: int) -> str:
        if self._brief:
            task = f"About the document:\n{self._brief}\n\n{task}"
        try:
            return self._chat([{"role": "user", "content": task}], max_tokens=64 + 24 * lines)
        except LLMError as error:
            log.warning("Could not decide the terminology (%s); continuing without it", error)
            return ""

    @staticmethod
    def _read_decisions(answer: str, asked: list[str], glossary: Glossary) -> None:
        """Lines "term => decision" into `glossary`; the first answer per term."""
        by_name = {term.lower(): term for term in asked}
        for line in answer.splitlines():
            if not (match := TERM_LINE.match(line)):
                continue
            # "<word> pin => pino": small models copy the placeholder.
            name = re.sub(r"<[^>]*>", "", match.group(1)).strip("\"'`* ")
            term = by_name.pop(name.lower(), None)
            decision = match.group(2).strip("\"'`* ")
            if not term or not decision:
                continue  # only terms we asked about
            if decision.upper() == "KEEP" or decision.lower() == term.lower():
                glossary.keep.append(term)
            else:
                glossary.terms[term] = decision

    # -- translation ---------------------------------------------------------

    def _translate_batch(
        self, texts: list[str], context: list[str], glossary: Glossary, following: list[str] = ()
    ) -> list[str]:
        items = json.dumps([self._asked(t) for t in texts], ensure_ascii=False, indent=0)
        task = self._format(BATCH_PROMPT, count=len(texts), items=items)
        prompt = self._prompt(task, texts, context, glossary, following)
        schema = {
            "type": "object",
            "properties": {"translations": _string_array(len(texts))},
            "required": ["translations"],
        }
        try:
            answer = self._chat(prompt, schema=schema, max_tokens=256 + sum(len(t) for t in texts))
            translations = json.loads(answer)["translations"]
        except (ValueError, KeyError, LLMError) as error:
            log.debug("Batch answer unusable (%s); translating one by one", error)
            translations = []
        if len(translations) != len(texts):
            translations = [""] * len(texts)

        results = []
        for index, (text, translation) in enumerate(zip(texts, translations)):
            translation = self._match_case(text, _clean(translation, text))
            if _problems(text, translation, glossary, self._known, self._spelling) or _shifted(index, texts, translations):
                # Context for a lone retry: whatever preceded it in the batch.
                previous = (context + texts[:index])[-self._context_units :]
                retry = self._translate_one(text, previous, glossary)
                # The retry can be worse (left in English where the batch
                # only missed a glossary term): keep the lesser problem.
                translation = min(
                    [translation, retry], key=lambda t: _rank(text, t, glossary, self._known, self._spelling)
                )
                # Retried without knowing what was wrong, a model can drop the
                # same code again ("RJ45") or translate the same name ("Network
                # Setup" starting a sentence): a last try names them.
                kept = set(glossary.kept())
                names = [t for t in glossary.violations(text, translation) if t in kept]
                # And words that do not exist ("reinsertado"): named, the model
                # writes a real one.
                invented = self._spelling.unknown(text, translation, self._known.source_words) if self._spelling and self._known else []
                # An English "'s" on a Portuguese word ("AMÉRICA'S") is one too.
                invented += english_possessives(text, translation)
                profane = added_profanity(text, translation)
                abbreviations = lost_abbreviations(text, translation, self._known)
                if (missing := missing_tokens(text, translation) + abbreviations + names) or invented or profane:
                    last = self._translate_one(text, previous, glossary, missing, invented, profane)
                    translation = min(
                        [translation, last], key=lambda t: _rank(text, t, glossary, self._known, self._spelling)
                    )
                # Still there after being named: a source word made to look
                # Portuguese ("prompção") is written as the source word.
                if self._spelling and self._known and (
                    left := self._spelling.unknown(text, translation, self._known.source_words)
                ):
                    translation = self._spelling.source_words_for(text, translation, left)
                # Still wrong in the sense or the language after being told:
                # the long prompt itself can be the cause (the 8b model copied
                # "AMERICA'S" in batches, not alone). Translated on its own,
                # with a short prompt.
                invented_left = (
                    self._spelling.unknown(text, translation, self._known.source_words) if self._spelling and self._known else []
                )
                if invented_left or _severity(text, translation, glossary, self._known, self._spelling) >= PLAIN_SEVERITY:
                    plain = self._translate_plain(text, glossary)
                    translation = min(
                        [translation, plain], key=lambda t: _rank(text, t, glossary, self._known, self._spelling)
                    )
                # Words that still do not exist: the grammar checker's spelling
                # ("VILLÃO" -> "VILÃO"), when it gives a word of the language.
                if self._grammar is not None and self._spelling and self._known and (
                    left := self._spelling.unknown(text, translation, self._known.source_words)
                ):
                    respelled = self._respelled(translation, left)
                    if _rank(text, respelled, glossary, self._known, self._spelling) < _rank(
                        text, translation, glossary, self._known, self._spelling
                    ):
                        translation = respelled
                if problems := _problems(text, translation, glossary, self._known, self._spelling):
                    log.warning("Check translation of %r (%s): %r", text[:50], problems, translation[:80])
            previous = (context + texts[:index])[-self._context_units :]
            if (reference := self._references.get(text)) and translation:
                translation = self._faithful(text, translation, reference, previous, glossary)
            if self._grammar is not None and translation:
                translation = self._grammatical(text, translation, previous, glossary)
            results.append(translation or text)
        return results

    def _faithful(self, text: str, translation: str, reference: str, previous: list[str], glossary: Glossary) -> str:
        """`translation`, or one saying what the source says when it shares
        almost no words with the fast engine's `reference`: translated again
        with what it said named, then alone with a short prompt. A candidate
        is taken when it shares enough and has no grave problem; failing
        that, the one sharing the most."""
        # Names are in both, whatever the rest says.
        stop = self.languages.target_function_words | {_fold(w) for w in self._kept_words}
        if not drifted(translation, reference, stop):
            return translation
        candidates = [translation]
        # A candidate may not leave in the source language words the first
        # one translated, unless the reference keeps them too ("SUPER-HERÓI"):
        # the reference can differ on a name only ("HOMEM-ARANHA" for the
        # "SPIDER-MAN" the model keeps), and a retry then left words in
        # English where the first had translated them.
        copied = copied_words(text, translation) | copied_words(text, reference)
        # Nor be worse by the other checks: a retry said what the balloon
        # says, and translated the name kept ("HOMEM-ARANHA").
        severity = _severity(text, translation, glossary, self._known, self._spelling)
        attempts = (
            lambda: self._translate_one(text, previous, glossary, drift=translation),
            lambda: self._translate_plain(text, glossary),
        )
        for attempt in attempts:
            candidate = attempt()
            log.debug("Meaning, candidate for %r: %r", text[:50], candidate)
            if (
                not candidate
                or not copied_words(text, candidate) <= copied
                or _severity(text, candidate, glossary, self._known, self._spelling) > severity
            ):
                continue
            if not drifted(candidate, reference, stop):
                log.debug("Meaning: %r -> %r", translation, candidate)
                return candidate
            candidates.append(candidate)
        best = max(candidates, key=lambda t: shared_share(t, reference, stop))
        log.warning("Check translation of %r (says something else than %r): %r", text[:50], reference[:60], best[:80])
        return best

    def _grammatical(self, text: str, translation: str, previous: list[str], glossary: Glossary) -> str:
        """`translation` without the grammar errors LanguageTool finds in it:
        translated again with the errors named, then alone with a short
        prompt, then with LanguageTool's own suggestions. Each is taken only
        free of grammar errors and no worse by the other checks."""
        if not (errors := self._grammar_errors(translation)):
            return translation
        rank = _rank(text, translation, glossary, self._known, self._spelling)
        attempts = (
            lambda: self._translate_one(text, previous, glossary, grammar=[e.describe() for e in errors]),
            lambda: self._translate_plain(text, glossary),
            lambda: self._grammar.corrected(translation),
        )
        for attempt in attempts:
            candidate = attempt()
            if (
                candidate
                and not self._grammar_errors(candidate)
                and _rank(text, candidate, glossary, self._known, self._spelling) <= rank
            ):
                log.debug("Grammar: %r -> %r", translation, candidate)
                return candidate
        log.warning("Check translation of %r (grammar: %s): %r", text[:50], "; ".join(e.describe() for e in errors), translation[:80])
        return translation

    def _respelled(self, translation: str, words: list[str]) -> str:
        """`translation` with `words` spelled as LanguageTool suggests, each
        when its suggestion is a word of the target dictionary one letter
        apart."""
        for word in words:
            try:
                suggestion = self._grammar.spelled(word)
            except OSError:
                return translation
            # One letter apart: a misspelling, not another word ("REINSERTADO"
            # is not "REINSERÇÃO").
            if suggestion and _one_edit(word.lower(), suggestion.lower()) and self._spelling.has(suggestion.lower()):
                fixed = suggestion.upper() if word.isupper() else suggestion
                translation = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", fixed, translation)
        return translation

    def _grammar_errors(self, text: str) -> list:
        try:
            return self._grammar.errors(text)
        except OSError as error:
            log.warning("Grammar checker unavailable (%s); translations not checked for grammar", error)
            self._grammar = None
            return []

    def _translate_plain(self, text: str, glossary: Glossary) -> str:
        """`text` translated alone, with a short prompt: its kind and the
        names it holds, no document description, context or batch."""
        kind = "comic book dialogue or narration" if self._comic else "text from a technical document"
        task = f"Translate into {self.languages.target} this {kind}. Answer with the translation only."
        if names := [name for name in glossary.kept() if name.lower() in text.lower()]:
            task += " Keep these names as written: " + ", ".join(names) + "."
        if terms := {s: t for s, t in glossary.terms.items() if s.lower() in text.lower()}:
            task += " Write these terms so: " + "; ".join(f"{s} => {t}" for s, t in terms.items()) + "."
        try:
            answer = self._chat([{"role": "user", "content": f"{task}\n\n{self._asked(text)}"}], max_tokens=64 + len(text))
        except LLMError:
            return ""
        return self._match_case(text, _clean(answer, text))

    def _translate_one(
        self,
        text: str,
        context: list[str],
        glossary: Glossary,
        missing: list[str] | None = None,
        invented: list[str] | None = None,
        profane: list[str] | None = None,
        grammar: list[str] | None = None,
        drift: str | None = None,
    ) -> str:
        prompt = self._prompt(f"Translate this text:\n{self._asked(text)}", [text], context, glossary)
        if terms := glossary.relevant(text, self.languages.source):
            prompt[0]["content"] += "\n- Mandatory terminology:\n" + terms
        if kept := codes(text) + NUMBER.findall(text):
            # In the system message: put next to the text, small models
            # translate the reminder along with it.
            prompt[0]["content"] += "\n- The translation must contain these codes and numbers unchanged: " + ", ".join(
                dict.fromkeys(kept)
            )
        if missing:
            prompt[0]["content"] += (
                "\n- A previous translation dropped or changed these; write each exactly as given: "
                + ", ".join(missing)
            )
        if invented:
            prompt[0]["content"] += (
                f"\n- A previous translation used words that do not exist in {self.languages.target}: "
                + ", ".join(invented)
                + ". Use correctly spelled words."
            )
            if any(POSSESSIVE.fullmatch(word) for word in invented):
                prompt[0]["content"] += (
                    f"\n- {self.languages.target} has no 's possessive: write \"the world's greatest hero\" "
                    "as \"the greatest hero of the world\", in the target language."
                )
        if profane:
            prompt[0]["content"] += (
                "\n- A previous translation used profanity the original does not have: "
                + ", ".join(profane)
                + ". Keep the original's register."
            )
        if grammar:
            prompt[0]["content"] += (
                f"\n- A previous translation had these {self.languages.target} grammar errors; "
                "write it without them: " + "; ".join(grammar)
            )
        if drift:
            prompt[0]["content"] += (
                f"\n- A previous translation said something the text does not say: \"{drift}\". "
                "Translate what the text says."
            )
        translation = _clean(self._chat(prompt, max_tokens=64 + len(text)), text)
        return self._match_case(text, translation)

    def _asked(self, text: str) -> str:
        """`text` as the model is asked to translate it. Capitals are
        lowered: in capitals the model kept "UPGRADING MODULE FIRMWARE
        THROUGH THE 7700FC FRAME CONTROLLER" as written, or wrote "SISTEMA
        DE MÓDULOS ... CONTROLLER DE FRAME"; lowered, it wrote "Atualização
        do firmware do módulo através do controlador de quadro 7700FC".
        Abbreviations and names keep their capitals (words that are not
        words of the language: "DHCP", "EVERTZ"); the answer is put back in
        capitals (_match_case)."""
        if not _mostly_upper(text) or self._known is None:
            return text

        def lower(match: re.Match) -> str:
            word = match.group(0)
            # Words of a kept name are a name's: "Doctor Octopus", not
            # "doctor octopus" (which the model translated "Dr. Aranha").
            if word.lower() in self._kept_words:
                return word.capitalize()
            return word.lower() if word.lower() in self._known.ordinary else word

        sentence = re.sub(r"[^\W\d_]+", lower, text)
        first = next((i for i, c in enumerate(sentence) if c.isalpha()), None)
        if first is None:
            return text
        return sentence[:first] + sentence[first].upper() + sentence[first + 1 :]

    def _prompt(
        self,
        task: str,
        texts: list[str],
        context: list[str],
        glossary: Glossary,
        following: list[str] = (),
    ) -> list[dict]:
        parts = []
        if self._brief:
            parts.append(f"About the document (context only):\n{self._brief}")
        if self._names:
            parts.append(self._names)
        if terms := glossary.relevant("\n".join(texts), self.languages.source):
            parts.append(f"Glossary:\n{terms}")
        section = self._sections.get(texts[0], "") if texts else ""
        if section and section not in texts:
            parts.append(f"Section: {section}")
        if table := self._tables.get(texts[0], "") if texts else "":
            # The whole table says more than the cells before these ones.
            parts.append(
                "The items are cells of this table (context only, do not translate it). "
                "Translate headers and values like any other text, keeping only names, "
                "codes and abbreviations; and translate each cell consistently with the "
                "rest of the table: the same word the same way in every cell.\n" + table
            )
            # A table too long for one batch: what earlier batches decided.
            done = [
                f"{cell} => {self._cache[cell]}"
                for cell in dict.fromkeys(self._table_cells.get(table, []))
                if cell in self._cache and cell not in texts
            ]
            if done:
                parts.append("Cells of this table already translated (follow them):\n" + "\n".join(done))
        elif context:
            parts.append("Preceding text (context only, do not translate):\n" + "\n".join(context))
        if following:
            parts.append("Text that follows (context only, do not translate):\n" + "\n".join(following))
        parts.append(task)
        return [
            {"role": "system", "content": self._format(SYSTEM_PROMPT)},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    def _format(self, prompt: str, **values) -> str:
        pair = self.languages
        return prompt.format(
            source=pair.source,
            target=pair.target,
            target_code=pair.target_code,
            speakers=pair.target_speakers,
            examples=pair.term_examples,
            **values,
        )

    def _match_case(self, source: str, translation: str) -> str:
        return _match_case(source, translation, self._spelling)

    # -- Ollama --------------------------------------------------------------

    def _check_model(self) -> str:
        """The model's digest; fails if Ollama or the model is missing."""
        try:
            tags = json.loads(self._request("/api/tags", None))
        except LLMError as error:
            raise LLMError(f"Ollama is not reachable at {self.host}: {error}") from error
        for model in tags.get("models", []):
            if model["name"] in (self.model, f"{self.model}:latest"):
                return model.get("digest", "")
        raise LLMError(f"model {self.model!r} not found; run `ollama pull {self.model}`")

    def _chat(
        self, messages: list[dict], schema: dict | None = None, max_tokens: int = 1024
    ) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            # num_predict caps runaway generations (loops, "thinking aloud").
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
        if schema:
            body["format"] = schema
        if self._all_layers_on_gpu:
            body["options"]["num_gpu"] = 999
            try:
                return json.loads(self._request("/api/chat", body))["message"]["content"]
            except LLMError as error:
                if "memory" not in str(error).lower():
                    raise
                log.warning("Model doesn't fit entirely on the GPU; letting Ollama split it")
                self._all_layers_on_gpu = False
                del body["options"]["num_gpu"]
        return json.loads(self._request("/api/chat", body))["message"]["content"]

    def _request(self, path: str, body: dict | None) -> str:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.host + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            # Ollama explains failures (e.g. out of memory) in the body.
            raise LLMError(f"{error}: {error.read().decode(errors='replace')}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise LLMError(str(error)) from error


class LLMError(RuntimeError):
    pass


def _batches(texts: list[str], group=lambda text: "") -> list[list[str]]:
    """Consecutive texts in batches; a batch holds texts of one `group`
    only (the cells of one table, or running text)."""
    batches: list[list[str]] = []
    size = 0
    for text in texts:
        if batches:
            limit = TABLE_BATCH_UNITS if group(batches[-1][0]) else BATCH_UNITS
            full = len(batches[-1]) >= limit or size + len(text) > BATCH_CHARS
        if not batches or full or group(text) != group(batches[-1][0]):
            batches.append([])
            size = 0
        batches[-1].append(text)
        size += len(text)
    return batches


# An answer this similar to another item of the batch (its source or its
# answer) belongs to that item: the model shifted the answers.
SHIFTED_SIMILARITY = 0.8
SHIFTED_MARGIN = 0.2


def _shifted(index: int, texts: list[str], translations: list[str]) -> bool:
    """Is the answer at `index` another item's text? ("The two criteria
    are:" came back as the next item, in English.) Only when the answer is
    clearly closer to the other item than to its own: neighboring cells
    ("Green/White or White", "Green/White or Green") look alike."""
    from difflib import SequenceMatcher

    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()

    answer = translations[index]
    if not answer.strip():
        return False
    own = similarity(answer, texts[index])
    return any(
        (other := similarity(answer, text)) >= SHIFTED_SIMILARITY and other > own + SHIFTED_MARGIN
        for position, text in enumerate(texts)
        if position != index
    )


def _one_edit(a: str, b: str) -> bool:
    """`a` and `b` differ by one letter added, dropped or changed."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def short_forms(names: list[str], texts: list[str]) -> list[str]:
    """The last word of a name of several, where the texts use it alone after
    "the" ("THE TORCH'S LECTURE" for the Human Torch): the name's short
    form. Asked, the model listed it in one run and not in the next, and
    "TORCH" was translated ("TORCIDA")."""
    found = []
    for name in names:
        words = re.findall(r"[^\W\d_]+", name)
        if len(words) < 2 or len(words[-1]) < SHORT_FORM_LETTERS:
            continue
        first, last = re.escape(words[-2]), re.escape(words[-1])
        alone = re.compile(rf"\bthe\s+(?!{first}[\s-]){last}\b", re.I)
        if any(alone.search(text) for text in texts):
            found.append(words[-1])
    return found


# A short form is a word this long at least ("TORCH"; not "MAY", "MAN").
SHORT_FORM_LETTERS = 4


def names_written(names: list[str], texts: list[str]) -> dict[str, str]:
    """How `texts` write each of `names`, and the name: an article before it
    left out ("the Torch": "TORCH"), a hyphen or a space between its words
    either way, or none after a period ("SPIDER MAN", "DR.OCTOPUS", as the
    OCR reads "SPIDER-MAN" and "DR. OCTOPUS"). Names the texts do not have
    are left out."""
    found: dict[str, tuple[str, str]] = {}
    for name in names:
        name = re.sub(r"^(?:the|a|an)\s+", "", name.strip(), flags=re.I)
        words = re.findall(r"[^\W_]+(?:['’.][^\W_]+)*\.?", name)
        if not words:
            continue
        pattern = re.compile(r"(?<![\w-])" + r"[\s-]*".join(re.escape(w) for w in words) + r"(?![\w-])", re.I)
        for text in texts:
            for match in pattern.finditer(text):
                found.setdefault(match.group(0).lower(), (match.group(0), name))
    return dict(found.values())


def _cast_line(character: dict) -> str:
    about = str(character.get("about", "")).strip()
    return f"- {str(character['name']).strip()}: {character.get('gender', 'unknown')}" + (f"; {about}" if about else "")


def _clean(answer: str, source: str) -> str:
    """Strip the wrappers small models sometimes add around the answer."""
    text = answer.strip()
    text = re.sub(r"^(tradução|translation)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*/no_think\s*$", "", text)  # Qwen3's switch, echoed
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'" and source[:1] not in "\"'":
        text = text[1:-1]
    if "\n" not in source:
        text = " ".join(text.split())
    return text


def _problems(
    source: str,
    translation: str,
    glossary: Glossary | None = None,
    known: Vocabulary | None = None,
    words: Spelling | None = None,
) -> str:
    """Why a translation can't be trusted, or "" if it looks fine."""
    return _check(source, translation, glossary, known, words)[1]


def _rank(
    source: str,
    translation: str,
    glossary: Glossary | None = None,
    known: Vocabulary | None = None,
    words: Spelling | None = None,
) -> tuple[int, bool]:
    """For choosing between two translations: the severity, then, at the
    same severity, a lost number or code is worse than an invented word.
    A batch answer shifted to another item ("The IP address..." given the
    previous item's translation) loses the item's codes; its retry may
    only have an odd word ("subrede"), and on a tie the batch answer won."""
    return _severity(source, translation, glossary, known, words), bool(
        token_problems(source, translation) or lost_abbreviations(source, translation, known)
    )


def _severity(
    source: str,
    translation: str,
    glossary: Glossary | None = None,
    known: Vocabulary | None = None,
    words: Spelling | None = None,
) -> int:
    """0 for a translation that looks fine; higher is worse."""
    return _check(source, translation, glossary, known, words)[0]


# A translation sharing less than this share of the words of the fast
# engine's (folded, their first STEM letters, function words left out) says
# something else. On the comic, the two that did shared 0% and 20%; good
# translations, 29% or more. Checked on references of this many words.
REFERENCE_SHARE = 0.25
REFERENCE_WORDS = 3
STEM = 4


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _content_words(text: str, function_words: frozenset[str]) -> set[str]:
    return {w[:STEM] for w in re.findall(r"[a-z]{3,}", _fold(text)) if w not in function_words}


def shared_share(translation: str, reference: str, function_words: frozenset[str] = frozenset()) -> float:
    """The share of the reference's content words the translation has."""
    expected = _content_words(reference, function_words)
    return len(expected & _content_words(translation, function_words)) / len(expected) if expected else 1.0


def copied_words(source: str, translation: str) -> set[str]:
    """Words of `translation` (three letters or more) written as in
    `source`: names, and words left untranslated."""
    written = {w for w in re.findall(r"[^\W\d_]{3,}", source.lower())}
    return {w for w in re.findall(r"[^\W\d_]{3,}", translation.lower()) if w in written}


def drifted(translation: str, reference: str, function_words: frozenset[str] = frozenset()) -> bool:
    """Whether `translation` says something else than the fast engine's
    `reference` translation of the same text."""
    if len(_content_words(reference, function_words)) < REFERENCE_WORDS:
        return False
    return shared_share(translation, reference, function_words) < REFERENCE_SHARE


# A translation this bad after the retries is translated once more alone
# (3: left untranslated, in part or as an English possessive).
PLAIN_SEVERITY = 3

# A word with the English possessive ("AMERICA'S"): Portuguese has none.
POSSESSIVE = re.compile(r"[^\W\d_][\w-]*['’][sS]\b")


def english_possessives(source: str, translation: str) -> list[str]:
    """Words of the translation with an English "'s" that the source does
    not have as they are: a translated word given the source's ending
    ("AMÉRICA'S", "MUNDO'S" for "AMERICA'S", "WORLD'S")."""
    kept = {m.group(0).lower() for m in POSSESSIVE.finditer(source)}
    return [m.group(0) for m in POSSESSIVE.finditer(translation) if m.group(0).lower() not in kept]


def compound_parts_left(
    source: str, translation: str, kept: re.Pattern | None = None, dictionary: Spelling | None = None
) -> list[str]:
    """Words of the translation that are a part of a hyphenated word of the
    source and no word of the target language ("VILLAIN" of "SUPER-VILLAIN"
    beside "SUPER-HERÓI"): the compound translated in part."""
    if dictionary is None:
        return []
    names = kept.sub(" ", source) if kept else source
    whole = {w.lower() for w in re.findall(r"(?<![\w-])[^\W\d_]+(?:['’][^\W\d_]+)?(?![\w-])", names)}
    parts = {
        part.lower()
        for compound in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)+", names)
        for part in compound.split("-")
    }
    words = re.findall(r"(?<![\w-])[^\W\d_]+(?![\w-])", translation)
    return [w for w in words if w.lower() in parts and w.lower() not in whole and len(w) > 2 and not dictionary.has(w.lower())]


def repeated_words(source: str, translation: str) -> list[str]:
    """A word written twice in a row with only a space between, alone or as
    the end of the next ("HERÓI SUPER-HERÓI", "O O"), when the source does
    not repeat one."""
    def repeats(text: str) -> list[str]:
        # Only spaces between: "NÃO! NÃO!", "VAI, VAI!" repeat on purpose.
        tokens = list(re.finditer(r"[^\W\d_]+(?:-[^\W\d_]+)*", text.lower()))
        return [
            a.group(0)
            for a, b in zip(tokens, tokens[1:])
            if not text[a.end() : b.start()].strip()
            and (a.group(0) == b.group(0) or b.group(0).endswith("-" + a.group(0)) or a.group(0).endswith("-" + b.group(0)))
        ]

    return [] if repeats(source) else repeats(translation)


def added_profanity(source: str, translation: str, languages: LanguagePair = EN_PT_BR) -> list[str]:
    """Profane words of the translation when the source has none: the
    register changed ("BIG DEAL!" -> "QUE BOSTA!")."""
    def found(text: str, words: tuple[str, ...]) -> list[str]:
        return [m.group(0) for w in words for m in re.finditer(rf"\b(?:{w})\b", text, re.IGNORECASE)]

    return [] if found(source, languages.source_profanity) else found(translation, languages.target_profanity)


# A text with this many ordinary words (lowercase, 3+ letters) whose words
# mostly come back unchanged was not translated, in whole or in part.
UNTRANSLATED_WORDS = 6
UNTRANSLATED_SHARE = 0.6


def _ordinary_words(text: str) -> list[str]:
    """Lowercase words of letters only: not names, codes, URLs or commands."""
    words = (token.strip(".,;:!?()[]\"'“”‘’«»") for token in text.split())
    return [w for w in words if len(w) >= 3 and w.isalpha() and w.islower()]


def _untranslated(source: str, translation: str) -> bool:
    """Names, codes and numbers stay as they are; sentences must not."""
    words = _ordinary_words(source)
    if len(words) < UNTRANSLATED_WORDS:
        return False
    kept = set(_ordinary_words(translation))
    return sum(w in kept for w in words) > UNTRANSLATED_SHARE * len(words)


def _check(
    source: str,
    translation: str,
    glossary: Glossary | None,
    known: Vocabulary | None = None,
    dictionary: Spelling | None = None,
) -> tuple[int, str]:
    if not _plausible(source, translation):
        return 4, "empty or unexpected length"
    if _untranslated(source, translation):
        return 3, "left untranslated"
    kept = Glossary(keep=glossary.keep, exact=glossary.exact, quoted=glossary.quoted).pattern() if glossary else None
    if words := unchanged_words(source, translation, kept, known):
        return 3, "left untranslated: " + ", ".join(words)
    if word := leading_source_word(source, translation, kept):
        return 3, f"first word left untranslated: {word}"
    if possessives := english_possessives(source, translation):
        return 3, "English possessive left: " + ", ".join(possessives)
    if profanity := added_profanity(source, translation):
        return 3, "profanity the original does not have: " + ", ".join(profanity)
    if parts := compound_parts_left(source, translation, kept, dictionary):
        return 3, "part of a compound left untranslated: " + ", ".join(parts)
    if repeated := repeated_words(source, translation):
        return 3, "word repeated: " + ", ".join(repeated)
    # Small models sometimes alter numbers ("RS-232" -> "RS-230") or signs
    # ("100Ω" -> "100<tool_call>"): in a technical document that is worse
    # than an awkward sentence.
    if problems := token_problems(source, translation):
        return 2, "; ".join(problems)
    if lost := lost_abbreviations(source, translation, known):
        return 2, "abbreviations dropped or spelled out: " + ", ".join(lost)
    # Invented words ("RETIFOS", "reinsertado"): as wrong as a changed number.
    source_words = known.source_words if known else frozenset()
    if dictionary and (unknown := dictionary.unknown(source, translation, source_words)):
        return 2, "not words of the language: " + ", ".join(unknown)
    if glossary and (terms := glossary.violations(source, translation)):
        return 1, "terminology not followed: " + ", ".join(terms)
    return 0, ""


def _excerpt(text: str, term: str) -> str:
    """A window of `text` around the first occurrence of `term`."""
    position = text.lower().find(term.lower())
    start = max(0, position - EXAMPLE_CHARS // 2)
    return " ".join(text[start : start + EXAMPLE_CHARS].split())


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 1 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def _match_case(source: str, translation: str, dictionary: Spelling | None = None) -> str:
    """Undo case changes the model made, and write titles in sentence case.

    The model copies casing from nearby items: an uppercase translation of
    a text that is not uppercase is rewritten. Titles and labels are written
    in sentence case, as Portuguese does ("Frame Communication" ->
    "Comunicação do quadro", not "Comunicação de Quadro").

    Only translated words change: a word of the source keeps the source's
    spelling ("VistaLINK", "STD-B31", "Paula Cunha", "Single Frequency
    Network"). A translated word is lowercased where the target dictionary
    has it in lowercase and it sits among translated words: beside words
    of the source it belongs to a name left as it is ("MG – Brasil –
    CEP"). Without a dictionary, a mixed-case translation keeps its case.
    """
    if _mostly_upper(source):
        return translation.upper()
    upper = _mostly_upper(translation)
    written = {w.lower(): w for w in re.findall(r"[^\W\d_]+", source)}
    words = list(re.finditer(r"[^\W\d_]+", translation))
    if not words:
        return translation
    title = _title_case(source)
    # Glossary renderings are lowercase ("controlador de quadro"); at the
    # start of a text they follow the source's capital.
    capital = next((c for c in source if c.isalpha()), "").isupper()
    translated = [w.group(0).lower() not in written for w in words]

    def among_translated(index: int) -> bool:
        neighbors = [i for i in (index - 1, index + 1) if 0 <= i < len(words)]
        return bool(neighbors) and all(translated[i] for i in neighbors)

    def cased(index: int) -> str:
        match = words[index]
        word = match.group(0)
        lower = word.lower()
        if not translated[index]:
            return written[lower] if upper else word
        if upper:
            if dictionary is not None and not dictionary.has(lower):
                return lower.capitalize()  # a name ("CALIFÓRNIA")
            return lower
        if (
            title
            and index > 0
            and word[0].isupper()
            and word[1:].islower()
            and dictionary is not None
            and dictionary.has(lower)
            and among_translated(index)
            # After "Figura 1:" a caption starts again.
            and not re.search(r"[:.]\s*$", translation[: match.start()])
        ):
            return lower  # "Comunicação de Quadro": a word of the language, capitalized
        return word

    pieces, end = [], 0
    for index, match in enumerate(words):
        word = cased(index)
        if index == 0 and capital:
            word = word[0].upper() + word[1:]
        pieces += [translation[end : match.start()], word]
        end = match.end()
    return "".join(pieces) + translation[end:]


def _agreed(decisions: list[str]) -> str:
    """The answer given at least twice (in the singular or the plural), or ""."""
    for index, decision in enumerate(decisions):
        if decision and any(_same_word(decision, other) for other in decisions[index + 1 :]):
            return decision
    return ""


def _same_word(a: str, b: str) -> bool:
    """The same word, in the singular or the plural ("placa", "placas";
    "informação", "informações"; "painel", "painéis")."""

    def singular(word: str) -> str:
        word = word.strip().lower()
        for plural, ending in (("ões", "ão"), ("ães", "ão"), ("éis", "el"), ("ais", "al"), ("ns", "m"), ("s", "")):
            if word.endswith(plural):
                return word[: -len(plural)] + ending
        return word

    return singular(a) == singular(b)


def _title_case(text: str) -> bool:
    """Most words of more than 3 letters capitalized ("Frame Communication")."""
    words = [w for w in re.findall(r"[^\W\d_]+", text) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) >= 0.6 * len(words)


# Short texts (headings, labels) vary more in length, but not by a sentence:
# a heading answered with a whole sentence is an answer shifted in the batch.
SHORT_TEXT = 20
SHORT_TEXT_SLACK = 25


def _plausible(source: str, translation: str) -> bool:
    if not translation:
        return False
    if len(source) < SHORT_TEXT:
        return len(translation) <= len(source) * MAX_LENGTH_RATIO + SHORT_TEXT_SLACK
    ratio = len(translation) / max(1, len(source))
    return MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO
