from doc_translator.translators.base import protect, restore


def test_urls_and_emails_are_protected_and_restored():
    text = "Write to team@example.com or visit https://example.com/a?b=1."
    protected, originals = protect(text)
    assert "@" not in protected and "https" not in protected
    assert protected.endswith(".")  # sentence punctuation stays outside
    assert restore(protected, originals) == text


def test_restore_does_not_confuse_placeholder_prefixes():
    originals = [f"u{i}@x.com" for i in range(11)]
    text = " ".join(f"XURL{i}" for i in range(11))
    assert restore(text, originals) == " ".join(originals)


def test_numbers_with_separators_are_protected():
    text = "Set it to 192.168.1.1 (see 7.3.7) on status page (1/2) with 10/100 ports, not 12."
    protected, originals = protect(text)
    assert originals == ["192.168.1.1", "7.3.7", "(1/2)", "10/100"]
    assert "12." in protected
    assert restore(protected, originals) == text
