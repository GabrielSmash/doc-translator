"""Context-aware translation with a local LLM served by Ollama.

Unlike sentence-level models, the LLM:
1. reads the document once and builds a glossary (technical terms, product
   names, UI labels) so terminology stays consistent;
2. translates paragraphs in batches, in reading order, seeing the text that
   precedes them;
3. points out, in its own translation, the words matching each styled
   phrase (bold, links...), instead of relying on fuzzy matching.

Answers are requested as JSON (Ollama structured outputs) and validated;
anything that doesn't check out is retried one paragraph at a time.
"""

import json
import logging
import re
import urllib.error
import urllib.request

from ..terms import quoted_terms, recurring_terms
from .base import Glossary, Unit, UnitTranslation

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
CONTEXT_UNITS = 2  # preceding paragraphs shown as context
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
English to Brazilian Portuguese (pt-BR).
Rules:
- Translate the meaning faithfully; never add, omit or explain information.
- Keep product names, model numbers, code, commands, file names, numbers, \
units, URLs and emails exactly as in the original.
- Use the standard Brazilian Portuguese terminology of the document's field. \
Keep in English the technical terms Brazilian professionals keep in English.
- Follow the glossary when one is given.
- Be concise: each translation must fit in roughly the same space as the original.
- Preserve the form: a heading stays a heading, a fragment stays a fragment, \
uppercase stays uppercase."""

# Plain lines rather than a JSON schema: with some documents, schema-constrained
# decoding made the model emit whitespace until the token limit.
TERMS_PROMPT = """These terms recur in a technical document being translated \
from English into Brazilian Portuguese. Decide how each one must be written \
everywhere in the translation, so the terminology stays consistent.
Translate by default. Answer KEEP only for: product and brand names, names of \
screens, menus, buttons or options shown by the software (in English), and \
technical terms Brazilian professionals really use in English. Capital letters \
alone do not make a name.
Answer one line per term, in the same order, exactly as in these examples:
Power Supply => fonte de alimentação
Rear Panel => painel traseiro
Safety Guidelines => diretrizes de segurança
Network Setup => KEEP
Boot Loader => KEEP

Terms, each with an example of use:
{terms}"""
TERM_LINE = re.compile(r"^\s*[-*\d.]*\s*(.+?)\s*=>\s*(.+?)\s*$")

BATCH_PROMPT = """Translate each item of the JSON array below into Brazilian \
Portuguese. Answer with {{"translations": [...]}}: exactly {count} strings, \
in the same order, one per item.

{items}"""

SEGMENTS_PROMPT = """Each item has an English text, its Portuguese translation \
and English phrases taken from the text. For every phrase, copy EXACTLY the \
words of the Portuguese translation that correspond to it, or "" if none.
Answer with {{"items": [...]}}: one array of strings per item, in order.

