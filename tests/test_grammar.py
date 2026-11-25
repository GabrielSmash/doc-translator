"""LanguageTool on real translations (ADR 0008). Runs only with Java and the
server already in the cache: the suite does not download 250 MB."""

import os
import shutil
from pathlib import Path

import pytest

from doc_translator.languages import EN_PT_BR
from doc_translator.translators.grammar import Grammar, sentence_case

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "doc-translator" / "languagetool"
needs_server = pytest.mark.skipif(
    shutil.which("java") is None or not (CACHE / "LanguageTool-6.6" / "languagetool-server.jar").is_file(),
    reason="LanguageTool not installed",
)


def test_capitals_are_checked_in_sentence_case_with_the_same_offsets():
    text = "NA ESTA ÉPICA HISTÓRIA! VEJA O HUMAN TORCH!"
    assert sentence_case(text) == "Na esta épica história! Veja o human torch!"
    assert len(sentence_case(text)) == len(text)
    assert sentence_case("Already mixed Case.") == "Already mixed Case."


@pytest.fixture(scope="module")
def grammar():
    checker = Grammar(EN_PT_BR)
    yield checker
    checker.close()


@needs_server
def test_agreement_errors_of_the_comic_are_found_and_corrected(grammar):
    # Errors the 8b model made in the comic, in capitals as lettered.
    wrong = {
        "A SUPER-HERÓI MAIS EMOCIONANTE DO MUNDO!": "O SUPER-HERÓI MAIS EMOCIONANTE DO MUNDO!",
        "NA ESTA ÉPICA HISTÓRIA, VEJA O HUMAN TORCH!": "NESTA ÉPICA HISTÓRIA, VEJA O HUMAN TORCH!",
        "MEUS BRAÇOS NÃO É SUFICIENTE!": "MEUS BRAÇOS NÃO SÃO SUFICIENTE!",
        # LanguageTool's own rules miss these; ours (grammar_pt.xml) do not.
        "A MAIS INTERESSANTE SUPER-HERÓI DA AMÉRICA!": "O MAIS INTERESSANTE SUPER-HERÓI DA AMÉRICA!",
        "A MELHOR SUPER-HERÓI DO MUNDO!": "O MELHOR SUPER-HERÓI DO MUNDO!",
        "ELA É O MAIS FORTE MULHER!": "ELA É A MAIS FORTE MULHER!",
    }
    for text, right in wrong.items():
        assert grammar.errors(text), text
        assert grammar.corrected(text) == right
    # Right as they are: an adjective of either gender, set phrases.
    for text in ("A MELHOR AMIGA", "O MAIOR INIMIGO", "A LONGO PRAZO", "A TODO CUSTO", "VOU A PÉ", "UMA BOA IDEIA"):
        assert grammar.errors(text) == [], text
    # Names, capitals and spelling are not its business here.
    assert grammar.errors("VOCÊ É IMPOTENTE, SPIDER-MAN! O PODER DO DOCTOR OCTOPUS É MAIOR!") == []
