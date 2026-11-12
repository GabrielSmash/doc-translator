import json

from doc_translator.translators.base import Glossary, Unit
from doc_translator.translators.llm import LLMTranslator, _batches, _clean, _plausible


class FakeLLM(LLMTranslator):
    """LLMTranslator with canned Ollama answers, in call order."""

    def __init__(self, answers: list[str], glossary: Glossary | None = None) -> None:
        self.model = "fake"
        self.user_glossary = glossary or Glossary()
        self._cache = {}
        self._progress = None
        self.cache_id = "llm|fake"
        self._all_layers_on_gpu = False
        self.answers = iter(answers)
        self.prompts: list[list[dict]] = []

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


def test_batches_respect_size_limits():
    texts = ["x" * 600] * 4 + ["y"] * 12
    # Short texts fill the previous batch up to BATCH_UNITS.
    assert [len(b) for b in _batches(texts)] == [2, 10, 4]


def test_units_are_batched_with_glossary_and_segments():
    llm = FakeLLM(
        [
            json.dumps({"translations": ["Visão geral", "A placa fornece os principais resultados via VistaLINK."]}),
            json.dumps({"items": [["principais resultados"]]}),
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
    assert len(llm.prompts) == 2  # no recurring terms: no terminology call


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


def test_segment_not_in_translation_falls_back_to_source_phrase():
    llm = FakeLLM([json.dumps({"items": [["algo inventado"]]})])
    located = llm._locate_batch([Unit("the main results", ["main results"])], ["os resultados"])
    assert located == [["main results"]]


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
