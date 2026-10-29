# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the pass-gating and fluency config wiring (P0#2/P0#3).

These validate:
  * the new config constants exist with sensible defaults,
  * every new settings key is a known user key with a matching default,
  * the settings window exposes the new fields,
  * the pass gate disables "Next phrase" in the view's show_feedback for a
    failed take (and enables it for a passing one / when the gate is off).

Run from the project root with:

    python -m unittest tests.test_pass_gating
"""

import unittest
from unittest import mock

from mimora import config


class ConfigKeysTests(unittest.TestCase):
    """The four new config constants and their settings wiring."""

    def test_new_constants_exist(self):
        self.assertTrue(hasattr(config, "FLUENCY_ENABLED"))
        self.assertTrue(hasattr(config, "FLUENCY_WEIGHT"))
        self.assertTrue(hasattr(config, "FLUENCY_REFERENCE_WPM"))
        self.assertTrue(hasattr(config, "PASS_GATING_ENABLED"))
        self.assertTrue(hasattr(config, "SHADOWING_ENABLED"))
        self.assertTrue(hasattr(config, "SHADOWING_WEIGHT"))

    def test_defaults_are_safe(self):
        # Both extensions default OFF so existing behaviour is unchanged.
        self.assertFalse(config.FLUENCY_ENABLED)
        self.assertFalse(config.PASS_GATING_ENABLED)
        self.assertFalse(config.SHADOWING_ENABLED)
        self.assertGreaterEqual(config.FLUENCY_WEIGHT, 0.0)
        self.assertLessEqual(config.FLUENCY_WEIGHT, 100.0)
        self.assertGreaterEqual(config.FLUENCY_REFERENCE_WPM, 0.0)
        self.assertGreaterEqual(config.SHADOWING_WEIGHT, 0.0)
        self.assertLessEqual(config.SHADOWING_WEIGHT, 100.0)

    def test_keys_are_known_and_defaulted(self):
        for key in ("fluency_enabled", "fluency_weight",
                    "fluency_reference_wpm", "pass_gating_enabled",
                    "shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, config._KNOWN_USER_KEYS, key)
            self.assertIn(key, config.USER_SETTING_DEFAULTS, key)

    def test_settings_window_exposes_fields(self):
        from mimora.settings_window import all_fields
        keys = {field.key for field in all_fields()}
        for key in ("fluency_enabled", "fluency_weight",
                    "fluency_reference_wpm", "pass_gating_enabled",
                    "shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, keys, key)

    def test_live_apply_mapping(self):
        from mimora.settings_ctl import LIVE_CONFIG_ATTRS
        for key in ("fluency_enabled", "fluency_weight",
                    "fluency_reference_wpm", "pass_gating_enabled",
                    "shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, LIVE_CONFIG_ATTRS, key)


class _FakeHero:
    """Minimal hero-card stand-in: records what set_score_row/set_fluency got."""

    def __init__(self):
        self.expression = None
        self.score_row = None
        self.fluency = None
        self.badges = None
        self.misses = None
        self.hint = None
        self.word_feedback = None
        # show_feedback reads hero.face.set_expression directly; a tiny stand-in.
        self.face = _FakeFace()

    def set_expression(self, e):
        self.expression = e

    def set_score_row(self, number, verdict, color):
        self.score_row = (number, verdict, color)

    def set_fluency(self, axis, speed_label):
        self.fluency = (axis, speed_label)

    def set_shadowing(self, axis, delay_sec, overlap):
        self.shadowing = (axis, delay_sec, overlap)

    def set_badges(self, badges):
        self.badges = badges

    def mark_miss_words(self, rw):
        self.misses = rw
        return bool(rw)

    def set_hint(self, show):
        self.hint = show

    def set_word_feedback(self, rw):
        self.word_feedback = rw


class _FakeFace:
    """FaceWidget stand-in: only set_expression is read by show_feedback."""

    def __init__(self):
        self.expression = None

    def set_expression(self, e):
        self.expression = e


class _FakeProsody:
    def set_prosody(self, p):
        pass

    def redraw(self):
        pass


class _FakeResult:
    """PronunciationResult stand-in with the fields show_feedback reads."""
    scored = True
    bucket = 4
    grade = "4"
    grade_value = 4.0
    user_percent = 90.0
    score = 88.0
    weak_phonemes = []
    reference_words = [{"word": "a", "level": "good"}]
    prosody = {}
    transcription = "a"
    fluency_axis = -1.0
    speed_label = ""
    shadowing_axis = -1.0
    delay_sec = 0.0
    overlap = 0.0
    passed = False  # the test flips this


class GateTests(unittest.TestCase):
    """The pass gate in TrainerView.show_feedback (via a real-ish facade)."""

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
        # Swap the real hero/prosody for stand-ins that record calls without
        # drawing, so the test asserts on captured state, not pixels.
        cls.hero = _FakeHero()
        cls.view._hero = cls.hero
        cls.view._prosody = _FakeProsody()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _drive(self, gating, passed):
        """Run show_feedback once with the given gate/passed state."""
        with mock.patch.object(config, "PASS_GATING_ENABLED", gating):
            result = _FakeResult()
            result.passed = passed
            self.view.show_feedback(result, "a phrase", has_recording=True)

    def test_failed_take_under_gate_disables_next(self):
        self._drive(gating=True, passed=False)
        self.assertEqual(str(self.view.generate_btn.cget("state")), "disabled")
        self.assertIn("Keep trying", self.view.status_label.cget("text"))

    def test_passing_take_under_gate_enables_next(self):
        self._drive(gating=True, passed=True)
        self.assertEqual(str(self.view.generate_btn.cget("state")), "normal")

    def test_gate_off_keeps_next_enabled_even_when_failed(self):
        self._drive(gating=False, passed=False)
        self.assertEqual(str(self.view.generate_btn.cget("state")), "normal")

    def test_fluency_label_rendered_when_axis_present(self):
        # A take with a computed fluency axis shows the fluency line; one without
        # (axis < 0) keeps it hidden. Drive both and assert the captured state.
        self._drive(gating=False, passed=True)
        self.assertEqual(self.hero.fluency, (-1.0, ""))  # hidden here
        with mock.patch.object(config, "PASS_GATING_ENABLED", False):
            result = _FakeResult()
            result.passed = True
            result.fluency_axis = 87.0
            result.speed_label = "natural"
            self.view.show_feedback(result, "a phrase", has_recording=True)
        self.assertGreaterEqual(self.hero.fluency[0], 0.0)
        self.assertEqual(self.hero.fluency[1], "natural")

    def test_update_hero_false_skips_hero_but_still_notifies(self):
        # A drill take scores through the shared analyze_recording path, which
        # calls show_feedback with update_hero=False. The main hero card must
        # NOT be re-rendered with the drill's phrase (that is what replaced the
        # main sentence with the drilled word), while the controller callbacks
        # (on_take_scored / on_history_entry) must still fire so the drill's
        # own bridge records and refreshes the drill progress.
        # The hero is shared across tests; reset the captured sentinels first.
        self.hero.score_row = None
        self.hero.misses = None
        self.hero.word_feedback = None
        self.hero.badges = None
        fired = []
        self.view._cb = mock.MagicMock()
        self.view._cb.on_take_scored = lambda *a, **k: fired.append(("scored", a))
        self.view._cb.on_history_entry = lambda e: fired.append(("history", e))
        result = _FakeResult()
        result.passed = True
        self.view.show_feedback(result, "drilled word", has_recording=True,
                                update_hero=False)
        # The hero card was left untouched (initial sentinels still in place).
        self.assertIsNone(self.hero.score_row)
        self.assertIsNone(self.hero.misses)
        self.assertIsNone(self.hero.word_feedback)
        self.assertIsNone(self.hero.badges)
        # The controller callbacks still ran (drill bridge needs them).
        kinds = [k for k, _ in fired]
        self.assertIn("scored", kinds)
        self.assertIn("history", kinds)
        # The take was reported with the drill phrase, not the saved sentence.
        self.assertEqual(fired[0][1][0], "drilled word")


if __name__ == "__main__":
    unittest.main()
