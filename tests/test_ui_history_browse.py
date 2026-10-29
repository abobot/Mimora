# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P2#6 history "Re-drill" button.

Covers:
  * HistoryCallbacks accepts the optional ``start_drill`` callback,
  * the pure target helper app._drill_target_for_take picks the right
    target for a take with error words / phoneme scores / neither,
  * the history row renders a "Re-drill" button that forwards the take.

Runs in the fast suite: a single withdrawn Tk root, no models.
"""

import unittest


def _session(sid="s-test"):
    return {
        "id": sid,
        "started_at": "2026-10-26T18:00:00+08:00",
        "ended_at": "2026-10-26T18:05:00+08:00",
        "totals": {"takes": 1, "avg_best_score": 75.0},
        "takes": [],
    }


def _take(phrase="hello world", error_words=None, phoneme_scores=None):
    return {
        "id": "t-test",
        "timestamp": "2026-10-26T18:00:00+08:00",
        "phrase": phrase,
        "score": 75.0,
        "grade": "3",
        "grade_value": 3.0,
        "attempts": 1,
        "audio_duration_sec": 2.0,
        "transcript": phrase,
        "words_per_minute": 60.0,
        "error_words": error_words or [],
        "phoneme_scores": phoneme_scores or [],
        "session_id": "s-test",
    }


class HistoryCallbacksTest(unittest.TestCase):
    def test_start_drill_defaults_to_none(self):
        from mimora.ui_history_browse import HistoryCallbacks
        cb = HistoryCallbacks(
            load_sessions=lambda: [],
            load_drill_sessions=lambda: [],
            load_dictation_sessions=lambda: [],
            delete_take=lambda s, t: None,
            delete_session=lambda s: None,
            clear_all=lambda: None,
            play_recording=lambda s, t: None,
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        self.assertIsNone(cb.start_drill)

    def test_start_drill_accepts_callable(self):
        from mimora.ui_history_browse import HistoryCallbacks
        calls = []
        cb = HistoryCallbacks(
            load_sessions=lambda: [],
            load_drill_sessions=lambda: [],
            load_dictation_sessions=lambda: [],
            delete_take=lambda s, t: None,
            delete_session=lambda s: None,
            clear_all=lambda: None,
            play_recording=lambda s, t: None,
            export_json=lambda: None,
            export_csv=lambda: None,
            start_drill=lambda t: calls.append(t),
        )
        take = _take()
        cb.start_drill(take)
        self.assertEqual(calls, [take])


class DrillTargetForTakeTest(unittest.TestCase):
    """The pure target helper: what target a past take maps to."""

    def _target(self, **take_fields):
        from mimora.app import PronunciationTrainerGUI  # noqa: F401
        # The helper is a staticmethod on the GUI class; import it directly.
        from mimora.app import PronunciationTrainerGUI as G
        return G._drill_target_for_take(_take(**take_fields))

    def test_error_word_is_target(self):
        t = self._target(error_words=[{"word": "helping", "level": "bad"}])
        self.assertEqual(t, {"type": "word", "value": "helping"})

    def test_first_of_many_error_words_wins(self):
        t = self._target(error_words=[
            {"word": "alpha", "level": "bad"},
            {"word": "beta", "level": "bad"},
        ])
        self.assertEqual(t, {"type": "word", "value": "alpha"})

    def test_phoneme_fallback_when_no_word_errors(self):
        t = self._target(phoneme_scores=[{"ipa": "θ", "score": 0.2, "count": 2}])
        self.assertEqual(t, {"type": "phoneme", "value": "θ"})

    def test_free_form_fallback_when_nothing_failed(self):
        t = self._target(error_words=[], phoneme_scores=[])
        self.assertEqual(t, {"type": "free_form", "value": "hello world"})

    def test_free_form_fallback_on_empty_phrase(self):
        t = self._target(phrase="")
        self.assertEqual(t["type"], "free_form")
        self.assertTrue(t["value"])


class ReDrillRowButtonTest(unittest.TestCase):
    """The history row shows a Re-drill button that forwards the take."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from mimora.ui_history_browse import HistoryBrowseView, HistoryCallbacks
        cls.drills = []
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.cb = HistoryCallbacks(
            load_sessions=lambda: [{
                "id": "s-1",
                "started_at": "2026-10-26T18:00:00+08:00",
                "ended_at": "2026-10-26T18:05:00+08:00",
                "totals": {"takes": 1, "avg_best_score": 75.0},
                "takes": [_take()],
            }],
            load_drill_sessions=lambda: [],
            load_dictation_sessions=lambda: [],
            delete_take=lambda s, t: None,
            delete_session=lambda s: None,
            clear_all=lambda: None,
            play_recording=lambda s, t: None,
            export_json=lambda: None,
            export_csv=lambda: None,
            start_drill=lambda t: cls.drills.append(t),
        )
        cls.view = HistoryBrowseView(cls.root, cls.cb)
        # Expand the session so its take rows are built.
        cls.view._expanded.add("s-1")
        cls.view._render()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _collect(self, widget, text):
        import tkinter as tk
        out = []
        if isinstance(widget, tk.Label):
            try:
                if widget.cget("text") == text:
                    out.append(widget)
            except Exception:
                pass
        for child in widget.winfo_children():
            out.extend(self._collect(child, text))
        return out

    def test_redrill_button_is_rendered(self):
        buttons = self._collect(self.view._body, "Re-drill")
        self.assertGreaterEqual(len(buttons), 1)

    def test_redrill_button_forwards_take(self):
        buttons = self._collect(self.view._body, "Re-drill")
        self.assertGreaterEqual(len(buttons), 1)
        buttons[0].event_generate("<Button-1>")
        self.assertEqual(len(self.drills), 1)
        self.assertEqual(self.drills[0]["id"], "t-test")


if __name__ == "__main__":
    unittest.main()
