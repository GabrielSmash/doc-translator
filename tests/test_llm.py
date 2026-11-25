import json

from doc_translator.languages import EN_PT_BR
from doc_translator.translators.base import Glossary, Unit
from doc_translator.translators.llm import LLMTranslator, _batches, _clean, _plausible


class FakeLLM(LLMTranslator):
    """LLMTranslator with canned Ollama answers, in call order."""

    def __init__(self, answers: list[str], glossary: Glossary | None = None) -> None:
        self.model = "fake"
        self.languages = EN_PT_BR
        self.user_glossary = glossary or Glossary()
        self._cache = {}
        self._progress = None
        self.cache_id = "llm|fake"
        self.user_context = ""
        self._all_layers_on_gpu = False
        self._brief = ""
        self._names = ""
        self._kept_words = set()
        self._sections = {}
        self._tables = {}
        self._table_cells = {}
        self._context_units = 3
        self._following_units = 0
        self._known = None
        self._spelling = None
        self.answers = iter(answers)
        self.prompts: list[list[dict]] = []
        self.brief = None  # None: skip the description call
        self.spelling = None  # no dictionary download in tests

    def _load_spelling(self):
        return self.spelling

    def _load_source_spelling(self):
        return None

    def _describe(self, units):
        return super()._describe(units) if self.brief is not None else ""

    def _chat(self, messages, schema=None, max_tokens=1024):
        self.prompts.append(messages)
        return next(self.answers)


def test_clean_strips_wrappers():
    assert _clean('Tradução: "Visão geral"', "Overview") == "Visão geral"
    assert _clean("Linha um\nlinha dois", "Line one line two") == "Linha um linha dois"


def test_plausible_rejects_explanations_and_empty_answers():
    source = "Connect the cable to the serial port."
    assert _plausible(source, "Conecte o cabo à porta serial.")
    assert not _plausible(source, "")
    assert not _plausible(source, "Claro! " + "Aqui está a tradução detalhada. " * 10)
    # A heading answered with the next paragraph's translation.
    assert _plausible("IP Address", "Endereço IP")
    assert not _plausible("IP Address", "Este controle define o endereço IP único do 7700FC dentro da rede.")


def test_batches_respect_size_limits():
    texts = ["x" * 600] * 4 + ["y"] * 12
    # Short texts fill the previous batch up to BATCH_UNITS.
    assert [len(b) for b in _batches(texts)] == [2, 10, 4]


def test_units_are_batched_with_glossary_and_segments():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Visão geral", "A placa fornece os principais resultados via VistaLINK."]}),
            json.dumps({"translations": ["principais resultados"]}),
        ],
        glossary=Glossary(terms={"Overview": "Visão geral"}),
    )
    units = [
        Unit("Overview"),
        Unit("The card provides the main results via VistaLINK.", ["main results"]),
        Unit("Overview"),  # repeated header: translated once
        Unit("12/2026"),  # no letters: kept as-is
    ]
    results = llm.translate_units(units)

    assert [r.text for r in results] == [
        "Visão geral",
        "A placa fornece os principais resultados via VistaLINK.",
        "Visão geral",
        "12/2026",
    ]
    assert results[1].segments == ["principais resultados"]
    batch_prompt = llm.prompts[0][1]["content"]
    assert '"Overview" -> "Visão geral"' in batch_prompt  # user glossary used
    # Styled phrases go in their own batch, after the paragraphs, without
    # context; no terminology call (no recurring terms).
    assert '"main results"' not in batch_prompt
    assert "Preceding text" not in llm.prompts[1][1]["content"]
    assert len(llm.prompts) == 2


def test_bad_batch_answer_falls_back_to_one_by_one():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["só uma"]}),  # wrong count
            "Visão geral",
            "Destaques",
        ]
    )
    results = llm.translate_units([Unit("Overview"), Unit("Highlights")])
    assert [r.text for r in results] == ["Visão geral", "Destaques"]


def test_changed_numbers_are_detected():
    from doc_translator.translators.llm import _problems

    assert _problems("Provides a RS-232 serial port.", "Fornece uma porta serial RS-232.") == ""
    assert "232" in _problems("Provides a RS-232 serial port.", "Fornece uma porta serial RS-230.")
    # pt-BR decimal comma is not a changed number.
    assert _problems("Use a 2.5 mm screwdriver.", "Use uma chave de fenda de 2,5 mm.") == ""


