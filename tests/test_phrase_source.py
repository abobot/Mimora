# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/phrase_source.py (pure, no tkinter / no ML stack).

Run: python -m unittest tests.test_phrase_source
"""

import unittest

from mimora.phrase_source import SourceTextPhraseProvider, split_sentences


class TestSplitSentences(unittest.TestCase):
    """The shared sentence splitter (also used by LLMManager)."""

    def test_splits_on_end_punctuation(self):
        self.assertEqual(split_sentences("One. Two! Three?"),
                         ["One.", "Two!", "Three?"])

    def test_splits_on_newlines_and_keeps_headings(self):
        text = "A heading\nFirst sentence. Second sentence."
        self.assertEqual(split_sentences(text),
                         ["A heading", "First sentence.", "Second sentence."])

    def test_empty_and_whitespace_text(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   \n\n  "), [])

    def test_decimal_number_is_not_a_boundary(self):
        # No whitespace after the period inside "1.5", so no split.
        self.assertEqual(split_sentences("It costs 1.5 dollars."),
                         ["It costs 1.5 dollars."])


class TestSourceTextPhraseProvider(unittest.TestCase):
    """Sequential verbatim sentences with wraparound and edit reset."""

    TEXT = "First one. Second one! Third one?"

    def test_sequential_order_and_wraparound(self):
        provider = SourceTextPhraseProvider()
        phrases = [provider.generate_phrase(self.TEXT) for _ in range(4)]
        self.assertEqual(phrases, ["First one.", "Second one!",
                                   "Third one?", "First one."])

    def test_sentences_are_verbatim(self):
        # Punctuation and casing survive untouched - the "off" mode promise.
        provider = SourceTextPhraseProvider()
        self.assertEqual(provider.generate_phrase("¿Qué tal, señor Gómez?"),
                         "¿Qué tal, señor Gómez?")

    def test_length_argument_is_ignored(self):
        provider = SourceTextPhraseProvider()
        self.assertEqual(provider.generate_phrase(self.TEXT, length="fragment"),
                         "First one.")

    def test_text_edit_resets_to_first_sentence(self):
        provider = SourceTextPhraseProvider()
        provider.generate_phrase(self.TEXT)
        provider.generate_phrase(self.TEXT)
        edited = "Fresh start. Another sentence."
        self.assertEqual(provider.generate_phrase(edited), "Fresh start.")
        self.assertEqual(provider.generate_phrase(edited), "Another sentence.")

    def test_whitespace_only_edit_does_not_reset(self):
        # The hash is taken over the stripped text: surrounding whitespace is
        # not a content change and must not restart the walk.
        provider = SourceTextPhraseProvider()
        provider.generate_phrase(self.TEXT)
        self.assertEqual(provider.generate_phrase("  " + self.TEXT + "\n"),
                         "Second one!")

    def test_empty_text_returns_empty_phrase(self):
        provider = SourceTextPhraseProvider()
        self.assertEqual(provider.generate_phrase(""), "")
        self.assertEqual(provider.generate_phrase("   \n "), "")

    def test_single_sentence_repeats(self):
        provider = SourceTextPhraseProvider()
        self.assertEqual(provider.generate_phrase("Only one here."),
                         "Only one here.")
        self.assertEqual(provider.generate_phrase("Only one here."),
                         "Only one here.")


class TestSourceTextPhraseProviderRandom(unittest.TestCase):
    """Random order: never an immediate repeat, still covers the text."""

    TEXT = "First one. Second one! Third one?"

    def test_random_never_repeats_immediately(self):
        provider = SourceTextPhraseProvider(random_order=True)
        last = provider.generate_phrase(self.TEXT)
        for _ in range(50):
            phrase = provider.generate_phrase(self.TEXT)
            self.assertNotEqual(phrase, last)
            last = phrase

    def test_random_covers_all_sentences(self):
        provider = SourceTextPhraseProvider(random_order=True)
        seen = {provider.generate_phrase(self.TEXT) for _ in range(200)}
        self.assertEqual(seen, {"First one.", "Second one!", "Third one?"})

    def test_random_single_sentence_repeats(self):
        provider = SourceTextPhraseProvider(random_order=True)
        self.assertEqual(provider.generate_phrase("Only one here."),
                         "Only one here.")

    def test_random_empty_text_returns_empty(self):
        provider = SourceTextPhraseProvider(random_order=True)
        self.assertEqual(provider.generate_phrase(""), "")

    def test_random_text_edit_resets_state(self):
        # The hash reset must clear the "last phrase" guard so the first
        # sentence of a new text is not skipped because it equals the last.
        provider = SourceTextPhraseProvider(random_order=True)
        provider.generate_phrase("Same. Second.")
        provider.generate_phrase("Same. Second.")
        provider.generate_phrase("Same. Second.")
        edited = "Same. New second."
        for _ in range(50):
            phrase = provider.generate_phrase(edited)
            self.assertIn(phrase, {"Same.", "New second."})


if __name__ == "__main__":
    unittest.main()
