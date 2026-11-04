from doc_translator.align import Run, Style, realign

BASE = Style("Serif", 12.0, 0, False, False)
BOLD = Style("Serif-Bold", 12.0, 0, True, False)
LINK = Style("Serif", 12.0, 0xFF, False, False, link="https://example.com", underline=True)


def styled(runs: list[Run]) -> dict[str, Style]:
    return {run.text: run.style for run in runs if run.style != BASE}


def test_exact_match_keeps_untranslated_tokens():
    runs = realign("A receita cresceu 12% no trimestre.", BASE, [Run("12%", BOLD)], ["12%"])
    assert styled(runs) == {"12%": BOLD}


def test_fuzzy_match_handles_word_order_change():
    runs = realign(
        "Este documento resume os principais resultados do trimestre.",
        BASE,
        [Run("main results", BOLD)],
        ["resultados principais"],
    )
    assert styled(runs) == {"principais resultados": BOLD}


def test_punctuation_is_left_outside_the_styled_run():
    runs = realign(
        "Vamos focar em desempenho, confiabilidade e mais.",
        BASE,
        [Run("performance", BOLD), Run("reliability", BOLD)],
        ["desempenho", "confiabilidade"],
    )
    assert styled(runs) == {"desempenho": BOLD, "confiabilidade": BOLD}


def test_unmatched_segment_falls_back_to_base_style():
    runs = realign("Texto sem relação.", BASE, [Run("quarterly", BOLD)], ["trimestral"])
    assert styled(runs) == {}
    assert "".join(run.text for run in runs) == "Texto sem relação."


def test_link_style_is_carried_over():
    runs = realign(
        "Para detalhes, visite o relatório completo.",
        BASE,
        [Run("the full report", LINK)],
        ["o relatório completo"],
    )
    assert styled(runs) == {"o relatório completo": LINK}