def test_case_copied_from_neighbors_is_undone():
    from doc_translator.translators.llm import _match_case

    assert (
        _match_case("Downloading the 7700FC Frame Controller Image", "BAIXANDO A IMAGEM DO 7700FC")
        == "Baixando a imagem do 7700FC"
    )
    assert _match_case("Viewing the LED status", "VISUALIZANDO O STATUS DO LED") == (
        "Visualizando o status do LED"
    )
    assert _match_case("OVERVIEW", "Visão geral") == "VISÃO GERAL"
    assert _match_case("7700FC Frame Controller", "controlador de quadro 7700FC") == (
        "Controlador de quadro 7700FC"
    )


def test_titles_are_written_in_sentence_case():
    from doc_translator.translators.llm import _match_case

    portuguese = Lowercase({"comunicação", "de", "da", "do", "a", "quadro", "universidade", "porta", "atualização"})
    assert _match_case("Frame Communication", "Comunicação de Quadro", portuguese) == "Comunicação de quadro"
    # Names keep their capital: from the source, or only capitalized in the
    # dictionary.
    assert _match_case("Ethernet Port", "Porta Ethernet", portuguese) == "Porta Ethernet"
    assert _match_case("University of California", "UNIVERSIDADE DA CALIFÓRNIA", portuguese) == (
        "Universidade da Califórnia"
    )
    assert _match_case("Figure 1: Block Diagram", "Figura 1: Diagrama de Blocos", Lowercase({"diagrama", "blocos", "de"})) == (
        "Figura 1: Diagrama de blocos"
    )
    # Words of the source keep its spelling: names and codes left as they are.
    address = Lowercase({"paula", "cunha", "brasil", "santa", "rita", "do"})
    assert _match_case("Avenida Frederico de Paula Cunha", "Avenida Frederico de Paula Cunha", address) == (
        "Avenida Frederico de Paula Cunha"
    )
    assert _match_case("Santa Rita – MG – Brazil – CEP", "Santa Rita – MG – Brasil – CEP", address) == (
        "Santa Rita – MG – Brasil – CEP"
    )
    assert _match_case("STD-B31 and ABNT NBR", "STD-B31 E ABNT NBR", Lowercase({"e", "b"})) == "STD-B31 e ABNT NBR"
    single = Lowercase({"operação", "em", "single"})
    assert _match_case("Operation in SFN (Single Frequency Network):", "Operação em SFN (Single Frequency Network):", single) == (
        "Operação em SFN (Single Frequency Network):"
    )
    # Sentences are left as written.
    assert _match_case("the port of the frame", "a Porta do quadro", portuguese) == "a Porta do quadro"
    assert _match_case("no", "nenhum", portuguese) == "nenhum"


def test_number_mismatch_triggers_single_retry():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Porta serial RS-230"]}),
            "Porta serial RS-232",
        ]
    )
    results = llm.translate_units([Unit("RS-232 serial port")])
    assert results[0].text == "Porta serial RS-232"
    # Numbers are listed in the system message, never next to the text.
    assert "232" in llm.prompts[1][0]["content"]
    assert "unchanged" not in llm.prompts[1][1]["content"]


PROSE = [
    "Connect the Frame Controller to the network before you open the Network Setup menu.",
    "The Frame Controller reboots when the Network Setup values are saved to memory.",
]


def test_terminology_is_decided_once_for_recurring_terms():
    llm = FakeLLM(["- Frame Controller => controlador de quadro\nNetwork Setup => KEEP\nOther => x"])
    glossary = llm._build_glossary([Unit(t) for t in PROSE])
    assert glossary.keep == ["Network Setup"]
    assert glossary.terms == {"Frame Controller": "controlador de quadro"}
    assert "Frame Controller" in llm.prompts[0][0]["content"]


