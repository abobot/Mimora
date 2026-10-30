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


class RangeFilterTest(unittest.TestCase):
    """The statistics-range selector slices takes by days or by count."""

    def _take(self, days_ago):
        import datetime
        ts = (datetime.datetime.now().astimezone()
              - datetime.timedelta(days=days_ago)).isoformat()
        return ({"id": "s"}, {"timestamp": ts, "score": 1.0})

    def test_days_window_keeps_only_recent(self):
        from mimora.ui_weakness import _filter_takes_by_range
        takes = [self._take(d) for d in (1, 3, 10, 40)]
        out = _filter_takes_by_range(takes, "last_7d")
        self.assertEqual(len(out), 2)  # days 1 and 3

    def test_count_window_keeps_last_n(self):
        from mimora.ui_weakness import _filter_takes_by_range
        takes = [self._take(d) for d in (1, 2, 3, 4, 5)]
        out = _filter_takes_by_range(takes, "last_30")
        self.assertEqual(len(out), 5)  # all 5 (fewer than 30)

    def test_unknown_range_returns_all(self):
        from mimora.ui_weakness import _filter_takes_by_range
        takes = [self._take(d) for d in (1, 2, 3)]
        out = _filter_takes_by_range(takes, "bogus")
        self.assertEqual(len(out), 3)

    def test_days_window_ignores_missing_timestamp(self):
        from mimora.ui_weakness import _filter_takes_by_range
        takes = [({"id": "s"}, {"timestamp": None, "score": 1.0}),
                 self._take(1)]
        out = _filter_takes_by_range(takes, "last_7d")
        self.assertEqual(len(out), 1)  # only the dated one


class DrillButtonLeftRightTest(unittest.TestCase):
    """The drill buttons bind left-click = sentence, right-click = word."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from mimora.ui_weakness import WeaknessView, WeaknessCallbacks
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.drills = []
        cls.cb = WeaknessCallbacks(
            load_takes=lambda: ([], []),
            start_drill=lambda t: cls.drills.append(t),
            open_practice=lambda: None,
            play_example=lambda w: None,
        )
        cls.view = WeaknessView(cls.root, cls.cb, native_language="zh")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_make_drill_button_left_sentence_right_word(self):
        btn = self.view._make_drill_button(
            self.view._tab_words, "Drill",
            {"type": "word", "value": "cat"},
            {"type": "single_word", "value": "cat"},
        )
        # Left-click fires the command (sentence drill).
        btn.invoke()
        self.assertEqual(self.drills[-1], {"type": "word", "value": "cat"})
        # Right-click fires the word-only drill.
        btn.event_generate("<Button-3>")
        self.assertEqual(self.drills[-1], {"type": "single_word", "value": "cat"})

    def test_make_drill_button_no_word_falls_back(self):
        btn = self.view._make_drill_button(
            self.view._tab_words, "Drill",
            {"type": "phoneme", "value": "θ"},
            None,
        )
        btn.invoke()
        self.assertEqual(self.drills[-1], {"type": "phoneme", "value": "θ"})
        btn.event_generate("<Button-3>")
        # No word target: right-click uses the same sentence target.
        self.assertEqual(self.drills[-1], {"type": "phoneme", "value": "θ"})


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


class RangeTitleUpdatesTest(unittest.TestCase):
    """Selecting a range in the dropdown must update the Overview title.

    Regression for the user report "choosing a range changes nothing": the
    StringVar was not bound to the combobox as textvariable, so
    _on_range_selected always read the default range and the title never
    changed. The combobox is driven by the localized label, so the selected
    label must be mapped back to its key before refreshing.
    """

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from mimora.ui_weakness import WeaknessView, WeaknessCallbacks
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.cb = WeaknessCallbacks(
            load_takes=lambda: ([], []),
            start_drill=lambda t: None,
            open_practice=lambda: None,
            play_example=lambda w: None,
        )
        cls.view = WeaknessView(cls.root, cls.cb, native_language="zh")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _overview_title(self):
        from mimora import i18n
        labels = self.view._tab_overview.winfo_children()
        # First label is the section title.
        return labels[0].cget("text") if labels else ""

    def test_range_combo_bound_to_var(self):
        """The combobox is bound to the StringVar (the bug)."""
        self.assertIsNotNone(self.view._range_combo.cget("textvariable"))

    def test_selecting_range_updates_title(self):
        """Picking a non-default range renames the Overview title."""
        labels = self.view._range_combo["values"]
        # Choose "Last 14 days" (index 1) - a different range from the default.
        self.view._range_combo.current(1)
        self.view._range_var.set(labels[1])
        self.view._on_range_selected({k: v for k, v in zip(
            ("last_7d", "last_14d", "last_30d", "last_60d",
             "last_30", "last_100", "last_300"), labels)})
        title = self._overview_title()
        self.assertIn("Last 14 days", title)

    def test_default_title_mentions_30_days(self):
        self.view._range_key = "last_30d"
        self.view.refresh()
        title = self._overview_title()
        self.assertIn("30", title)


if __name__ == "__main__":
    unittest.main()