{items}"""


def _string_array(count: int | None = None) -> dict:
    schema: dict = {"type": "array", "items": {"type": "string"}}
    if count is not None:
        schema |= {"minItems": count, "maxItems": count}
    return schema


class LLMTranslator:
    def __init__(
        self, model: str, host: str = DEFAULT_HOST, glossary: Glossary | None = None
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.user_glossary = glossary or Glossary()
        self._cache: dict[str, str] = {}
        # Ollama's memory estimate is conservative and leaves layers on the
        # CPU even when they fit: asking for every layer on the GPU made
        # generation ~1.75x faster on a 4 GB card. Dropped if it runs out of
        # memory.
        self._all_layers_on_gpu = True
        self._check_model()

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        glossary = self._build_glossary(units).merged(self.user_glossary)
        texts = [u.text for u in units]

        # Translate each distinct text once (headers and footers repeat on
        # every page), batched in reading order with the preceding text.
        first_seen: dict[str, int] = {}
        for index, text in enumerate(texts):
            if any(c.isalpha() for c in text):
                first_seen.setdefault(text, index)
        pending = list(first_seen)
        done = 0
        for batch in _batches(pending):
            start = first_seen[batch[0]]
            context = texts[max(0, start - CONTEXT_UNITS) : start]
            for text, translation in zip(batch, self._translate_batch(batch, context, glossary)):
                self._cache[text] = translation
            done += len(batch)
            log.info("Translated %d/%d paragraphs", done, len(pending))

        translations = [self._cache.get(text, text) for text in texts]
        segments = self._locate_all(units, translations)
        return [UnitTranslation(t, s) for t, s in zip(translations, segments)]

    # -- glossary ------------------------------------------------------------

    def _build_glossary(self, units: list[Unit]) -> Glossary:
        """Ask the model to decide, once, how each recurring term is rendered."""
        candidates = recurring_terms([u.text for u in units])
        if not candidates:
            return Glossary()
        listing = "\n".join(
            f'- {term} (e.g. "{_excerpt(example, term)}")' for term, example in candidates.items()
        )
        try:
            answer = self._chat(
                [{"role": "user", "content": TERMS_PROMPT.format(terms=listing)}],
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
        prompt = self._prompt(BATCH_PROMPT.format(count=len(texts), items=items), texts, context, glossary)
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
            translation = _match_case(text, _clean(translation, text))
            if _problems(text, translation, glossary):
                # Context for a lone retry: whatever preceded it in the batch.
                previous = (context + texts[:index])[-CONTEXT_UNITS:]
                translation = self._translate_one(text, previous, glossary)
            results.append(translation)
        return results

    def _translate_one(self, text: str, context: list[str], glossary: Glossary) -> str:
        prompt = self._prompt(f"Translate this text:\n{text}", [text], context, glossary)
        if terms := glossary.relevant(text):
            prompt[0]["content"] += "\n- Mandatory terminology:\n" + terms
        if numbers := NUMBER.findall(text):
            # In the system message: put next to the text, small models
            # translate the reminder along with it.
            prompt[0]["content"] += "\n- The translation must contain these numbers unchanged: " + ", ".join(
                dict.fromkeys(numbers)
            )
        translation = _clean(self._chat(prompt, max_tokens=64 + len(text)), text)
        translation = _match_case(text, translation)
        if problems := _problems(text, translation, glossary):
            log.warning("Check translation of %r (%s): %r", text[:50], problems, translation[:80])
        return translation or text

    def _prompt(
        self, task: str, texts: list[str], context: list[str], glossary: Glossary
    ) -> list[dict]:
        parts = []
        if terms := glossary.relevant("\n".join(texts)):
            parts.append(f"Glossary:\n{terms}")
        if context:
            parts.append("Preceding text (context only, do not translate):\n" + "\n".join(context))
        parts.append(task)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    # -- styled phrases ------------------------------------------------------

    def _locate_all(self, units: list[Unit], translations: list[str]) -> list[list[str]]:
        located = [[] for _ in units]
        styled = [i for i, unit in enumerate(units) if unit.segments]
        for start in range(0, len(styled), BATCH_UNITS):
            batch = styled[start : start + BATCH_UNITS]
            answers = self._locate_batch([units[i] for i in batch], [translations[i] for i in batch])
            for i, answer in zip(batch, answers):
                located[i] = answer
        return located

    def _locate_batch(self, units: list[Unit], translations: list[str]) -> list[list[str]]:
        items = json.dumps(
            [
                {"english": u.text, "portuguese": t, "phrases": u.segments}
                for u, t in zip(units, translations)
            ],
            ensure_ascii=False,
            indent=0,
        )
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": _string_array(),
                    "minItems": len(units),
                    "maxItems": len(units),
                }
            },
            "required": ["items"],
        }
        prompt = [{"role": "user", "content": SEGMENTS_PROMPT.format(items=items)}]
        size = sum(len(s) for u in units for s in u.segments)
        try:
            answers = json.loads(self._chat(prompt, schema=schema, max_tokens=128 + 2 * size))["items"]
        except (ValueError, KeyError, LLMError):
            answers = []

        results = []
        for index, (unit, translation) in enumerate(zip(units, translations)):
            matches = answers[index] if index < len(answers) else []
            if len(matches) != len(unit.segments):
                matches = [""] * len(unit.segments)
            # Only exact substrings are usable; anything else falls back to
            # the fuzzy matching done by the caller (from the English phrase).
            results.append(
                [m if m and m in translation else seg for m, seg in zip(matches, unit.segments)]
            )
        return results

    # -- Ollama --------------------------------------------------------------

    def _check_model(self) -> None:
        try:
            tags = json.loads(self._request("/api/tags", None))
        except LLMError as error:
            raise LLMError(f"Ollama is not reachable at {self.host}: {error}") from error
        names = {m["name"] for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
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


NUMBER = re.compile(r"\d+(?:[.,/:]\d+)*")
# Portuguese words left lowercase in title case.
MINOR_WORDS = {"a", "o", "as", "os", "e", "de", "da", "do", "das", "dos", "em", "no", "na",
               "nos", "nas", "para", "por", "com", "ao", "aos", "à", "às", "um", "uma"}


def _numbers(text: str) -> set[str]:
    """Digits of each number, ignoring separators: "2.5" and "2,5" (pt-BR
    decimal comma), "1,000" and "1.000" are the same number."""
    return {re.sub(r"[.,]", "", n) for n in NUMBER.findall(text)}


def _problems(source: str, translation: str, glossary: Glossary | None = None) -> str:
    """Why a translation can't be trusted, or "" if it looks fine."""
    if not _plausible(source, translation):
        return "empty or unexpected length"
    # Small models sometimes alter numbers ("RS-232" -> "RS-230"): in a
    # technical document that is worse than an awkward sentence.
    if missing := _numbers(source) - _numbers(translation):
        return "numbers changed: " + ", ".join(sorted(missing))
    if glossary and (terms := glossary.violations(source, translation)):
        return "terminology not followed: " + ", ".join(terms)
    return ""


def _excerpt(text: str, term: str) -> str:
    """A window of `text` around the first occurrence of `term`."""
    position = text.lower().find(term.lower())
    start = max(0, position - EXAMPLE_CHARS // 2)
    return " ".join(text[start : start + EXAMPLE_CHARS].split())


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 1 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def _match_case(source: str, translation: str) -> str:
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
        if first or (title_case and lower not in MINOR_WORDS):
            return lower.capitalize()
        return lower

    first_word = re.search(r"[^\W\d_]+", translation)
    first_word_start = first_word.start() if first_word else -1
    return re.sub(r"\w+", fix, translation)


def _plausible(source: str, translation: str) -> bool:
    if not translation:
        return False
    ratio = len(translation) / max(1, len(source))
    return MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO or len(source) < 20
