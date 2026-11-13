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

import json
import logging
import re
import urllib.error
import urllib.request

from ..languages import EN_PT_BR, LanguagePair
from ..terms import kept_terms, quoted_terms, recurring_terms
from .base import Glossary, Unit, UnitTranslation
from .cache import glossary_id
from .checks import NUMBER, lost_numbers

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
CONTEXT_UNITS = 3  # preceding paragraphs shown as context
# How much of the document the model reads to describe it: its headings and
# its opening text, up to this many characters.
BRIEF_CHARS = 3000
EXAMPLE_CHARS = 160  # usage example shown with each glossary term
# Batches trade prompt overhead (instructions/context repeated per call)
# against the cost of a bad answer, which sends the whole batch to retry.
BATCH_CHARS = 1500
BATCH_UNITS = 10
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
TERM_LINE = re.compile(r"^\s*[-*\d.]*\s*(.+?)\s*=>\s*(.+?)\s*$")

# Written once per document and shown with every batch: a paragraph alone
# does not say whether "Setup" is a menu or "frame" a video frame.
BRIEF_PROMPT = """Below are the headings and the opening text of a document \
that will be translated. In at most 5 short lines, in {source}, describe: what \
the document is and its field; who it is written for; the names that must stay \
as they are (products, and screens, menus, buttons or fields of the software); \
the meaning that ambiguous words have in it. No preamble.

{excerpt}"""

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
    ) -> None:
        self.model = model
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
        self._brief = ""
        self._sections: dict[str, str] = {}
        digest = self._check_model()
        # The digest changes when `ollama pull` brings a new version.
        self.cache_id = f"llm|{model}|{digest}|{languages.target_code}|{glossary_id(glossary)}"

    def keep_progress(self, store) -> None:
        """Save each translated batch to `store` (a cache namespace) and
        skip the paragraphs it already holds."""
        self._progress = store

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        glossary = self._build_glossary(units)
        # Names of screens and fields, set in italics by the document, and
        # abbreviations used as they are ("N/A").
        for label in kept_terms(units, self.languages):
            glossary.terms.pop(label, None)
            if label not in glossary.keep:
                glossary.keep.append(label)
        glossary = glossary.merged(self.user_glossary)
        self._brief = self._describe(units)
        for unit in units:
            self._sections.setdefault(unit.text, unit.section)
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

    def _translate_all(self, texts: list[str], glossary: Glossary, with_context: bool) -> None:
        """Translate each distinct text once into self._cache (headers and
        footers repeat on every page), batched in reading order, with the
        preceding text as context."""
        first_seen: dict[str, int] = {}
        for index, text in enumerate(texts):
            if any(c.isalpha() for c in text):
                first_seen.setdefault(text, index)
        pending = list(first_seen)
        if self._progress is not None:
            resumed = self._progress.get_many([t for t in pending if t not in self._cache])
            self._cache.update(resumed)
            if resumed:
                log.info("Resuming: %d texts translated by an earlier run", len(resumed))
        done = sum(text in self._cache for text in pending)
        pending = [text for text in pending if text not in self._cache]
        for batch in _batches(pending):
            start = first_seen[batch[0]]
            context = texts[max(0, start - CONTEXT_UNITS) : start] if with_context else []
            translated = dict(zip(batch, self._translate_batch(batch, context, glossary)))
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
        try:
            brief = self._chat(
                [{"role": "user", "content": self._format(BRIEF_PROMPT, excerpt=excerpt)}],
                max_tokens=200,
            )
        except LLMError as error:
            log.warning("Could not describe the document (%s); continuing without it", error)
            return ""
        log.info("Document: %s", " ".join(brief.split()))
        return brief.strip()

    # -- glossary ------------------------------------------------------------

    def _build_glossary(self, units: list[Unit]) -> Glossary:
        """Ask the model to decide, once, how each recurring term is rendered."""
        candidates = recurring_terms([u.text for u in units], self.languages.source_stopwords)
        if not candidates:
            return Glossary()
        listing = "\n".join(
            f'- {term} (e.g. "{_excerpt(example, term)}")' for term, example in candidates.items()
        )
        try:
            answer = self._chat(
                [{"role": "user", "content": self._format(TERMS_PROMPT, terms=listing)}],
                max_tokens=64 + 24 * len(candidates),
            )
        except LLMError as error:
            log.warning("Could not decide the terminology (%s); continuing without it", error)
            return Glossary()

        by_name = {term.lower(): term for term in candidates}
        glossary = Glossary()
        for line in answer.splitlines():
            if not (match := TERM_LINE.match(line)):
                continue
            term = by_name.get(match.group(1).strip("\"'`* ").lower())
            decision = match.group(2).strip("\"'`* ")
            if not term or not decision:
                continue  # only terms we asked about
            if decision.upper() == "KEEP" or decision.lower() == term.lower():
                glossary.keep.append(term)
            else:
                glossary.terms[term] = decision
        # The model can't reliably tell UI labels from common terms; the
        # document can: labels are the terms it puts between quotes.
        for term in quoted_terms([u.text for u in units], list(candidates)):
            glossary.terms.pop(term, None)
            if term not in glossary.keep:
                glossary.keep.append(term)
        log.info("Terminology: keep %s; translate %s", glossary.keep, glossary.terms)
        return glossary

    # -- translation ---------------------------------------------------------

    def _translate_batch(self, texts: list[str], context: list[str], glossary: Glossary) -> list[str]:
        items = json.dumps(texts, ensure_ascii=False, indent=0)
        task = self._format(BATCH_PROMPT, count=len(texts), items=items)
        prompt = self._prompt(task, texts, context, glossary)
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
            if _problems(text, translation, glossary):
                # Context for a lone retry: whatever preceded it in the batch.
                previous = (context + texts[:index])[-CONTEXT_UNITS:]
                retry = self._translate_one(text, previous, glossary)
                # The retry can be worse (left in English where the batch
                # only missed a glossary term): keep the lesser problem.
                translation = min(
                    [translation, retry], key=lambda t: _severity(text, t, glossary)
                )
                if problems := _problems(text, translation, glossary):
                    log.warning("Check translation of %r (%s): %r", text[:50], problems, translation[:80])
            results.append(translation or text)
        return results

    def _translate_one(self, text: str, context: list[str], glossary: Glossary) -> str:
        prompt = self._prompt(f"Translate this text:\n{text}", [text], context, glossary)
        if terms := glossary.relevant(text, self.languages.source):
            prompt[0]["content"] += "\n- Mandatory terminology:\n" + terms
        if numbers := NUMBER.findall(text):
            # In the system message: put next to the text, small models
            # translate the reminder along with it.
            prompt[0]["content"] += "\n- The translation must contain these numbers unchanged: " + ", ".join(
                dict.fromkeys(numbers)
            )
        translation = _clean(self._chat(prompt, max_tokens=64 + len(text)), text)
        return self._match_case(text, translation)

    def _prompt(
        self, task: str, texts: list[str], context: list[str], glossary: Glossary
    ) -> list[dict]:
        parts = []
        if self._brief:
            parts.append(f"About the document (context only):\n{self._brief}")
        if terms := glossary.relevant("\n".join(texts), self.languages.source):
            parts.append(f"Glossary:\n{terms}")
        section = self._sections.get(texts[0], "") if texts else ""
        if section and section not in texts:
            parts.append(f"Section: {section}")
        if context:
            parts.append("Preceding text (context only, do not translate):\n" + "\n".join(context))
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
        return _match_case(source, translation, self.languages.target_minor_words)

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


