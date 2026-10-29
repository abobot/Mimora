# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the speed / phrase-source features (P1#5).

Covers:
  * _time_stretch - the pitch-preserving slow playback helper (C),
  * the phrase-source toggle branch in _generate_and_prompt (D),
  * FlatButton long-press - fires the long-press action and suppresses the
    click (A/B mechanism),
  * the long-press speed / slow popups and the source-mode handler.

Run: python -m unittest tests.test_speed_and_source
"""

import tkinter as tk
import unittest
from unittest import mock

import numpy as np

from mimora import config


class TimeStretchTests(unittest.TestCase):
    """The pitch-preserving time-stretch helper (app.py _time_stretch)."""

    def _sin(self, sr=24000, seconds=0.5, freq=220.0):
        t = np.arange(int(sr * seconds))
        return np.sin(2 * np.pi * freq * t / sr).astype(np.float32)

    def test_speed_1_returns_input_unchanged(self):
        from mimora.app import _time_stretch
        y = self._sin()
        out = _time_stretch(y, 1.0)
        np.testing.assert_array_equal(out, y)
        self.assertEqual(out.dtype, np.float32)

    def test_empty_input_returns_empty(self):
        from mimora.app import _time_stretch
        out = _time_stretch(np.zeros(0, dtype=np.float32), 0.5)
        self.assertEqual(out.size, 0)

    def test_output_is_finite(self):
        # WSOLA must never produce NaN/Inf, even on a noisy input.
        from mimora.app import _time_stretch
        rng = np.random.RandomState(3)
        y = rng.randn(24000).astype(np.float32)
        out = _time_stretch(y, 0.5)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_slow_output_preserves_pitch_freq(self):
        # A pure 220 Hz tone stretched to 0.5x keeps its zero-crossing density
        # (pitch), i.e. it is a time-stretch and not a resample.
        from mimora.app import _time_stretch
        y = self._sin()
        out = _time_stretch(y, 0.5)
        crosses = np.sum(np.sign(out[1:]) != np.sign(out[:-1]))
        seconds = len(out) / 24000.0
        self.assertAlmostEqual(crosses / 2 / seconds, 220.0, delta=10.0)

    def test_slow_speed_makes_longer(self):
        from mimora.app import _time_stretch
        y = self._sin()
        out = _time_stretch(y, 0.5)
        self.assertGreater(len(out), len(y))
        self.assertEqual(out.dtype, np.float32)

    def test_fast_speed_makes_shorter(self):
        from mimora.app import _time_stretch
        y = self._sin()
        out = _time_stretch(y, 2.0)
        self.assertLess(len(out), len(y))

    def test_pitch_is_preserved(self):
        # A pure tone time-stretched at 1.0x keeps its frequency; at 0.5x the
        # length doubles but the tone's zero-crossing density (pitch) is
        # unchanged, unlike the old lowered-sample-rate resample that halved it.
        from mimora.app import _time_stretch
        y = self._sin()
        out = _time_stretch(y, 0.5)
        # Count sign changes per second in the output: ~2*freq for the original.
        crosses = np.sum(np.sign(out[1:]) != np.sign(out[:-1]))
        seconds = len(out) / 24000.0
        self.assertAlmostEqual(crosses / 2 / seconds, 220.0, delta=8.0)


class PhraseSourceBranchTests(unittest.TestCase):
    """_generate_and_prompt picks the provider by phrase_source (D)."""

    def _controller(self, mode):
        from mimora.app import PronunciationTrainerGUI
        c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        c._phrase_source_mode = mode
        c._source_text_provider = mock.MagicMock()
        c._source_text_provider.generate_phrase.return_value = "from practice text."
        c.llm_mgr = mock.MagicMock()
        c.llm_mgr.generate_phrase.return_value = "from llm."
        c.tts_mgr = mock.MagicMock()
        c.tts_mgr.sample_rate = 24000
        c.tts_mgr.synthesize.return_value = np.zeros(2400, dtype=np.float32)
        c.playback = mock.MagicMock()
        c.root = mock.MagicMock()
        c.root.after = lambda _ms, _fn, *_a: None
        c.current_phrase = None
        c.shutdown_event = mock.MagicMock()
        # _translate_into_panel is reached after generation; stub it out.
        c.translator_mgr = mock.MagicMock()
        c._translate_into_panel = mock.MagicMock()
        return c

    def _run(self, controller):
        # The worker is synchronous when called directly; mock the async
        # dispatch used for UI so nothing needs a real event loop.
        controller.playback.new_event.return_value = mock.MagicMock()
        controller._generate_and_prompt("Source text.", "full", "en", "voice",
                                        1.0, controller._phrase_source_mode,
                                        mock.MagicMock())

    def test_practice_mode_uses_source_provider(self):
        c = self._controller("practice")
        self._run(c)
        c._source_text_provider.generate_phrase.assert_called_once_with(
            "Source text.", length="full")
        c.llm_mgr.generate_phrase.assert_not_called()
        self.assertEqual(c.current_phrase, "from practice text.")

    def test_llm_mode_uses_llm_manager(self):
        c = self._controller("llm")
        self._run(c)
        c.llm_mgr.generate_phrase.assert_called_once_with(
            "Source text.", length="full")
        c._source_text_provider.generate_phrase.assert_not_called()
        self.assertEqual(c.current_phrase, "from llm.")

    def test_source_mode_handler_updates_mode_and_persists(self):
        from mimora.app import PronunciationTrainerGUI
        c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        c._phrase_source_mode = "llm"
        c.settings_ctl = mock.MagicMock()
        old = config.PHRASE_SOURCE
        try:
            PronunciationTrainerGUI._on_phrase_source_changed(c, "practice")
            self.assertEqual(c._phrase_source_mode, "practice")
            self.assertEqual(config.PHRASE_SOURCE, "practice")
            c.settings_ctl.persist.assert_called_once_with("phrase_source", "practice")
        finally:
            config.PHRASE_SOURCE = old

    def test_source_mode_handler_ignores_unknown(self):
        from mimora.app import PronunciationTrainerGUI
        c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        c._phrase_source_mode = "llm"
        c.settings_ctl = mock.MagicMock()
        PronunciationTrainerGUI._on_phrase_source_changed(c, "bogus")
        self.assertEqual(c._phrase_source_mode, "llm")
        c.settings_ctl.persist.assert_not_called()


class LongPressTests(unittest.TestCase):
    """FlatButton long-press: fires the action, suppresses the click (A/B)."""

    def _button(self, root):
        from mimora.ui_theme import FlatButton
        calls = {"long": 0, "click": 0}
        btn = FlatButton(root, text="X", cursor="hand2",
                         command=lambda: calls.__setitem__("click", calls["click"] + 1),
                         on_long_press=lambda: calls.__setitem__("long", calls["long"] + 1))
        btn.pack()
        return btn, calls

    def _press(self, btn):
        ev = tk.Event(); ev.x = 5; ev.y = 5
        btn._on_press(ev)

    def _release(self, btn):
        ev = tk.Event(); ev.x = 5; ev.y = 5
        btn._on_release(ev)

    def test_short_click_fires_command_not_long(self):
        root = tk.Tk()
        try:
            btn, calls = self._button(root)
            root.update()
            self._press(btn)
            self._release(btn)
            self.assertEqual(calls["long"], 0)
            self.assertEqual(calls["click"], 1)
        finally:
            root.destroy()

    def test_long_press_fires_action_and_suppresses_click(self):
        from mimora.ui_theme import LONG_PRESS_DELAY_MS
        root = tk.Tk(); root.withdraw()
        try:
            btn, calls = self._button(root)
            root.update()
            self._press(btn)
            root.after(LONG_PRESS_DELAY_MS + 50, root.quit)
            root.mainloop()  # lets the after-timer fire the long-press
            self._release(btn)
            self.assertEqual(calls["long"], 1)
            self.assertEqual(calls["click"], 0)
        finally:
            root.destroy()

    def test_no_long_press_when_disabled(self):
        root = tk.Tk(); root.withdraw()
        try:
            btn, calls = self._button(root)
            btn.config(state=tk.DISABLED)
            root.update()
            self._press(btn)
            self.assertEqual(calls["long"], 0)
        finally:
            root.destroy()


class PopupTests(unittest.TestCase):
    """The long-press popups build a window and play on confirm."""

    def _controller(self):
        from mimora.app import PronunciationTrainerGUI
        c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        c.root = mock.MagicMock()
        c.root.after = lambda *_a, **_k: None
        c.reference_audio = np.zeros(2400, dtype=np.float32)
        c.tts_mgr = mock.MagicMock()
        c.tts_mgr.sample_rate = 24000
        c.playback = mock.MagicMock()
        c._play_reference_at = mock.MagicMock()
        return c

    def test_ref_long_press_opens_popup_and_plays_on_confirm(self):
        root = tk.Tk(); root.withdraw()
        try:
            c = self._controller()
            c.root.winfo_children = mock.MagicMock(return_value=[])
            # Patch _make_popup to return a real (hidden) Toplevel so the
            # confirm path can destroy it.
            def fake_popup(_x, _y):
                return tk.Toplevel(root), tk.Toplevel(root)
            c._make_popup = fake_popup
            from mimora.app import PronunciationTrainerGUI
            PronunciationTrainerGUI._on_ref_long_press(c, 100, 100)
            self.assertTrue(c._play_reference_at.called is False)  # not yet
        finally:
            root.destroy()

    def test_confirm_popup_plays_at_speed(self):
        c = self._controller()
        win = mock.MagicMock()
        catcher = mock.MagicMock()
        from mimora.app import PronunciationTrainerGUI
        PronunciationTrainerGUI._confirm_popup(c, win, catcher, 0.8)
        win.destroy.assert_called_once()
        c._play_reference_at.assert_called_once_with(0.8)

    def test_confirm_popup_persists_speed_when_requested(self):
        # The 🔊 speed popup asks to persist: the chosen speed becomes the new
        # default (config.REFERENCE_SPEED) and is written to settings.json, so
        # the next phrase is generated and auto-played at this speed (issue 1).
        from mimora.app import PronunciationTrainerGUI
        c = self._controller()
        c.settings_ctl = mock.MagicMock()
        old = config.REFERENCE_SPEED
        try:
            PronunciationTrainerGUI._confirm_popup(
                c, mock.MagicMock(), mock.MagicMock(), 0.9, persist=True)
            self.assertEqual(config.REFERENCE_SPEED, 0.9)
            c.settings_ctl.persist.assert_called_once_with("reference_speed", 0.9)
            c._play_reference_at.assert_called_once_with(0.9)
        finally:
            config.REFERENCE_SPEED = old

    def test_confirm_popup_does_not_persist_slow_multiplier(self):
        # The 🐢 slow popup stays a one-shot transient; it must not rewrite the
        # default speed, so the Slow ▶ button keeps its relative meaning.
        from mimora.app import PronunciationTrainerGUI
        c = self._controller()
        c.settings_ctl = mock.MagicMock()
        old = config.REFERENCE_SPEED
        try:
            PronunciationTrainerGUI._confirm_popup(
                c, mock.MagicMock(), mock.MagicMock(), 0.7)
            self.assertEqual(config.REFERENCE_SPEED, old)
            c.settings_ctl.persist.assert_not_called()
        finally:
            config.REFERENCE_SPEED = old

    def test_dismiss_popup_tolerates_already_destroyed(self):
        c = self._controller()
        from mimora.app import PronunciationTrainerGUI
        gone = mock.MagicMock()
        gone.winfo_exists.return_value = False
        # Must not raise even if a widget is already gone.
        PronunciationTrainerGUI._dismiss_popup(c, gone, None)

    def test_next_long_press_builds_source_popup(self):
        root = tk.Tk(); root.withdraw()
        try:
            c = self._controller()
            c._phrase_source_mode = "llm"
            def fake_popup(_x, _y):
                return tk.Toplevel(root), tk.Toplevel(root)
            c._make_popup = fake_popup
            from mimora.app import PronunciationTrainerGUI
            # Should not raise; builds the LLM/Text popup.
            PronunciationTrainerGUI._on_next_long_press(c, 100, 100)
        finally:
            root.destroy()

    def test_choose_phrase_source_applies_and_dismisses(self):
        c = self._controller()
        c._phrase_source_mode = "llm"
        c.settings_ctl = mock.MagicMock()
        win = mock.MagicMock(); catcher = mock.MagicMock()
        from mimora.app import PronunciationTrainerGUI
        PronunciationTrainerGUI._choose_phrase_source(c, "practice", win, catcher)
        self.assertEqual(c._phrase_source_mode, "practice")
        win.destroy.assert_called_once()
        catcher.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
