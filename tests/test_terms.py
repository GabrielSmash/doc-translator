from doc_translator.terms import kept_terms, quoted_names, recurring_terms
from doc_translator.translators.base import Glossary, Unit

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


def test_a_screen_name_also_written_as_text_is_kept_only_with_its_capitals():
    units = [
        Unit("Select <1> to open Main Menu and then Network Setup.", names=["Main Menu", "Network Setup", "Exit"]),
        Unit("From the main menu, exit and save. Exit the menu now."),
    ]
    kept = kept_terms(units)
    assert kept.exact == ["Main Menu"]
    assert "Network Setup" in kept.keep
    assert "Exit" not in kept.kept()  # one word: it also starts sentences
    pattern = kept.pattern()
    assert pattern.search("open Main Menu")
    assert not pattern.search("from the main menu")


def test_names_an_instruction_points_at_are_kept_where_quoted():
    units = [
        Unit("Enter “customer” for both “User” and “Password” prompts."),
        Unit("Select the ‘Control’ tab, then select ‘View configuration’."),
        Unit("This item defines the “subnet mask” of the network."),
        Unit("If you are building a “crossover” cable, wire it as shown."),
        Unit("Connect the PC’s network card to the hub."),
    ]
    assert quoted_names(units) == ["customer", "User", "Password", "Control", "View configuration"]
    kept = kept_terms(units)
    pattern = kept.pattern()
    assert pattern.search("press the ‘Control’ tab")
    assert not pattern.search("Control the LEDs")
    assert kept.violations("Select the ‘Control’ tab.", "Selecione a aba 'Controle'.") == ["Control"]
    assert kept.violations("Select the ‘Control’ tab.", "Selecione a aba 'Control'.") == []


def test_kept_terms_are_whole_words():
    glossary = Glossary(keep=["IP Address"], exact=["Network Setup"])
    assert glossary.relevant("Set the IP addresses.", "English") == ""
    assert glossary.violations("Set the IP addresses.", "Defina os endereços IP.") == []
    assert glossary.violations("Set the IP Address.", "Defina o endereço IP.") == ["IP Address"]


def test_domain_words_are_found_where_they_name_things():
    from doc_translator.terms import domain_words

    units = [
        Unit("Insert the card into the frame and close the front door of the unit."),
        Unit("Each card has a jumper that selects the mode when the frame starts."),
        Unit("Remove the card from the frame before you move the jumper to a new place."),
        Unit("Use the same steps for the following card in the Evertz frame."),
        Unit("Put the jumper back when the upgrade of the unit is complete."),
        Unit("9 pin D Female"),
        Unit("15 pin D Female"),
        Unit("The female pin of the cable goes into the pin header of the card."),
    ]
    words = domain_words(units)
    assert list(words)[:2] == ["card", "frame"]
    assert {"jumper", "pin", "female"} <= set(words)
    # Describing words, and names only written capitalized, are not things.
    assert not {"same", "following", "front", "evertz"} & set(words)
    # Running text first, where the word is written in lowercase.
    assert words["female"][0].startswith("The female pin")
    assert "card" not in domain_words(units, exclude=["Card"])


def test_a_fixed_word_is_matched_whole_and_not_inside_a_kept_name():
    glossary = Glossary(keep=["Network Setup"], terms={"port": "porta", "network": "rede"})
    assert glossary.violations("Check the support team.", "Verifique a equipe.") == []
    assert glossary.violations("Connect both ports.", "Conecte as duas entradas.") == ["port"]
    assert glossary.violations("Open Network Setup.", "Abra Network Setup.") == []
    assert "network" not in glossary.relevant("Open Network Setup.", "English")