def test_domain_words_are_decided_by_translating_a_sentence_using_them():
    prose = [
        "Insert the card into the frame and wait until the card starts up.",
        "Remove the card from the frame before you change the jumper setting.",
        "Each card reports its status to the controller over the network.",
    ]
    # Two uses rendered alike ("placa", "placas"): the decision.
    llm = FakeLLM(
        [
            json.dumps({"translation": "Insira a placa no quadro.", "word": "placa"}),
            json.dumps({"translation": "Remova as placas do quadro.", "word": "placas"}),
        ]
    )
    glossary = llm._build_glossary([Unit(t) for t in prose])
    assert glossary.terms == {"card": "placa"}
    # Asked to translate a sentence where the word names the thing.
    assert "Insert the card into the frame" in llm.prompts[0][0]["content"]
    assert '"card"' in llm.prompts[0][0]["content"]
    assert "Remove the card from the frame" in llm.prompts[1][0]["content"]

    # Two uses rendered differently ("porta", "porto"): a third breaks the tie.
    llm = FakeLLM(
        [
            json.dumps({"translation": "Insira a placa no quadro.", "word": "placa"}),
            json.dumps({"translation": "Remova o quadro.", "word": "quadro"}),
            json.dumps({"translation": "Cada placa informa seu status.", "word": "placa"}),
        ]
    )
    assert llm._build_glossary([Unit(t) for t in prose]).terms == {"card": "placa"}
    # Three different answers: no decision, each sentence decides.
    llm = FakeLLM(
        [
            json.dumps({"translation": "Insira a placa no quadro.", "word": "placa"}),
            json.dumps({"translation": "Remova o quadro.", "word": "quadro"}),
            json.dumps({"translation": "Cada cartão informa seu status.", "word": "cartão"}),
        ]
    )
    assert llm._build_glossary([Unit(t) for t in prose]).terms == {}
    # A word the translation does not use is no decision.
    llm = FakeLLM(
        [
            json.dumps({"translation": "Insira a placa no quadro.", "word": "chassi"}),
            json.dumps({"translation": "Remova a placa.", "word": "chassi"}),
            json.dumps({"translation": "Cada placa informa.", "word": "chassi"}),
        ]
    )
    assert llm._build_glossary([Unit(t) for t in prose]).terms == {}


def test_plurals_are_the_same_word():
    from doc_translator.translators.llm import _same_word

    assert _same_word("informação", "informações")
    assert _same_word("painel", "painéis")
    assert not _same_word("porta", "porto")


def test_pieces_of_a_company_name_are_kept():
    from doc_translator.terms import name_fragments

    texts = [
        "KOKUSAI DENKI Electric Linear S/A",
        "The method developed by KOKUSAI DENKI Electric Linear allows other equipment.",
        "Generator with the Function Implementer enabled.",
        "Used by the Function Implementer only.",
    ]
    assert name_fragments(texts, ["Electric Linear", "Function Implementer"]) == {"Electric Linear"}


def test_terminology_violation_triggers_retry():
    glossary = Glossary(terms={"Frame Controller": "controlador de quadro"})
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Reinicie o Frame Controller."]}),
            "Reinicie o controlador de quadro.",
        ],
        glossary=glossary,
    )
    results = llm.translate_units([Unit("Restart the Frame Controller.")])
    assert results[0].text == "Reinicie o controlador de quadro."
    assert "Mandatory terminology" in llm.prompts[1][0]["content"]


def test_interrupted_run_resumes_from_saved_progress():
    class Store(dict):
        def get_many(self, texts):
            return {t: self[t] for t in texts if t in self}

        def put_many(self, entries):
            self.update(entries)

    store = Store({"Connect the power cable.": "Conecte o cabo de alimentação."})
    llm = FakeLLM([json.dumps({"translations": ["Ligue o aparelho."]})])
    llm.keep_progress(store)

    result = llm.translate_units([Unit("Connect the power cable."), Unit("Turn on the device.")])

    assert [r.text for r in result] == ["Conecte o cabo de alimentação.", "Ligue o aparelho."]
    # Only the new paragraph is translated (the saved one is just context).
    assert len(llm.prompts) == 1
    assert llm.prompts[0][-1]["content"].endswith('[\n"Turn on the device."\n]')
    assert store["Turn on the device."] == "Ligue o aparelho."  # saved as it goes


