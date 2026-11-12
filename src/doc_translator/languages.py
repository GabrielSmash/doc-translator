"""Everything that depends on the source and target languages.

Only English to Brazilian Portuguese exists. Keeping its specifics here
(names, codes, model, prompt examples, word lists) means a second pair is a
new value of LanguagePair, not edits across the engines.
"""

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
    # Target words left lowercase in title case.
    target_minor_words: frozenset[str]


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
    }),
    target_minor_words=frozenset({
        "a", "o", "as", "os", "e", "de", "da", "do", "das", "dos", "em", "no", "na",
        "nos", "nas", "para", "por", "com", "ao", "aos", "à", "às", "um", "uma",
    }),
)
