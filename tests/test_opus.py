from doc_translator.translators.opus import OpusTranslator


class FakeOpus(OpusTranslator):
    """OpusTranslator with a scripted model: answers per call, in order."""

    def __init__(self, answers):
        self.glossary = None
        self._unknown_cache = {}
        self.answers = iter(answers)
        self.calls = []

    def _is_unknown(self, char):
        return False

    def _run(self, protected):
        self.calls.append([text for text, _ in protected])
        batch = next(self.answers)
        from doc_translator.translators.base import restore

        return [restore(answer, originals) for answer, (_, originals) in zip(batch, protected)]


def test_sentence_losing_a_number_is_retried_with_numbers_protected():
    opus = FakeOpus(
        [
            ["Conecte o cabo à porta 1.", "Pressione Enter."],  # "port 3" became "porta 1"
            ["Conecte o cabo à porta XURL0."],  # retry: the placeholder is copied
        ]
    )
    result = opus._translate_sentences(["Connect the cable to port 3.", "Press Enter."])
    assert result == ["Conecte o cabo à porta 3.", "Pressione Enter."]
    assert opus.calls[1] == ["Connect the cable to port XURL0."]  # only the failing sentence


def test_number_still_lost_after_retry_keeps_first_translation():
    opus = FakeOpus([["Conecte à porta 1."], ["Conecte à porta."]])
    assert opus._translate_sentences(["Connect to port 3."]) == ["Conecte à porta 1."]
