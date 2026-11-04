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