def test_prompts_follow_the_language_pair():
    from dataclasses import replace

    llm = FakeLLM([json.dumps({"translations": ["Visão geral"]})])
    llm.languages = replace(EN_PT_BR, target="European Portuguese", target_code="pt-PT")
    llm.translate_units([Unit("Overview")])
    system, user = llm.prompts[0]
    assert "English to European Portuguese (pt-PT)" in system["content"]
    assert "into European Portuguese" in " ".join(user["content"].split())


def test_prompts_carry_the_document_brief_and_the_section():
    llm = FakeLLM(["A manual for a video frame controller.", json.dumps({"translations": ["Reinicie."]})])
    llm.brief = True
    llm.translate_units([Unit("Restart the card.", section="Upgrading the Firmware")])
    user = llm.prompts[1][1]["content"]
    assert "A manual for a video frame controller." in user
    assert "Section: Upgrading the Firmware" in user


def test_what_the_user_says_about_the_document_is_read_and_followed():
    llm = FakeLLM(["A comic book.", json.dumps({"translations": ["Meus braços!"]})])
    llm.brief = True
    llm.user_context = "Doctor Octopus has four mechanical arms."
    llm.translate_units([Unit("My arms!", kind="balloon")])
    describe, translate = (prompt[-1]["content"] for prompt in llm.prompts)
    assert "Doctor Octopus has four mechanical arms." in describe
    assert "From the user (follow it): Doctor Octopus has four mechanical arms." in translate


def test_italic_labels_that_recur_stay_untranslated():
    from doc_translator.terms import interface_labels

    units = [
        Unit("Open the Network Setup menu.", labels=["Network Setup"]),
        Unit("The Network Setup menu has six parameters.", labels=["Network Setup", "must"]),
        Unit("Set the value to yes.", labels=["yes"]),
        Unit("The IP Address and Gateway are assigned.", labels=["IP Address, Gateway,"]),
        Unit("Enter the IP Address and Gateway.", labels=["IP Address", "Gateway"]),
        Unit("Each card has a Gateway; the gateway routes packets.", labels=["Gateway"]),
    ]
    # Lowercase emphasis ("must", "yes") is not a name; a list is split; a
    # name also written in lowercase ("gateway") is an ordinary word.
    assert interface_labels(units) == ["Network Setup", "IP Address"]
    translations = [
        "Abra o menu Network Setup.",
        "O menu Network Setup tem seis parâmetros.",
        "Defina o valor como yes.",
        "O IP Address e o Gateway são atribuídos.",
        "Insira o IP Address e o Gateway.",
        "Cada placa tem um gateway; o gateway encaminha pacotes.",
    ]
    llm = FakeLLM([json.dumps({"translations": translations})])
    llm.translate_units(units)
    assert '"Network Setup": keep in English' in llm.prompts[0][1]["content"]


def test_a_worse_retry_does_not_replace_the_batch_translation():
    glossary = Glossary(terms={"Frame Controller": "controlador de quadro"})
    source = "Disable DHCP before upgrading the Frame Controller firmware."
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Desative o DHCP antes de atualizar o firmware do Frame Controller."]}),
            source,  # the retry comes back in English
        ],
        glossary=glossary,
    )
    results = llm.translate_units([Unit(source)])
    assert results[0].text.startswith("Desative")


def test_untranslated_text_is_detected_but_names_are_not():
    from doc_translator.translators.llm import _untranslated

    source = "Subsequently, the 7700FC should also be upgraded with the latest information."
    assert _untranslated(source, source)
    assert _untranslated(source, "Subsequentemente, the 7700FC should also be upgraded with the latest information.")
    assert not _untranslated(source, "Em seguida, o 7700FC também deve ser atualizado com as informações recentes.")
    assert not _untranslated("**** SNMP Setup ****", "**** SNMP Setup ****")
    assert not _untranslated("Trap #1: 192.168.1.76 Trap #2: 192.168.8.140", "Trap #1: 192.168.1.76 Trap #2: 192.168.8.140")
    for text in ["http://www.evertz.com", "Type: put<space><<firmwarelocation>>.bin<space>app<Enter>"]:
        assert not _untranslated(text, text)
    # Quoted terms kept, the rest translated.
    assert not _untranslated(
        "Changed “route” to “gateway” throughout manual", "Alterado 'route' para 'gateway' em todo o manual"
    )


