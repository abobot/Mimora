# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P1#4 shadowing wiring in the app controller.

Validates:
  * the config keys are known/defaulted and the settings window exposes them,
  * _apply_shadowing computes the axis, blends it into the score and re-derives
    the verdict exactly like _apply_fluency does,
  * the hero card's set_shadowing hides the line for an uncomputed axis and
    shows a formatted read-out otherwise.

The shadowing math itself is covered by tests/test_shadowing.py; here we only
exercise the host-side blend + the view read-out, with a mocked shadowing
module so no audio model is needed.

Run from the project root with:

    python -m unittest tests.test_shadowing_wiring
"""

import unittest
from unittest import mock

import numpy as np

from mimora import config


class ConfigKeysTests(unittest.TestCase):
    def test_constants_exist_with_safe_defaults(self):
        self.assertTrue(hasattr(config, "SHADOWING_ENABLED"))
        self.assertTrue(hasattr(config, "SHADOWING_WEIGHT"))
        self.assertFalse(config.SHADOWING_ENABLED)
        self.assertGreaterEqual(config.SHADOWING_WEIGHT, 0.0)
        self.assertLessEqual(config.SHADOWING_WEIGHT, 100.0)

    def test_keys_are_known_and_defaulted(self):
        for key in ("shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, config._KNOWN_USER_KEYS, key)
            self.assertIn(key, config.USER_SETTING_DEFAULTS, key)

    def test_settings_window_exposes_fields(self):
        from mimora.settings_window import all_fields
        keys = {field.key for field in all_fields()}
        for key in ("shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, keys, key)

    def test_live_apply_mapping(self):
        from mimora.settings_ctl import LIVE_CONFIG_ATTRS
        for key in ("shadowing_enabled", "shadowing_weight"):
            self.assertIn(key, LIVE_CONFIG_ATTRS, key)


class _FakeResult:
    """Minimal result the app's _apply_shadowing mutates."""
    score = 70.0
    bucket = 3
    user_percent = 75.0
    grade = "3"
    grade_value = 3.0
    passed = True
    shadowing_axis = -1.0
    delay_sec = 0.0
    overlap = 0.0


class ApplyShadowingTests(unittest.TestCase):
    """The controller's blend of the shadowing axis (app.py _apply_shadowing)."""

    def _controller(self):
        # A bare object exposing only the attributes _apply_shadowing reads.
        c = mock.MagicMock()
        c.reference_audio = np.array([0.1, -0.1] * 400, dtype=np.float32)
        c.tts_mgr = mock.MagicMock()
        c.tts_mgr.sample_rate = 16000
        return c

    def test_computes_and_blends(self):
        from mimora.app import PronunciationTrainerGUI
        controller = self._controller()
        result = _FakeResult()
        with mock.patch("mimora.shadowing.compute_shadowing",
                        return_value={"shadowing_axis": 0.8, "delay_sec": 0.2,
                                      "overlap": 0.9, "lag_penalty": 1.0}):
            PronunciationTrainerGUI._apply_shadowing(controller, result,
                                                     np.zeros(1600, dtype=np.float32),
                                                     16000)
        # 30% weight default: 0.7*70 + 0.3*(0.8*100) = 49 + 24 = 73.0
        self.assertAlmostEqual(result.score, 73.0, places=1)
        self.assertAlmostEqual(result.shadowing_axis, 80.0, places=1)
        self.assertAlmostEqual(result.delay_sec, 0.2, places=3)
        self.assertAlmostEqual(result.overlap, 0.9, places=3)

    def test_computation_failure_keeps_score(self):
        from mimora.app import PronunciationTrainerGUI
        controller = self._controller()
        result = _FakeResult()
        with mock.patch("mimora.shadowing.compute_shadowing", side_effect=RuntimeError("boom")):
            PronunciationTrainerGUI._apply_shadowing(controller, result,
                                                     np.zeros(1600, dtype=np.float32),
                                                     16000)
        self.assertEqual(result.score, 70.0)
        self.assertEqual(result.shadowing_axis, -1.0)

    def test_zero_weight_computes_but_does_not_blend(self):
        from mimora.app import PronunciationTrainerGUI
        controller = self._controller()
        result = _FakeResult()
        with mock.patch.object(config, "SHADOWING_WEIGHT", 0.0), \
             mock.patch("mimora.shadowing.compute_shadowing",
                        return_value={"shadowing_axis": 0.5, "delay_sec": 0.0,
                                      "overlap": 0.5, "lag_penalty": 1.0}):
            PronunciationTrainerGUI._apply_shadowing(controller, result,
                                                     np.zeros(1600, dtype=np.float32),
                                                     16000)
        self.assertEqual(result.score, 70.0)  # unchanged
        self.assertAlmostEqual(result.shadowing_axis, 50.0, places=1)


class HeroShadowingTests(unittest.TestCase):
    """The hero-card read-out for the shadowing axis (ui_hero.set_shadowing)."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_hero import HeroCard
        from tests._tk import make_root
        cls.root = make_root()
        cls.hero = HeroCard(cls.root, on_word_clicked=lambda w: None,
                            on_sound_example=lambda w: None)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_hidden_when_not_computed(self):
        self.hero.set_shadowing(-1.0, 0.0, 0.0)
        self.assertEqual(self.hero.shadowing_label.cget("text"), "")

    def test_shown_when_computed(self):
        self.hero.set_shadowing(85.0, 0.2, 0.9)
        text = self.hero.shadowing_label.cget("text")
        self.assertIn("85", text)
        self.assertIn("+0.20s", text)
        self.assertIn("90%", text)

    def test_reset_clears(self):
        self.hero.set_shadowing(85.0, 0.2, 0.9)
        self.hero.set_shadowing(-1.0, 0.0, 0.0)
        self.assertEqual(self.hero.shadowing_label.cget("text"), "")


if __name__ == "__main__":
    unittest.main()
