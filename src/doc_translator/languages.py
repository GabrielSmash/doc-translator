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
    source_code: str
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
    # Hunspell dictionary of the target language (.dic and .aff), as (URL,
    # SHA-256): words a translation has and the language does not were
    # invented by the model ("RETIFOS"). Downloaded on first use.
    target_dictionary: tuple[tuple[str, str], ...] = ()
    # The same for the source language: a word it has in lowercase ("blue")
    # is a word, even where a document only writes it capitalized (a table
    # cell "Blue"), and left untranslated it was not kept as a name.
    source_dictionary: tuple[tuple[str, str], ...] = ()
    # Words of the source language from the most to the least used, one per
    # line, gzipped, as (URL, SHA-256): lettering read by OCR loses its
    # spaces, and "MYNAME" is "MY NAME", not "MYNA ME" (ADR 0008).
    source_word_ranks: tuple[tuple[str, str], ...] = ()
    # Abbreviations the target language writes its own way ("USA" ->
    # "EUA"): not dropped when the translation has them so.
    translated_abbreviations: tuple[tuple[str, str], ...] = ()

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


VERO_COMMIT = "5cf23af85b47be6e398add826da1a02175be4238"

EN_PT_BR = LanguagePair(
    source="English",
    target="Brazilian Portuguese",
    source_code="en-US",
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
    # LibreOffice's VERO dictionary, LGPLv3 or MPL 2.0, by Raimundo Moura
    # and the LibreOffice community; pinned to a commit.
    target_dictionary=tuple(
        (f"https://raw.githubusercontent.com/LibreOffice/dictionaries/{VERO_COMMIT}/pt_BR/pt_BR.{ext}", sha256)
        for ext, sha256 in (
            ("dic", "a38bfb26b68ece2834e79fe83e48d5792652970ace12db89d1b9674bf9933183"),
            ("aff", "21d8ad2a769a60e17e2b5ea4ef11d4d593a58b9e2a82d642ef82d6a4c5523865"),
        )
    ),
    # SCOWL's en_US dictionary (MIT-like license), from the same commit.
    source_dictionary=tuple(
        (f"https://raw.githubusercontent.com/LibreOffice/dictionaries/{VERO_COMMIT}/en/en_US.{ext}", sha256)
        for ext, sha256 in (
            ("dic", "f0b1a234bd178bdd01875b2a392a9647f888b8fe879f79c52aae62c2759b3647"),
            ("aff", "c7a8c4d08c29d237880844b1623099f59092602f189be38ce3912e457ff38bc1"),
        )
    ),
    # wordninja's list (MIT, by Derek Anderson): 126k English words by
    # frequency in Wikipedia; pinned to a commit.
    source_word_ranks=(
        (
            (
                "https://raw.githubusercontent.com/keredson/wordninja/0421d148cd3d88e4f075aa9c703006f31809e9e5/"
                "wordninja/wordninja_words.txt.gz"
            ),
            "5b3c6c418fea7188b3919c47829ff94bc010f8336c74cbb929cc20d0c9d9e901",
        ),
    ),
    translated_abbreviations=(
        ("USA", "EUA"), ("UN", "ONU"), ("EU", "UE"), ("UK", "RU"), ("AC", "CA"), ("DC", "CC"),
    ),
    month_abbreviations=(
        ("Feb", "Fev"), ("Apr", "Abr"), ("May", "Mai"), ("Aug", "Ago"),
        ("Sep", "Set"), ("Sept", "Set"), ("Oct", "Out"), ("Dec", "Dez"),
    ),
)
