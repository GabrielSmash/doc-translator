from doc_translator.terms import recurring_terms
from doc_translator.translators.base import Glossary

TEXTS = [
    "Once the Frame Controller is connected, open the Network Setup menu and press Enter.",
    "Once installed, the Frame Controller reads the Network Setup values from memory.",
    "The frame controller keeps the community strings in a safe place for later.",
    "Frame Controller",  # headings don't count
    "Address Netmask Gateway Broadcast Hostname values appear in the table for you.",
]


def test_recurring_capitalized_terms_are_found():
    terms = recurring_terms(TEXTS)
    assert list(terms) == ["Frame Controller", "Network Setup"]
    assert "Frame Controller" in terms["Frame Controller"]  # usage example


def test_sentence_initial_words_and_long_runs_are_not_terms():
    terms = recurring_terms(TEXTS + TEXTS)
    assert not any(t.startswith("Once") for t in terms)
    assert not any("Netmask" in t for t in terms)


def test_glossary_violations_allow_inflections():
    glossary = Glossary(keep=["Network Setup"], terms={"Frame Controller": "controlador de quadro"})
    source = "The Frame Controllers use the Network Setup menu."
    assert glossary.violations(source, "Os controladores de quadro usam o menu Network Setup.") == []
    assert glossary.violations(source, "Os Frame Controllers usam o menu de configuração.") == [
        "Network Setup",
        "Frame Controller",
    ]


def test_quoted_terms_are_ui_labels():
    from doc_translator.terms import quoted_terms

    texts = ["Expose the module using ‘Add/Update Frame’ and select the “Upload Main Program” prompt."]
    terms = ["Add/Update Frame", "Upload Main Program", "Power Supply"]
    assert quoted_terms(texts, terms) == {"Add/Update Frame", "Upload Main Program"}
