"""Everything that depends on the source and target languages.

Only English to Brazilian Portuguese exists. Keeping its specifics here
(names, codes, model, prompt examples, word lists) means a second pair is a
new value of LanguagePair, not edits across the engines.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguagePair:
    source: str  # language names as written in the LLM prompts
    target: str
    target_code: str  # BCP 47: output file suffix and cache key
    target_speakers: str  # "Brazilian professionals keep these terms in English"
    # Opus-MT: the CTranslate2 conversion on Hugging Face and the token
    # selecting the target variant in the multilingual model.
    opus_model: str
    opus_token: str
    # Lines "<source term> => <decision>" showing the LLM how to answer when
    # it decides the terminology; KEEP leaves a term in the source language.
    term_examples: str
    # Source words that never start or end a recurring term ("the", "of").
    source_stopwords: frozenset[str]
    # Source words before a noun ("the", "each"): a word after one names a
    # thing of the document ("the card", "a jumper").
    source_determiners: tuple[str, ...]
    # Target words left lowercase in title case.
    target_minor_words: frozenset[str]
    # Source abbreviations readers of the target language use as they are,
    # kept by both engines: a table cell "N/A" spelled out as "NÃO
    # APLICÁVEL" no longer fits its column.
    kept_abbreviations: frozenset[str] = frozenset()
    # Month abbreviations of dates ("Aug 01" -> "Ago 01"): written by a
    # table, not by the engines, which leave them in a table cell as they are.
    month_abbreviations: tuple[tuple[str, str], ...] = ()
    # Words that start source sentences and are not words of the target
    # language: one of them before a translated rest was left untranslated
    # ("The 7700FC controlador...", "To atualizar..."). "Use", "A" or "Note"
    # start sentences in both languages, so they are not here.
    source_only_starts: frozenset[str] = frozenset()

    def localize_dates(self, source: str, translation: str) -> str:
        """`translation` with the source's month abbreviations, still written
        as in the source before a day or a year, in the target language."""
        for month, target in self.month_abbreviations:
            for written in {month, month.upper()}:
                if not re.search(rf"\b{written}\b", source):
                    continue
                local = target.upper() if written.isupper() else target
                translation = re.sub(rf"\b{written}\b(?=\.?\s*\d)", local, translation)
        return translation


EN_PT_BR = LanguagePair(
    source="English",
    target="Brazilian Portuguese",
    target_code="pt-BR",
    target_speakers="Brazilian",
    # Conversion of Helsinki-NLP/opus-mt-tc-big-en-pt published by this
    # project (see scripts/convert_model.py).
    opus_model="gabrielsmash/opus-mt-tc-big-en-pt-ct2-int8",
    opus_token=">>pob<<",
    term_examples="""Power Supply => fonte de alimentação
Rear Panel => painel traseiro
Safety Guidelines => diretrizes de segurança
Network Setup => KEEP
Boot Loader => KEEP""",
    source_stopwords=frozenset({
        "a", "an", "and", "as", "at", "by", "for", "from", "if", "in", "into", "is",
        "it", "of", "on", "or", "the", "this", "to", "when", "with", "you", "your",
        "are", "was", "be", "must", "can", "will", "should", "may", "has",
        "have", "not", "that", "which", "so", "then", "using", "there",
    }),
    source_determiners=(
        "the", "a", "an", "this", "these", "those", "each", "every", "its", "your",
        "their", "any", "both", "per",
    ),
    target_minor_words=frozenset({
        "a", "o", "as", "os", "e", "de", "da", "do", "das", "dos", "em", "no", "na",
        "nos", "nas", "para", "por", "com", "ao", "aos", "à", "às", "um", "uma",
    }),
    kept_abbreviations=frozenset({"N/A"}),
    source_only_starts=frozenset({
        "the", "to", "press", "if", "when", "this", "these", "those", "for", "with",
        "click", "select", "enter", "type", "connect", "make", "upon", "after",
        "before", "once", "an", "and", "or", "of", "in", "on", "it", "you", "your",
        "all", "each", "then", "while", "where", "which", "there", "only", "both",
    }),
    month_abbreviations=(
        ("Feb", "Fev"), ("Apr", "Abr"), ("May", "Mai"), ("Aug", "Ago"),
        ("Sep", "Set"), ("Sept", "Set"), ("Oct", "Out"), ("Dec", "Dez"),
    ),
)