def test_cells_are_translated_together_with_their_table():
    """A cell alone ("Pin #", "White/Orange") says too little: the cells of
    a table go in one batch, with the whole table as context, apart from
    the running text around it."""
    table = "Pin # | Signal | Color\n1 | Transmit + | White/Orange"
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Conecte o cabo."]}),
            json.dumps({"translations": ["Pino #", "Sinal", "Cor", "Transmitir +", "Branco/Laranja"]}),
            json.dumps({"translations": ["Veja a tabela."]}),
        ]
    )
    cells = [Unit(text, table=table) for text in ["Pin #", "Signal", "Color", "Transmit +", "White/Orange"]]
    result = llm.translate_units([Unit("Connect the cable."), *cells, Unit("See the table.")])
    assert [r.text for r in result][1] == "Pino #"
    batch = llm.prompts[1][1]["content"]
    assert "cells of this table" in batch and table in batch
    assert "Preceding text" not in batch
    assert "Connect the cable." not in batch.split("cells of this table")[1]


def test_batches_keep_groups_apart():
    group = {"a": "t1", "b": "t1", "c": "", "d": "t2"}
    assert _batches(["a", "b", "c", "d"], group.get) == [["a", "b"], ["c"], ["d"]]


def test_a_table_goes_in_one_batch_and_later_batches_follow_earlier_ones():
    cells = [f"cell {n}" for n in range(15)]
    assert len(_batches(cells, lambda text: "table")) == 1  # not 10 + 5

    table = "Color\nWhite\nGreen"
    llm = FakeLLM([json.dumps({"translations": ["Verde"]})])
    llm._tables = {"White": table, "Green": table}
    llm._table_cells = {table: ["White", "Green"]}
    llm._cache = {"White": "Branco"}
    llm._translate_all(["White", "Green"], Glossary(), with_context=True)
    assert "White => Branco" in llm.prompts[0][1]["content"]


def test_an_answer_shifted_to_another_item_is_detected():
    from doc_translator.translators.llm import _shifted

    texts = ["The two criteria are:", "7700FC Image is “fc_release-2-19-b1” or greater."]
    shifted = ['7700FC Image is "fc_release-2-19-b1" or greater', "A imagem do 7700FC é fc_release-2-19-b1 ou superior."]
    assert _shifted(0, texts, shifted)
    good = ["Os dois critérios são:", "A imagem do 7700FC é fc_release-2-19-b1 ou superior."]
    assert not _shifted(0, texts, good) and not _shifted(1, texts, good)


def test_alike_cells_are_not_taken_for_shifted_answers():
    from doc_translator.translators.llm import _shifted

    texts = ["Green/White or White", "Green/White or Green", "Blue"]
    answers = ["Verde/Branco ou Branco", "Verde/Branco ou Verde", "Blue"]
    assert not any(_shifted(i, texts, answers) for i in range(3))


def test_a_code_dropped_twice_is_named_in_a_last_try():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Tabela 1: Códigos de cores de fiação padrão"]}),
            "Tabela 1: Códigos de cores de fiação padrão",
            "Tabela 1: Códigos de cores de fiação RJ45 padrão",
        ]
    )
    results = llm.translate_units([Unit("Table 1: Standard RJ45 Wiring Colour Codes")])
    assert results[0].text == "Tabela 1: Códigos de cores de fiação RJ45 padrão"
    assert "RJ45" in llm.prompts[1][0]["content"]  # codes listed in the first retry
    assert "dropped or changed these" in llm.prompts[2][0]["content"]
    assert "dropped" not in llm.prompts[1][0]["content"]


def test_a_name_translated_twice_is_named_in_a_last_try():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["A Configuração de SNMP é usada para destinos de trap."]}),
            "A Configuração de SNMP é usada para destinos de trap.",
            "SNMP Setup é usado para destinos de trap.",
        ],
        glossary=Glossary(keep=["SNMP Setup"]),
    )
    results = llm.translate_units([Unit("SNMP Setup is used to identify trap destinations.")])
    assert results[0].text == "SNMP Setup é usado para destinos de trap."
    assert "dropped or changed these; write each exactly as given: SNMP Setup" in llm.prompts[2][0]["content"]


