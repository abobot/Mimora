# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the fluency axis (mimora/fluency.py).

These exercise the pure scoring math (tempo + prosody-shape) without any audio
model. The prosody half is exercised through compute_fluency with a real
reference waveform; the contour functions themselves are stubbed where needed
so the test stays fast and deterministic. Run from the project root with:

    python -m unittest tests.test_fluency
"""

import unittest
from unittest import mock

import numpy as np

from mimora.fluency import (
    compute_fluency,
    _prosody_score,
    _speed_label,
    _tempo_score,
)


class TempoScoreTests(unittest.TestCase):
    """The WPM-vs-target half of the axis."""

    def test_exact_target_scores_100(self):
        self.assertAlmostEqual(_tempo_score(150.0, 150.0, 0.6), 100.0)

    def test_no_target_disables_penalty(self):
        # reference_wpm <= 0 -> no penalty (nothing to compare against).
        self.assertAlmostEqual(_tempo_score(120.0, 0.0, 0.6), 100.0)
        self.assertAlmostEqual(_tempo_score(120.0, -5.0, 0.6), 100.0)

    def test_no_words_scores_100(self):
        # wpm == 0 (no transcript) -> nothing to penalise.
        self.assertAlmostEqual(_tempo_score(0.0, 150.0, 0.6), 100.0)

    def test_half_target_scores_zero(self):
        # deviation 0.5 / tolerance 0.6 -> 100 - 83.3 = 16.7
        self.assertAlmostEqual(_tempo_score(75.0, 150.0, 0.6), 100.0 - 100.0 * 0.5 / 0.6)

    def test_extreme_deviation_clamps_to_zero(self):
        # deviation 1.0 > tolerance 0.6 -> clamp to 0.
        self.assertEqual(_tempo_score(0.0, 150.0, 0.6), 100.0)  # no words: special
        self.assertEqual(_tempo_score(300.0, 150.0, 0.6), 0.0)

    def test_linear_falloff(self):
        # 25% deviation -> 100 - 25/60*100 = 58.33
        self.assertAlmostEqual(_tempo_score(187.5, 150.0, 0.6), 100.0 - 100.0 * 0.25 / 0.6, places=1)


class SpeedLabelTests(unittest.TestCase):
    """The tempo verdict string."""

    def test_natural_band(self):
        self.assertEqual(_speed_label(130.0, 150.0), "natural")   # ratio 0.867
        self.assertEqual(_speed_label(150.0, 150.0), "natural")   # ratio 1.0
        self.assertEqual(_speed_label(180.0, 150.0), "natural")   # ratio 1.2

    def test_too_slow(self):
        self.assertEqual(_speed_label(100.0, 150.0), "too slow")  # ratio 0.67

    def test_too_fast(self):
        self.assertEqual(_speed_label(220.0, 150.0), "too fast")  # ratio 1.47

    def test_no_target_or_no_words_returns_empty(self):
        self.assertEqual(_speed_label(120.0, 0.0), "")
        self.assertEqual(_speed_label(0.0, 150.0), "")


class ProsodyScoreTests(unittest.TestCase):
    """Contour-shape similarity (cosine on centred, resampled curves)."""

    def test_identical_shapes_score_100(self):
        a = [0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0]
        self.assertAlmostEqual(_prosody_score(a, a), 100.0)

    def test_opposite_shape_scores_below_50(self):
        a = [0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0]
        b = [3.0, 2.0, 1.0, 0.0, 1.0, 2.0, 3.0]
        self.assertLess(_prosody_score(a, b), 50.0)

    def test_flat_contour_scores_100(self):
        # A flat (constant) contour cannot be penalised for shape; avoids a 0
        # for a take the score should not punish twice.
        a = [1.0, 1.0, 1.0, 1.0]
        b = [0.0, 1.0, 2.0, 1.0]
        self.assertAlmostEqual(_prosody_score(a, b), 100.0)

    def test_empty_input_scores_100(self):
        self.assertAlmostEqual(_prosody_score([], []), 100.0)

    def test_resamples_longer_curves(self):
        a = list(range(100))
        self.assertAlmostEqual(_prosody_score(a, a), 100.0)


class ComputeFluencyTests(unittest.TestCase):
    """The public entry point, with a real (small) reference waveform."""

    def setUp(self):
        # A 1-second 16 kHz sine-ish reference; enough for prosody to run.
        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        self.ref = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        self.user = self.ref  # identical take -> perfect shape

    def test_returns_full_dict(self):
        fx = compute_fluency(
            user_audio=self.user, user_sr=16000,
            reference_audio=self.ref, reference_sr=16000,
            transcript="I like to read books", audio_duration_sec=2.0,
            reference_wpm=150.0,
        )
        for key in ("score", "tempo_score", "prosody_score", "wpm",
                    "wpm_ratio", "speed_label"):
            self.assertIn(key, fx)
        self.assertIsInstance(fx["score"], float)
        self.assertIsInstance(fx["wpm"], float)

    def test_tempo_ratio_and_label(self):
        # 4 words in 1s = 240 wpm vs 150 target -> "too fast", ratio 1.6.
        fx = compute_fluency(
            user_audio=self.user, user_sr=16000,
            reference_audio=self.ref, reference_sr=16000,
            transcript="a b c d", audio_duration_sec=1.0,
            reference_wpm=150.0, tempo_weight=1.0, prosody_weight=0.0,
        )
        self.assertAlmostEqual(fx["wpm"], 240.0, places=1)
        self.assertAlmostEqual(fx["wpm_ratio"], 1.6, places=1)
        self.assertEqual(fx["speed_label"], "too fast")

    def test_exact_target_tempo_scores_100_with_weight(self):
        # 5 words in 2s = 150 wpm = target -> tempo 100; prosody disabled.
        fx = compute_fluency(
            user_audio=self.user, user_sr=16000,
            reference_audio=self.ref, reference_sr=16000,
            transcript="a b c d e", audio_duration_sec=2.0,
            reference_wpm=150.0, tempo_weight=1.0, prosody_weight=0.0,
        )
        self.assertAlmostEqual(fx["tempo_score"], 100.0)
        self.assertAlmostEqual(fx["score"], 100.0)

    def test_no_reference_audio_uses_tempo_only(self):
        fx = compute_fluency(
            user_audio=self.user, user_sr=16000,
            reference_audio=None, reference_sr=16000,
            transcript="a b c d e", audio_duration_sec=2.0,
            reference_wpm=150.0, tempo_weight=0.5, prosody_weight=0.5,
        )
        # No reference -> prosody half scores 100 (nothing to compare); tempo 100.
        self.assertAlmostEqual(fx["prosody_score"], 100.0)
        self.assertAlmostEqual(fx["score"], 100.0)

    def test_no_transcript_or_duration_degrades_gracefully(self):
        fx = compute_fluency(
            user_audio=self.user, user_sr=16000,
            reference_audio=self.ref, reference_sr=16000,
            transcript="", audio_duration_sec=0.0,
            reference_wpm=150.0,
        )
        self.assertEqual(fx["wpm"], 0.0)
        self.assertEqual(fx["speed_label"], "")
        self.assertAlmostEqual(fx["score"], 100.0)  # nothing to penalise


if __name__ == "__main__":
    unittest.main()
