# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P2#8 interactive IPA chart (weakness phoneme tab).

Covers:
  * WeaknessCallbacks accepts the new ``play_example`` callback (default None),
  * the phoneme card renders a "Play" button when an example word is known
    and a play_example callback is provided,
  * clicking that button calls play_example with the per-language example word,
  * a phoneme with no example word gets no Play button (graceful).

Runs in the fast suite: a single withdrawn Tk root, no models, no network.
The example-word lookup is per-language via ``example_for``, so this also
covers that the Mandarin table keeps tone-digit tokens working.
"""

import unittest
from tkinter import ttk

from mimora import config


def _session(sid="s-test"):
    return {"id": sid, "start": "2026-10-26T18:00:00+08:00", "totals": {}}


def _take(phoneme_scores=None, phrase="hello"):
    take = {
        "id": "t-test",
        "timestamp": "2026-10-26T18:00:00+08:00",
        "phrase": phrase,
        "score": 75.0,
        "grade": "",
        "grade_value": 0.0,
        "attempts": 1,
        "audio_duration_sec": 2.0,
        "transcript": phrase,
        "words_per_minute": 60.0,
        "error_words": [],
        "phoneme_scores": phoneme_scores or [],
        "session_id": "s-test",
    }
    return take


class WeaknessCallbacksTest(unittest.TestCase):
    """The callback contract gained an optional play_example."""

    def test_play_example_defaults_to_none(self):
        from mimora.ui_weakness import WeaknessCallbacks
        cb = WeaknessCallbacks(
            load_takes=lambda: ([], []),
            start_drill=lambda t: None,
            open_practice=lambda: None,
        )
        self.assertIsNone(cb.play_example)

    def test_play_example_accepts_callable(self):
        from mimora.ui_weakness import WeaknessCallbacks
        calls = []
        cb = WeaknessCallbacks(
            load_takes=lambda: ([], []),
            start_drill=lambda t: None,
            open_practice=lambda: None,
            play_example=lambda w: calls.append(w),
        )
        cb.play_example("put")
        self.assertEqual(calls, ["put"])


class InteractiveIpaChartTest(unittest.TestCase):
    """The phoneme tab renders a Play button and forwards the example word."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.plays = []
        cls.drills = []
        from mimora.ui_weakness import WeaknessView, WeaknessCallbacks
        cls.cb = WeaknessCallbacks(
            load_takes=lambda: ([], []),
            start_drill=lambda t: cls.drills.append(t),
            open_practice=lambda: None,
            play_example=lambda w: cls.plays.append(w),
        )
        cls.view = WeaknessView(cls.root, cls.cb, native_language="zh")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _takes(self):
        return [(_session(), _take(phoneme_scores=[
            {"ipa": "θ", "score": 0.2, "count": 2},
            {"ipa": "x", "score": 0.9, "count": 1},
        ]))]

    def _collect_play_buttons(self):
        """Walk the phoneme tab for ttk Buttons whose text is 'Play'."""
        buttons = []
        for widget in self._tab_phonemes().winfo_children():
            buttons.extend(self._walk(widget))
        return buttons

    def _walk(self, widget):
        out = []
        try:
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Play":
                out.append(widget)
        except Exception:
            pass
        for child in widget.winfo_children():
            out.extend(self._walk(child))
        return out

    def _tab_phonemes(self):
        # The notebook's third tab (index 2) is the phonemes tab.
        return self.view._tab_phonemes

    def test_phoneme_with_known_example_gets_play_button(self):
        # θ has an English example word ("think") so a Play button must appear.
        self.view._render_phonemes(aggregate(self._takes()))
        plays = self._collect_play_buttons()
        self.assertGreaterEqual(len(plays), 1)

    def test_clicking_play_calls_backback_with_example(self):
        self.view._render_phonemes(aggregate(self._takes()))
        plays = self._collect_play_buttons()
        self.assertGreaterEqual(len(plays), 1)
        plays[0].invoke()
        self.assertGreaterEqual(len(self.plays), 1)
        # The example for /θ/ is "think" (English table).
        self.assertEqual(self.plays[-1], "think")

    def test_unknown_phoneme_has_no_play_button(self):
        # Only the "x" phoneme (no example in any table) is present.
        self.view._render_phonemes(aggregate([(_session(), _take(phoneme_scores=[
            {"ipa": "x", "score": 0.9, "count": 1},
        ]))]))
        plays = self._collect_play_buttons()
        self.assertEqual(plays, [])

    def test_mandarin_tone_token_resolves_example(self):
        # A Mandarin tone-bearing token like "i5" must resolve to "一" through
        # the per-language table (example_for strips the tone digit).
        from mimora.phoneme_examples import example_for
        # Force the active language to mandarin for this lookup only.
        orig = config.PRACTICE_LANGUAGE
        config.PRACTICE_LANGUAGE = "mandarin"
        try:
            self.assertEqual(example_for("i5"), "一")
            self.assertEqual(example_for("ɑu2"), "好")
        finally:
            config.PRACTICE_LANGUAGE = orig


def aggregate(takes):
    """Small helper: aggregate_phoneme_scores needs [(session, take), ...]."""
    from mimora.analysis import aggregate_phoneme_scores
    return aggregate_phoneme_scores(takes, native_language="zh")


if __name__ == "__main__":
    unittest.main()
