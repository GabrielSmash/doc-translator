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
        self._all_layers_on_gpu = False
        self._brief = ""
        self._sections = {}
        self._tables = {}
        self._table_cells = {}
        self.answers = iter(answers)
        self.prompts: list[list[dict]] = []
        self.brief = None  # None: skip the description call

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
        == "Baixando a Imagem do 7700FC"
    )
    assert _match_case("Viewing the LED status", "VISUALIZANDO O STATUS DO LED") == (
        "Visualizando o status do LED"
    )
    assert _match_case("OVERVIEW", "Visão geral") == "VISÃO GERAL"
    assert _match_case("7700FC Frame Controller", "controlador de quadro 7700FC") == (
        "Controlador de quadro 7700FC"
    )


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
