# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for the word-feedback strip in mimora/ui_hero.py.

Covers the pure ``parse_word_levels`` normalizer (both engine shapes) and, with
a real Tk root, the HeroCard strip rendering (coloured cells per word, click to
hear, hide when empty). The Tk part mirrors the way the app runs - a real root,
no model, no audio.
"""

import tkinter as tk
import unittest

from mimora.ui_hero import HeroCard, parse_word_levels


class ParseWordLevelsTests(unittest.TestCase):
    """Normalizes both engines' ``reference_words`` into (word, level) pairs."""

    def test_phoneme_three_levels_passthrough(self):
        words = [
            {"word": "she", "level": "good", "correct": True},
            {"word": "could", "level": "ok", "correct": True},
            {"word": "helping", "level": "bad", "correct": False},
        ]
        self.assertEqual(parse_word_levels(words),
                         [("she", "good"), ("could", "ok"), ("helping", "bad")])

    def test_acoustic_boolean_becomes_good_bad(self):
        words = [
            {"word": "Hello,", "correct": True},
            {"word": "world", "correct": False},
        ]
        self.assertEqual(parse_word_levels(words),
                         [("Hello,", "good"), ("world", "bad")])

    def test_missing_both_flags_defaults_to_good(self):
        words = [{"word": "silent"}]
        self.assertEqual(parse_word_levels(words), [("silent", "good")])

    def test_empty_input_is_empty(self):
        self.assertEqual(parse_word_levels([]), [])
        self.assertEqual(parse_word_levels(None), [])

    def test_case_and_punctuation_are_kept(self):
        words = [{"word": "I'm,", "correct": True}]
        self.assertEqual(parse_word_levels(words), [("I'm,", "good")])


class HeroCardWordFeedbackTests(unittest.TestCase):
    """Renders the word-feedback strip on a real Tk root."""

    @classmethod
    def setUpClass(cls):
        from tests._tk import make_root
        cls.root = make_root()
        cls.clicked = []
        cls.card = HeroCard(
            cls.root,
            on_word_clicked=lambda w: cls.clicked.append(w),
            on_sound_example=lambda w: None)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def setUp(self):
        HeroCardWordFeedbackTests.clicked = []
        self.card.set_word_feedback([])  # reset between tests

    def test_cells_are_created_per_word(self):
        words = [
            {"word": "she", "level": "good"},
            {"word": "helping", "level": "bad"},
        ]
        self.card.set_word_feedback(words)
        cells = self.card._word_feedback_cells
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0].cget("text"), "she")
        self.assertEqual(cells[1].cget("text"), "helping")

    def test_bad_word_uses_bad_colour(self):
        from mimora.ui_theme import THEME
        words = [{"word": "helping", "level": "bad"}]
        self.card.set_word_feedback(words)
        cell = self.card._word_feedback_cells[0]
        self.assertEqual(cell.cget("fg"), THEME["bad"])

    def test_good_word_uses_good_colour(self):
        from mimora.ui_theme import THEME
        words = [{"word": "she", "level": "good"}]
        self.card.set_word_feedback(words)
        cell = self.card._word_feedback_cells[0]
        self.assertEqual(cell.cget("fg"), THEME["good"])

    def test_click_cell_hears_that_word(self):
        words = [{"word": "helping", "level": "bad"}]
        self.card.set_word_feedback(words)
        cell = self.card._word_feedback_cells[0]
        self.card._on_feedback_cell_clicked(cell.cget("text"))
        self.assertEqual(HeroCardWordFeedbackTests.clicked, ["helping"])

    def test_empty_breakdown_hides_strip(self):
        self.card.set_word_feedback([])
        self.assertEqual(self.card._word_feedback_cells, [])
        self.assertNotEqual(self.card.word_feedback.winfo_manager(), "pack")

    def test_reset_clears_cells_and_hides(self):
        self.card.set_word_feedback([{"word": "x", "level": "bad"}])
        self.card.reset_word_feedback()
        self.assertEqual(self.card._word_feedback_cells, [])
        self.assertNotEqual(self.card.word_feedback.winfo_manager(), "pack")

    def test_set_phrase_clears_strip(self):
        self.card.set_word_feedback([{"word": "x", "level": "bad"}])
        self.card.set_phrase("A new phrase.")
        self.assertEqual(self.card._word_feedback_cells, [])


