# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for discarding a take that scored zero.

The user asked: if a recording scores 0, do not record it and do not save
it. These tests cover the controller-level wiring in ``on_take_scored``
(no session tally, no review advance, no persistence) and the drill bridge
(no drill attempt recorded), plus the view's history-entry skip. They run
without a Tk root - the controller is built with ``__new__``.

Run: python -m unittest tests.test_zero_score_discard
"""

import tempfile
import unittest
from unittest import mock

from mimora import config


class _FakeResult:
    """PronunciationResult stand-in with the fields show_feedback reads.

    ``score`` is the raw 0-100 engine score (authoritative for the discard);
    ``grade_value``/``bucket`` emulate a graded phoneme take so the test
    proves the raw score decides, not the tally value.
    """

    scored = True
    bucket = 4
    grade = "4"
    grade_value = 4.0
    user_percent = 90.0
    weak_phonemes = []
    reference_words = [{"word": "a", "level": "good"}]
    prosody = {}
    transcription = "a"
    fluency_axis = -1.0
    speed_label = ""
    shadowing_axis = -1.0
    delay_sec = 0.0
    overlap = 0.0
    passed = False

    def __init__(self, score=0.0, passed=False):
        self.score = score
        self.passed = passed
        self.error_words = []
        self.phoneme_scores = []
        self.transcript = ""


class ZeroScoreDiscardTests(unittest.TestCase):
    """on_take_scored must discard a 0-score take from every record."""

    def setUp(self):
        from mimora.app import PronunciationTrainerGUI
        self.c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        self.c.session = mock.MagicMock()
        self.c.view = mock.MagicMock()
        self.c.last_user_audio = None
        self.c._last_take_audio = None
        self.c._drill_view = None
        self.c._drill_auto_next = False
        self.c._review_enabled = True
        self.c._review_store = None
        self.c._record_review_result = mock.MagicMock()
        self.c._persist_take = mock.MagicMock()
        self.c.root = mock.MagicMock()
        self._orig_stats = config.STATS_ENABLED
        self._orig_auto = config.AUTO_NEXT_PHRASE
        self._orig_gate = config.PASS_GATING_ENABLED
        config.STATS_ENABLED = True
        config.AUTO_NEXT_PHRASE = False
        config.PASS_GATING_ENABLED = False

    def tearDown(self):
        config.STATS_ENABLED = self._orig_stats
        config.AUTO_NEXT_PHRASE = self._orig_auto
        config.PASS_GATING_ENABLED = self._orig_gate

    def test_zero_score_skips_tally_and_persist(self):
        self.c.on_take_scored("phrase", 0.0, False,
                              {"result": _FakeResult(score=0.0)})
        self.c.session.record_take.assert_not_called()
        self.c._record_review_result.assert_not_called()
        self.c._persist_take.assert_not_called()

    def test_zero_score_from_result_skips(self):
        # score arg could be non-zero while result.score is 0: the raw result
        # score is authoritative.
        self.c.on_take_scored("phrase", 50.0, False,
                              {"result": _FakeResult(score=0.0)})
        self.c.session.record_take.assert_not_called()
        self.c._persist_take.assert_not_called()

    def test_nonzero_score_records(self):
        self.c.on_take_scored("phrase", 80.0, False,
                              {"result": _FakeResult(score=80.0, passed=True)})
        self.c.session.record_take.assert_called_once()
        self.c._persist_take.assert_called_once()
        self.c._record_review_result.assert_called_once_with("phrase", True)

    def test_drill_zero_score_not_recorded(self):
        self.c._original_on_take_scored = mock.MagicMock()
        engine = mock.MagicMock()
        self.c._drill_engine = engine
        self.c._drill_on_take_scored("word", 0.0, False,
                                     {"result": _FakeResult(score=0.0)})
        engine.record_take.assert_not_called()
        # But the original (main-loop) path still ran; its own guard discards.
        self.c._original_on_take_scored.assert_called_once()

    def test_drill_nonzero_score_recorded(self):
        self.c._original_on_take_scored = mock.MagicMock()
        engine = mock.MagicMock()
        self.c._drill_engine = engine
        self.c._drill_on_take_scored("word", 70.0, False,
                                     {"result": _FakeResult(score=70.0)})
        engine.record_take.assert_called_once_with("word", 70.0)


class ZeroScoreViewTests(unittest.TestCase):
    """show_feedback must skip the history row for a 0-score take."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from mimora.ui import TrainerView, ViewCallbacks
        cls.root = tk.Tk()
        cls.root.withdraw()
        callbacks = ViewCallbacks(
            on_gui_btn_press=lambda: None, on_gui_btn_release=lambda: None,
            on_generate_phrase=lambda: None, play_reference=lambda: None,
            play_reference_slow=lambda: None, play_user_recording=lambda: None,
            on_ref_long_press=lambda x, y: None,
            on_slow_long_press=lambda x, y: None,
            on_next_long_press=lambda x, y: None,
            on_settings_clicked=lambda: None, on_history_clicked=lambda: None,
            on_stats_clicked=lambda: None, on_weakness_clicked=lambda: None,
            on_drill_clicked=lambda: None, on_dictation_clicked=lambda: None,
            on_practice_collapsed_toggled=lambda: None, on_show_face_toggled=lambda: None,
            on_prosody_toggled=lambda: None, on_test_reference=lambda: None,
            on_sound_example=lambda w: None, on_word_clicked=lambda w: None,
            on_word_drill_clicked=lambda w: None,
            on_word_single_drill_clicked=lambda w: None,
            on_take_scored=lambda *a, **k: None,
            on_history_entry=lambda e: None, on_replay_take=lambda a, b: None,
        )
        cls.view = TrainerView(cls.root, callbacks)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_zero_score_skips_history_entry(self):
        fired = []
        self.view._cb = mock.MagicMock()
        self.view._cb.on_take_scored = lambda *a, **k: fired.append(("scored", a))
        self.view._cb.on_history_entry = lambda e: fired.append(("history", e))
        result = _FakeResult(score=0.0)
        self.view.show_feedback(result, "phrase", has_recording=True)
        kinds = [k for k, _ in fired]
        self.assertNotIn("history", kinds)
        # The take-scored callback is also skipped for 0 (controller discards).
        self.assertNotIn("scored", kinds)

    def test_nonzero_score_adds_history_entry(self):
        fired = []
        self.view._cb = mock.MagicMock()
        self.view._cb.on_take_scored = lambda *a, **k: fired.append(("scored", a))
        self.view._cb.on_history_entry = lambda e: fired.append(("history", e))
        result = _FakeResult(score=75.0)
        self.view.show_feedback(result, "phrase", has_recording=True)
        kinds = [k for k, _ in fired]
        self.assertIn("history", kinds)
        self.assertIn("scored", kinds)


if __name__ == "__main__":
    unittest.main()