def test_a_label_returned_in_english_is_retried():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Voltage:", "Verifique a tensão antes de ligar a placa ao quadro."]}),
            "Tensão:",
        ]
    )
    results = llm.translate_units([Unit("Voltage:"), Unit("Check the voltage before you plug the card into the frame.")])
    assert results[0].text == "Tensão:"


def test_a_name_returned_as_written_is_not_retried():
    llm = FakeLLM([json.dumps({"translations": ["IP Address"]})], glossary=Glossary(keep=["IP Address"]))
    assert llm.translate_units([Unit("IP Address")])[0].text == "IP Address"
    assert len(llm.prompts) == 1


class Words:
    """A dictionary that knows every word but the ones given."""

    def __init__(self, invented):
        self.invented = invented

    def lookup(self, word):
        return word.lower() not in self.invented


def test_an_invented_word_is_retried():
    from doc_translator.spelling import Spelling

    llm = FakeLLM(
        [
            json.dumps({"translations": ["Remova os RETIFOS da placa."]}),
            "Remova os jumpers da placa.",
        ]
    )
    llm.spelling = Spelling(Words({"retifos"}))
    result = llm.translate_units([Unit("Remove the jumpers from the card.")])
    assert result[0].text == "Remova os jumpers da placa."


def test_a_lost_code_is_worse_than_an_invented_word():
    from doc_translator.spelling import Spelling

    source = "The address is taken from the 7700FC configuration information."
    llm = FakeLLM(
        [
            # Shifted: the previous item's translation, without "7700FC".
            json.dumps({"translations": ["Na solicitação, digite boot upgrade."]}),
            "O endereço é extraído das informações de subrede do 7700FC.",
            "O endereço é extraído das informações de subrede do 7700FC.",
        ]
    )
    llm.spelling = Spelling(Words({"subrede"}))
    result = llm.translate_units([Unit(source)])
    assert result[0].text == "O endereço é extraído das informações de subrede do 7700FC."


def test_a_word_still_invented_is_named_in_the_last_try():
    from doc_translator.spelling import Spelling

    llm = FakeLLM(
        [
            json.dumps({"translations": ["O cartão deve ser reinsertado."]}),
            "O cartão deve ser reinsertado.",
            "O cartão deve ser reinserido.",
        ]
    )
    llm.spelling = Spelling(Words({"reinsertado"}))
    result = llm.translate_units([Unit("The card must be reinserted now.")])
    assert result[0].text == "O cartão deve ser reinserido."
    assert "do not exist in Brazilian Portuguese: reinsertado" in llm.prompts[2][0]["content"]


def test_an_english_possessive_is_not_left_on_a_portuguese_word():
    """"AMERICA'S MOST EXCITING SUPER-HERO" came back "AMÉRICA'S MAIS
    EMOCIONANTE SUPER-HERÓI" (the comic's cover): retried, then named."""
    source = "AMERICA'S MOST EXCITING SUPER-HERO CAPTURED BY THE WORLD'S MOST DREADED SUPER-VILLAIN!"
    llm = FakeLLM(
        [
            json.dumps({"translations": ["AMÉRICA'S MAIS EMOCIONANTE SUPER-HERÓI CAPTURADO PELO MUNDO'S MAIS TEMIDO SUPER-VILÃO!"]}),
            "AMÉRICA'S MAIS EMOCIONANTE SUPER-HERÓI CAPTURADO PELO MUNDO'S MAIS TEMIDO SUPER-VILÃO!",
            "O SUPER-HERÓI MAIS EMOCIONANTE DA AMÉRICA CAPTURADO PELO SUPER-VILÃO MAIS TEMIDO DO MUNDO!",
        ]
    )
    result = llm.translate_units([Unit(source)])
    assert result[0].text == "O SUPER-HERÓI MAIS EMOCIONANTE DA AMÉRICA CAPTURADO PELO SUPER-VILÃO MAIS TEMIDO DO MUNDO!"
    assert "AMÉRICA'S, MUNDO'S" in llm.prompts[2][0]["content"]