class HeroCardWordDrillTests(unittest.TestCase):
    """Double-click a phrase word to start a word drill on it."""

    @classmethod
    def setUpClass(cls):
        from tests._tk import make_root
        cls.root = make_root()
        cls.played = []
        cls.drilled = []
        cls.single_drilled = []
        cls.card = HeroCard(
            cls.root,
            on_word_clicked=lambda w: cls.played.append(w),
            on_sound_example=lambda w: None,
            on_word_drill_clicked=lambda w: cls.drilled.append(w),
            on_word_single_drill_clicked=lambda w: cls.single_drilled.append(w))
        cls.card.set_phrase("helping others")

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def setUp(self):
        HeroCardWordDrillTests.played = []
        HeroCardWordDrillTests.drilled = []
        HeroCardWordDrillTests.single_drilled = []
        self.card._cancel_scheduled_word_playback()

    def test_start_single_word_drill_forwards_word(self):
        self.card._start_word_single_drill("helping")
        self.assertEqual(HeroCardWordDrillTests.single_drilled, ["helping"])

    def test_start_single_word_drill_noop_without_callback(self):
        card = HeroCard(
            self.root, on_word_clicked=lambda w: None,
            on_sound_example=lambda w: None)
        card._start_word_single_drill("helping")  # must not raise
        self.assertEqual(card._scheduled_word_after_id, None)

    def test_single_click_is_deferred_not_immediate(self):
        self.card._schedule_word_playback("helping")
        # Not played yet: the playback waits for the double-click window.
        self.assertEqual(HeroCardWordDrillTests.played, [])
        self.assertIsNotNone(self.card._scheduled_word_after_id)

    def test_scheduled_playback_plays_word_after_window(self):
        self.card._schedule_word_playback("helping")
        self.card._play_scheduled_word("helping")
        self.assertEqual(HeroCardWordDrillTests.played, ["helping"])
        self.assertIsNone(self.card._scheduled_word_after_id)

    def test_cancel_scheduled_playback_prevents_play(self):
        self.card._schedule_word_playback("helping")
        self.card._cancel_scheduled_word_playback()
        self.assertIsNone(self.card._scheduled_word_after_id)
        self.assertEqual(HeroCardWordDrillTests.played, [])

    def test_second_click_supersedes_first(self):
        self.card._schedule_word_playback("helping")
        self.card._schedule_word_playback("others")
        # Only the second word remains scheduled (first was cancelled).
        self.card._play_scheduled_word("others")
        self.assertEqual(HeroCardWordDrillTests.played, ["others"])

    def test_double_click_cancels_playback_and_single_word_drills(self):
        # A double-click fires two single-clicks first; the first schedules the
        # playback, then the double-click cancels it and drills the word alone.
        self.card._schedule_word_playback("helping")
        self.card._cancel_scheduled_word_playback()
        self.card._start_word_single_drill("helping")
        self.assertEqual(HeroCardWordDrillTests.played, [])
        self.assertEqual(HeroCardWordDrillTests.single_drilled, ["helping"])

    def test_new_phrase_cancels_pending_playback(self):
        self.card._schedule_word_playback("helping")
        self.card.set_phrase("a brand new phrase")
        self.assertIsNone(self.card._scheduled_word_after_id)
        self.assertEqual(HeroCardWordDrillTests.played, [])


class HeroCardPhraseHeightCapTests(unittest.TestCase):
    """A long phrase is capped and scrollable instead of clipping the score row.

    The phrase Text grows with its wrapped content, but only up to
    ``_PHRASE_MAX_LINES``: a very long generated phrase would otherwise push the
    score row (and the WORK ON badges) off the card and out of the fixed-height
    window. Over the cap the phrase scrolls (mousewheel / Up / Down).
    """

    @classmethod
    def setUpClass(cls):
        from tests._tk import make_root
        cls.root = make_root()
        # A real, sized and *visible* root so the phrase Text wraps and the
        # scrollbar maps like it does in the app (the withdrawn root the helper
        # returns is 1px wide and never maps its children, so the phrase would
        # wrap to the cap and winfo_ismapped would always be false).
        cls.root.geometry("600x852")
        cls.root.deiconify()
        cls.root.update()
        cls.card = HeroCard(
            cls.root,
            on_word_clicked=lambda w: None,
            on_sound_example=lambda w: None)
        cls.long_words = ["the", "quick", "brown", "fox", "jumps", "over",
                          "lazy", "dog", "river", "bank", "mountain", "wind",
                          "tree", "bird", "song", "valley"]

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_short_phrase_uses_its_own_height(self):
        # 5 wrapped lines < the 8-line cap: the Text keeps its natural height.
        self.card.set_phrase("The quick brown fox jumps over the lazy dog")
        self.root.update()
        self.assertGreater(self.card.phrase_text.cget("height"), 1)
        self.assertLessEqual(self.card.phrase_text.cget("height"), 8)
        self.assertFalse(self.card.phrase_scrollbar.winfo_ismapped())

    def test_long_phrase_is_capped_at_max_lines(self):
        self.card.set_phrase(" ".join(self.long_words * 8))
        self.root.update()
        self.assertEqual(self.card.phrase_text.cget("height"), 8)
        # Over the cap the scrollbar appears beside the phrase.
        self.assertTrue(self.card.phrase_scrollbar.winfo_ismapped())

    def test_scrollbar_hides_again_when_phrase_shrinks(self):
        self.card.set_phrase(" ".join(self.long_words * 8))
        self.root.update()
        self.assertTrue(self.card.phrase_scrollbar.winfo_ismapped())
        self.card.set_phrase("short phrase")
        self.root.update()
        self.assertFalse(self.card.phrase_scrollbar.winfo_ismapped())

    def test_wheel_scrolls_long_phrase_down(self):
        self.card.set_phrase(" ".join(self.long_words * 8))
        self.root.update()
        self.card.phrase_text.yview_moveto(0.0)
        event = tk.Event()
        event.delta = -120  # wheel down
        self.card._on_phrase_wheel(event)
        self.root.update()
        self.assertGreater(self.card.phrase_text.yview()[0], 0.0)

    def test_wheel_scrolls_long_phrase_up(self):
        # Scrolled down first, then a wheel-up returns towards the top.
        self.card.set_phrase(" ".join(self.long_words * 8))
        self.root.update()
        self.card.phrase_text.yview_moveto(0.5)
        before = self.card.phrase_text.yview()[0]
        event = tk.Event()
        event.delta = 120  # wheel up
        self.card._on_phrase_wheel(event)
        self.root.update()
        self.assertLess(self.card.phrase_text.yview()[0], before)

    def test_wheel_ignored_when_phrase_is_short(self):
        # No scrolling under the cap: the view stays at the top.
        self.card.set_phrase("The quick brown fox")
        self.root.update()
        self.card.phrase_text.yview_moveto(0.0)
        event = tk.Event()
        event.delta = -120
        self.card._on_phrase_wheel(event)
        self.root.update()
        self.assertEqual(self.card.phrase_text.yview()[0], 0.0)


if __name__ == "__main__":
    unittest.main()
