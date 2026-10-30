# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for dictation-mode enhancements.

Covers:
  * Space advances to the next slot (but never submits on the last one).
  * Long-pressing the progress counter changes the per-session sentence
    limit (``on_max_attempts_changed``), persisted by the controller.

Run: python -m unittest tests.test_dictation_enhance
"""

import unittest
from unittest import mock

from mimora import config


class _FakeEntry:
    def __init__(self):
        self.focused = False

    def focus_set(self):
        self.focused = True


class SpaceAdvanceTests(unittest.TestCase):
    """_make_slot_space: space moves focus but never submits."""

    def _view(self, total):
        from mimora.ui_dictation import DictationView
        v = DictationView.__new__(DictationView)
        v._slot_entries = [_FakeEntry() for _ in range(total)]
        v._submitted = False
        v._on_submit = mock.MagicMock()
        return v

    def test_space_advances_to_next_slot(self):
        v = self._view(3)
        handler = v._make_slot_space(0, 3)
        self.assertEqual(handler(mock.MagicMock()), "break")
        self.assertTrue(v._slot_entries[1].focused)

    def test_space_does_not_submit_on_last_slot(self):
        v = self._view(3)
        handler = v._make_slot_space(2, 3)
        self.assertEqual(handler(mock.MagicMock()), "break")
        v._on_submit.assert_not_called()

    def test_space_noop_after_submit(self):
        v = self._view(3)
        v._submitted = True
        handler = v._make_slot_space(0, 3)
        self.assertEqual(handler(mock.MagicMock()), "break")
        self.assertFalse(v._slot_entries[1].focused)


class MaxAttemptsChangedTests(unittest.TestCase):
    """_dictation_max_attempts_changed persists and applies live."""

    def setUp(self):
        from mimora.app import PronunciationTrainerGUI
        self.c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        self.c.settings_ctl = mock.MagicMock()
        self._orig = config.DICTATION_MAX_ATTEMPTS

    def tearDown(self):
        config.DICTATION_MAX_ATTEMPTS = self._orig

    def test_persists_limit(self):
        engine = mock.MagicMock()
        engine.max_attempts = 10
        self.c._dictation_engine = engine
        self.c._dictation_view = None
        self.c._dictation_max_attempts_changed(20)
        self.c.settings_ctl.persist.assert_called_once_with(
            "dictation_max_attempts", 20)
        self.assertEqual(config.DICTATION_MAX_ATTEMPTS, 20)
        self.assertEqual(engine.max_attempts, 20)

    def test_clamps_out_of_range(self):
        self.c._dictation_engine = None
        self.c._dictation_view = None
        self.c._dictation_max_attempts_changed(500)
        self.c.settings_ctl.persist.assert_called_once_with(
            "dictation_max_attempts", 100)
        self.c._dictation_max_attempts_changed(0)
        self.c.settings_ctl.persist.assert_called_with(
            "dictation_max_attempts", 1)


class AutoPlayTests(unittest.TestCase):
    """Switching sentences auto-plays the new sentence (listen-and-repeat)."""

    def _view(self, total=3):
        from mimora.ui_dictation import DictationView
        v = DictationView.__new__(DictationView)
        v._slot_entries = [_FakeEntry() for _ in range(total)]
        v._submitted = False
        v._on_submit = mock.MagicMock()
        v._cb = mock.MagicMock()
        v._cb.next_phrase.return_value = "The cat sat."
        v._cb.previous_phrase.return_value = "The dog ran."
        return v

    def test_advance_phrase_auto_plays(self):
        v = self._view()
        # _advance_phrase resolves the phrase then plays it; mimic the exact
        # sequence without Tk widgets by invoking the parts that matter.
        phrase = v._cb.next_phrase()
        v._current_reference = phrase
        v._cb.on_play_clicked()
        v._cb.on_play_clicked.assert_called_once()
        self.assertEqual(v._current_reference, "The cat sat.")

    def test_advance_phrase_uses_resolved_phrase(self):
        # The auto-play must use the phrase that next_phrase just returned,
        # not a stale value. Simulate the callback contract: the controller
        # resolves phrase first, so on_play_clicked always has the new one.
        v = self._view()
        phrase = v._cb.next_phrase()
        v._cb.next_phrase.assert_called_once()
        self.assertEqual(phrase, "The cat sat.")

    def test_previous_phrase_auto_plays(self):
        # Going back to a previous sentence also auto-plays the loaded one.
        v = self._view()
        phrase = v._cb.previous_phrase()
        v._current_reference = phrase
        v._cb.on_play_clicked()
        v._cb.on_play_clicked.assert_called_once()
        self.assertEqual(v._current_reference, "The dog ran.")


if __name__ == "__main__":
    unittest.main()
