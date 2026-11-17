"""Numbers, value signs and model tokens: found and repaired the same way
for every engine (cases from the Evertz 7700FC manual)."""

from doc_translator.translators.checks import (
    missing_tokens,
    repair_tokens,
    token_problems,
)


def test_an_altered_number_is_put_back():
    source = "Trap #1: 192.168.1.76 Trap #2: 192.168.8.140"
    translation = "Destino #1: 192.168.1.76 Destino #2: 190.168.8.140"
    assert token_problems(source, translation)
    repaired = repair_tokens(source, translation)
    assert repaired == "Destino #1: 192.168.1.76 Destino #2: 192.168.8.140"
    assert token_problems(source, repaired) == []


def test_a_stray_number_glued_to_another_is_removed():
    source = "a subnet mask configured as 255.255.255.0, this parameter"
    translation = "uma máscara configurada como 25-255.255.255.0, este parâmetro"
    assert repair_tokens(source, translation) == "uma máscara configurada como 255.255.255.0, este parâmetro"


def test_model_tokens_are_removed_and_lost_signs_written_again():
    source = "requires 100Ω STP cabling"
    translation = "requer cabos 100<tool_call> STP"
    assert token_problems(source, translation) == ["symbols lost: Ω", "model tokens: <tool_call>"]
    assert repair_tokens(source, translation) == "requer cabos 100Ω STP"


def test_reordered_and_reformatted_numbers_are_left_alone():
    # Translations reorder dates and use the decimal comma.
    assert token_problems("Sep 27, 2000", "27 de setembro de 2000") == []
    assert token_problems("Use a 2.5 mm screwdriver.", "Use uma chave de 2,5 mm.") == []
    assert repair_tokens("from page 3 to 5", "da página 5 à 3") == "da página 5 à 3"
    # A number the translation spells out is reported, not removed.
    assert repair_tokens("the two criteria", "os 2 critérios") == "os 2 critérios"


def test_the_pipeline_repairs_what_the_engine_left_wrong():
    import pymupdf

    from doc_translator.pipeline import translate_document
    from doc_translator.translators.base import UnitTranslation

    class Garbles:
        def translate_units(self, units):
            return [UnitTranslation(u.text.replace("192.168", "190.168"), list(u.segments)) for u in units]

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Set the gateway to 192.168.8.1 now.", fontsize=11)
    result = translate_document(pymupdf.open("pdf", doc.tobytes()), Garbles())
    assert "192.168.8.1" in result.translations[0].text
    assert result.warnings == []


def test_a_number_split_by_a_hyphen_is_joined_back():
    source = "AT&T 258A or EIA/TIA 258B colour coding shown"
    translation = "codificação de cores AT&T 258A ou EIA/TIA 25-8B mostrada"
    assert repair_tokens(source, translation) == "codificação de cores AT&T 258A ou EIA/TIA 258B mostrada"
    # A range of the source stays a range.
    assert repair_tokens("pins 10-20", "pinos 10-20") == "pinos 10-20"


def test_vulgar_fractions_are_numbers():
    assert token_problems("When both LEDs ½ are enabled", "Quando ambos os LEDs 1/2 estão ativos") == []


def test_a_changed_code_is_a_problem():
    # A letter changed in a code is as wrong as a changed digit.
    assert token_problems("Use the RJ45 jack.", "Use o conector RG45.") == ["codes changed: RJ45"]
    assert token_problems("Image fc_release-2-19-b1 or later", "Imagem fc_release-2-19-b1 ou posterior") == []


def test_spacing_of_a_code_may_change():
    # pt-BR spaces units; the document writes "RJ-45" and "RJ45".
    assert token_problems("a 3V battery, 2.5mm wide", "uma bateria de 3 V, 2,5 mm de largura") == []
    assert token_problems("the RJ45 jack", "o conector RJ-45") == []
    assert token_problems("the 1st slot", "o 1º slot") == []
    assert token_problems("wait up to10 minutes", "aguarde até 10 minutos") == []


def test_missing_tokens_name_the_code_not_its_digits():
    source = "Table 1: Standard RJ45 Wiring Colour Codes"
    assert missing_tokens(source, "Tabela 1: Códigos de cores de fiação padrão") == ["RJ45"]
    assert missing_tokens("pins 2 and 3", "pino 2") == ["3"]


def test_the_ohm_sign_is_the_greek_omega():
    # The manual writes U+2126; a translation writes U+03A9 or loses it.
    source = "10Base-T (10 Mbps) or 100Base-TX (100 Mbps), and 568 100Ω STP cable"
    assert token_problems("568 100Ω STP cable", "cabo STP 568 de 100Ω") == []
    translation = "10Base-T (10 Mbps) ou 100Base-TX (100 Mbps), e cabos STP 568 de 100ósito"
    assert token_problems(source, translation) == ["symbols lost: Ω"]
    assert repair_tokens(source, translation) == (
        "10Base-T (10 Mbps) ou 100Base-TX (100 Mbps), e cabos STP 568 de 100Ω"
    )
