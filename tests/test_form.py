"""The form of short texts survives translation, whatever the engine."""

import pytest

from doc_translator.terms import kept_terms
from doc_translator.translators import Unit, UnitTranslation
from doc_translator.translators.form import restore_form, strip_affixes


def translated(source: str, translation: str) -> str:
    unit, lead, trail = strip_affixes(Unit(source))
    return restore_form(unit.text, UnitTranslation(translation, []), lead, trail).text


@pytest.mark.parametrize(
    ("source", "core", "translation", "expected"),
    [
        ("Transmit –", "Transmit", "Transmitir", "Transmitir –"),
        ("Receive +", "Receive", "Receber", "Receber +"),
        ("Pin #", "Pin", "Pino", "Pino #"),
        ("• Item", "Item", "Item", "• Item"),
    ],
)
def test_signs_standing_apart_are_kept_out_and_put_back(source, core, translation, expected):
    assert strip_affixes(Unit(source))[0].text == core
    assert translated(source, translation) == expected


def test_a_short_text_keeps_its_punctuation_and_case():
    assert translated("no", "Não.") == "não"
    assert translated("Flow Control", "Controle de fluxo.") == "Controle de fluxo"
    assert translated("Status", "Status...") == "Status"
    assert translated("The end.", "O fim.") == "O fim."
    assert translated("Note:", "Nota:") == "Nota:"


def test_names_keep_their_case():
    assert translated("ip address", "IP") == "IP"


def test_sentences_are_left_as_translated():
    long = "the card is installed in the frame"
    assert translated(long, "O cartão é instalado no quadro.") == "O cartão é instalado no quadro."


def test_segments_outside_the_words_keep_the_unit_whole():
    unit = Unit("Transmit –", segments=["–"])
    assert strip_affixes(unit) == (unit, "", "")


def test_na_is_kept_when_the_document_uses_it():
    units = [Unit("N/A"), Unit("Not used (required)")]
    assert "N/A" in kept_terms(units)
    assert "N/A" not in kept_terms([Unit("Nothing here")])
