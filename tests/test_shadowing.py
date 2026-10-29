# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P1#4 shadowing alignment module (mimora/shadowing.py).

Covers the envelope extraction, the cross-correlation alignment, the
lag penalty and the score blend. Pure numpy - no models, no Tk.
"""

import unittest

import numpy as np

from mimora.shadowing import (LAG_TOLERANCE_SEC, compute_shadowing,
                              shadowing_score, _envelope)


def _speech_like(duration_sec: float = 2.0, sr: int = 16000,
                 rhythm_hz: float = 3.0) -> np.ndarray:
    """A speech-like waveform: a syllabic amplitude rhythm on a carrier."""
    t = np.arange(int(sr * duration_sec)) / sr
    env = np.clip(np.sin(2 * np.pi * rhythm_hz * t) ** 2, 0, None)
    return (env * np.sin(2 * np.pi * 200 * t)).astype(np.float32) * 0.5


class EnvelopeTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_envelope(np.array([]), 16000).size, 0)

    def test_silence(self):
        env = _envelope(np.zeros(16000, dtype=np.float32), 16000)
        self.assertEqual(env.size, 100)

    def test_normalized_peak(self):
        env = _envelope(_speech_like(), 16000)
        self.assertAlmostEqual(float(env.max()), 1.0, places=3)


class ComputeShadowingTest(unittest.TestCase):
    def test_self_aligned_is_perfect(self):
        ref = _speech_like()
        r = compute_shadowing(ref, 16000, ref, 16000)
        self.assertAlmostEqual(r["delay_sec"], 0.0, places=2)
        self.assertGreaterEqual(r["overlap"], 0.95)
        self.assertAlmostEqual(r["shadowing_axis"], 1.0, places=1)

    def test_late_user_has_positive_delay(self):
        sr = 16000
        ref = _speech_like()
        user = np.concatenate([np.zeros(int(sr * 0.5), dtype=np.float32),
                               ref[:int(sr * 1.5)]])
        r = compute_shadowing(user, sr, ref, sr)
        self.assertGreater(r["delay_sec"], 0.3)

    def test_early_user_has_negative_delay(self):
        sr = 16000
        ref = _speech_like()
        user = np.concatenate([ref[int(sr * 0.3):],
                               np.zeros(int(sr * 0.3), dtype=np.float32)])
        r = compute_shadowing(user, sr, ref, sr)
        self.assertLess(r["delay_sec"], -0.1)

    def test_empty_inputs_give_zero(self):
        r = compute_shadowing(None, 16000, np.zeros(100, dtype=np.float32), 16000)
        self.assertEqual(r, {"delay_sec": 0.0, "overlap": 0.0,
                             "lag_penalty": 0.0, "shadowing_axis": 0.0})

    def test_zero_arrays_give_zero(self):
        r = compute_shadowing(np.zeros(1600, dtype=np.float32), 16000,
                              np.zeros(1600, dtype=np.float32), 16000)
        self.assertEqual(r["shadowing_axis"], 0.0)

    def test_axis_is_bounded(self):
        sr = 16000
        ref = _speech_like()
        user = np.zeros(sr, dtype=np.float32)
        r = compute_shadowing(user, sr, ref, sr)
        self.assertGreaterEqual(r["shadowing_axis"], 0.0)
        self.assertLessEqual(r["shadowing_axis"], 1.0)


class ShadowingScoreTest(unittest.TestCase):
    def test_zero_weight_returns_pronunciation(self):
        self.assertEqual(shadowing_score(70.0, 0.8, 0), 70.0)

    def test_full_weight(self):
        self.assertAlmostEqual(shadowing_score(70.0, 0.5, 100), 50.0, places=1)

    def test_mid_blend(self):
        self.assertAlmostEqual(shadowing_score(70.0, 0.8, 30), 73.0, places=1)

    def test_clamped(self):
        self.assertLessEqual(shadowing_score(95.0, 1.0, 100), 100.0)
        self.assertGreaterEqual(shadowing_score(5.0, 0.0, 100), 0.0)

    def test_weight_clamped(self):
        self.assertAlmostEqual(shadowing_score(70.0, 0.5, 150), 50.0, places=1)


class LagPenaltyTest(unittest.TestCase):
    def test_within_tolerance_free(self):
        # A tiny lag well inside the tolerance must not be penalized.
        sr = 16000
        ref = _speech_like()
        user = np.concatenate([np.zeros(int(sr * 0.1), dtype=np.float32),
                               ref[:int(sr * 1.9)]])
        r = compute_shadowing(user, sr, ref, sr)
        self.assertGreaterEqual(r["lag_penalty"], 0.9)


if __name__ == "__main__":
    unittest.main()