def _batches(texts: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    size = 0
    for text in texts:
        if not batches or len(batches[-1]) >= BATCH_UNITS or size + len(text) > BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(text)
        size += len(text)
    return batches


def _clean(answer: str, source: str) -> str:
    """Strip the wrappers small models sometimes add around the answer."""
    text = answer.strip()
    text = re.sub(r"^(tradução|translation)\s*:\s*", "", text, flags=re.IGNORECASE)
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'" and source[:1] not in "\"'":
        text = text[1:-1]
    if "\n" not in source:
        text = " ".join(text.split())
    return text


def _problems(source: str, translation: str, glossary: Glossary | None = None) -> str:
    """Why a translation can't be trusted, or "" if it looks fine."""
    return _check(source, translation, glossary)[1]


def _severity(source: str, translation: str, glossary: Glossary | None = None) -> int:
    """0 for a translation that looks fine; higher is worse."""
    return _check(source, translation, glossary)[0]


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


def _check(source: str, translation: str, glossary: Glossary | None) -> tuple[int, str]:
    if not _plausible(source, translation):
        return 4, "empty or unexpected length"
    if _untranslated(source, translation):
        return 3, "left untranslated"
    # Small models sometimes alter numbers ("RS-232" -> "RS-230"): in a
    # technical document that is worse than an awkward sentence.
    if missing := lost_numbers(source, translation):
        return 2, "numbers changed: " + ", ".join(sorted(missing))
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


def _match_case(
    source: str, translation: str, minor_words: frozenset[str] = EN_PT_BR.target_minor_words
) -> str:
    """Undo case changes the model made (it copies casing from nearby items).

    An uppercase translation of a non-uppercase source becomes title case or
    sentence case, as the source; acronyms found in the source stay uppercase.
    """
    if _mostly_upper(source):
        return translation.upper()
    if not _mostly_upper(translation):
        # Glossary renderings are lowercase ("controlador de quadro"); at the
        # start of a text they must follow the source's capital.
        first_source = next((c for c in source if c.isalpha()), "")
        first = next((i for i, c in enumerate(translation) if c.isalpha()), None)
        if first is not None and first_source.isupper() and translation[first].islower():
            return translation[:first] + translation[first].upper() + translation[first + 1 :]
        return translation
    acronyms = {w for w in re.findall(r"\w+", source) if len(w) > 1 and w.isupper()}
    source_words = [w for w in re.findall(r"[^\W\d_]+", source) if len(w) > 3]
    title_case = bool(source_words) and sum(w[0].isupper() for w in source_words) >= 0.6 * len(
        source_words
    )

    def fix(match: re.Match) -> str:
        word = match.group(0)
        if word in acronyms or not word.isalpha():
            return word
        lower = word.lower()
        first = match.start() == first_word_start
        if first or (title_case and lower not in minor_words):
            return lower.capitalize()
        return lower

    first_word = re.search(r"[^\W\d_]+", translation)
    first_word_start = first_word.start() if first_word else -1
    return re.sub(r"\w+", fix, translation)


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