def test_a_decision_with_an_invented_word_is_dropped():
    from doc_translator.spelling import Spelling

    prose = [
        "Set the netmask before you set the gateway of the card.",
        "The netmask of the card is shown on the screen.",
        "Write the netmask in the field and save the settings.",
    ]
    llm = FakeLLM([json.dumps({"translation": "Defina a máscara de subrede.", "word": "máscara de subrede"})] * 9)
    llm.spelling = Spelling(Words({"subrede"}))
    llm._spelling = llm.spelling
    glossary = llm._build_glossary([Unit(t) for t in prose])
    assert "netmask" not in glossary.terms


class Lowercase:
    """A target dictionary that has only the words given, in lowercase."""

    def __init__(self, words):
        self.words = words

    def has(self, word):
        return word in self.words


def test_capitals_are_asked_in_sentence_case_and_answered_in_capitals():
    from doc_translator.translators.checks import Vocabulary

    llm = FakeLLM([json.dumps({"translations": ["Atualização do firmware do módulo via DHCP"]})])
    llm._known = Vocabulary(ordinary=frozenset({"upgrading", "module", "firmware", "through"}))
    assert llm._asked("UPGRADING MODULE FIRMWARE THROUGH DHCP") == "Upgrading module firmware through DHCP"
    llm._known = None
    english = Lowercase({"upgrading", "module", "firmware", "through"})
    llm._load_source_spelling = lambda: english
    result = llm.translate_units([Unit("UPGRADING MODULE FIRMWARE THROUGH DHCP")])
    assert "Upgrading module firmware through DHCP" in llm.prompts[0][-1]["content"]
    assert result[0].text == "ATUALIZAÇÃO DO FIRMWARE DO MÓDULO VIA DHCP"


def test_a_source_word_made_to_look_portuguese_is_written_as_the_source_word():
    from doc_translator.spelling import Spelling

    spelling = Spelling(None)
    assert spelling.source_words_for("At the prompt, type:", "Na prompção, digite:", ["prompção"]) == "Na prompt, digite:"
    # A word not made from a source word is left (still reported).
    assert spelling.source_words_for("subnet mask", "máscara de subrede", ["subrede"]) == "máscara de subrede"


def test_values_with_units_are_not_translated_but_headings_are():
    from doc_translator.translators.base import has_words

    assert not has_words("1 kΩ")
    assert not has_words("BNC-F / 75 Ω")
    assert not has_words("3V3 LVTTL")
    assert has_words("1 to 13")
    assert has_words("UPGRADING MODULE FIRMWARE THROUGH THE 7700FC FRAME CONTROLLER")


def test_a_comics_names_are_kept_and_its_common_phrases_left():
    llm = FakeLLM(["Doctor Octopus => NAME\nArtificial Arms => WORDS"])
    texts = [
        "DOCTOR OCTOPUS IS STILL BREATHING!",
        "NO ONE CAN HOLD DOCTOR OCTOPUS!",
        "WE CAN'T REMOVE THOSE ARTIFICIAL ARMS!",
        "THE ARTIFICIAL ARMS ADHERE TO HIM!",
    ]
    assert llm._comic_names(texts) == ["Doctor Octopus"]
    assert "Artificial Arms" in llm.prompts[0][0]["content"]


def test_balloons_see_the_story_around_them():
    """A comic's balloons are short: three before them did not tell who was
    speaking. They see about two pages before, and the balloons after."""
    texts = [f"Line number {n} of the story!" for n in range(14)]
    answers = [json.dumps({"translations": [f"Linha {n} da história!" for n in range(*span)]}) for span in ((0, 10), (10, 14))]
    llm = FakeLLM(answers)
    llm.translate_units([Unit(t, kind="balloon") for t in texts])
    first, second = (prompt[1]["content"] for prompt in llm.prompts[-2:])
    assert "Text that follows" in first and "Line number 13 of" in first.split("Text that follows")[1]
    preceding = second.split("Preceding text")[1].split("Translate")[0]
    assert all(f"Line number {n} of" in preceding for n in range(10))
