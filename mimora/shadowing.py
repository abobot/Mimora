# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Shadowing (跟读) metrics: delay/overlap alignment between a take and the
reference.

In shadowing mode the reference phrase plays while the user speaks along with
it, so the take is not just "how well did you pronounce this" but "how well
did you *keep up* with the reference". That alignment is what this module
measures:

* ``delay_sec`` - the time offset between the user's speech and the reference
  (positive = the user is behind). Found by cross-correlating the two
  amplitude envelopes.
* ``overlap`` - the normalized peak correlation (0..1): how much of the
  reference the user actually tracked.
* ``lag_penalty`` - a linear falloff on the absolute delay: within
  ``LAG_TOLERANCE_SEC`` it is 1.0, then it decays to 0 at
  ``LAG_TOLERANCE_SEC * LAG_HARD_MAX``.
* ``shadowing_axis`` - the 0..1 axis score, the product of overlap and
  lag_penalty.

Pure numpy, no Tk, no engine imports, so it is unit-testable without models.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

# A shadowing lag up to this many seconds is free (the user is keeping up).
LAG_TOLERANCE_SEC = 0.35
# The lag at which the penalty reaches 0 (tolerance * this multiplier).
LAG_HARD_MAX = 3.0
# The envelope sample rate; low enough to keep the correlation cheap.
ENVELOPE_SR = 100.0


def _envelope(audio: np.ndarray, sr: int) -> np.ndarray:
    """A normalized RMS envelope of ``audio`` at ~ENVELOPE_SR.

    Frames ``sr / ENVELOPE_SR`` samples wide; the result is peak-normalized
    so a quiet take and a loud take correlate the same way.
    """
    if audio is None or audio.size == 0:
        return np.array([], dtype=np.float64)
    frame = max(1, int(round(sr / ENVELOPE_SR)))
    n_frames = audio.size // frame
    if n_frames == 0:
        return np.array([float(np.abs(audio).max() or 0.0)], dtype=np.float64)
    trimmed = audio[:n_frames * frame].reshape(n_frames, frame).astype(np.float64)
    env = np.sqrt((trimmed ** 2).mean(axis=1))
    peak = env.max()
    return env / peak if peak > 0 else env


def _normalized_correlation(ref: np.ndarray, user: np.ndarray) -> np.ndarray:
    """Cross-correlation of two zero-mean envelopes, normalized so the peak
    of a perfect (same-signal) match is 1.0."""
    if ref.size == 0 or user.size == 0:
        return np.array([], dtype=np.float64)
    # Zero-mean, so the correlation is meaningful for envelopes too.
    ref_c = ref - ref.mean()
    user_c = user - user.mean()
    denom = np.sqrt((ref_c ** 2).sum() * (user_c ** 2).sum())
    if denom == 0:
        return np.array([], dtype=np.float64)
    n = ref.size + user.size - 1
    corr = np.correlate(ref_c, user_c, mode="full")
    return corr / denom


def compute_shadowing(user_audio: Optional[np.ndarray], user_sr: int,
                      reference_audio: Optional[np.ndarray],
                      reference_sr: int) -> Dict[str, Any]:
    """Measure how well ``user_audio`` shadows ``reference_audio``.

    Returns a dict with ``delay_sec``, ``overlap``, ``lag_penalty`` and
    ``shadowing_axis``. All zeros when either input is empty or the two do
    not correlate (silence, mismatch), so the caller can blend it in without
    a special case.
    """
    empty = {"delay_sec": 0.0, "overlap": 0.0, "lag_penalty": 0.0,
             "shadowing_axis": 0.0}
    if (user_audio is None or user_audio.size == 0
            or reference_audio is None or reference_audio.size == 0):
        return empty
    ref_env = _envelope(reference_audio, reference_sr)
    user_env = _envelope(user_audio, user_sr)
    if ref_env.size < 2 or user_env.size < 2:
        return empty
    corr = _normalized_correlation(ref_env, user_env)
    if corr.size == 0:
        return empty
    lag = int(np.argmax(corr))
    # ``lag`` counts the offset of ``user`` relative to ``ref`` in envelope
    # frames; a positive lag means the user trails the reference.
    peak = float(corr[lag])
    overlap = max(0.0, min(1.0, peak))
    # Convert the lag to seconds, keeping the sign (behind vs ahead).
    # np.correlate mode="full" puts the reference on the left; the center for
    # equal-length inputs is at (ref.size - 1). A lag left of center means the
    # user trails (positive delay = behind), right of center means leading.
    offset_frames = (ref_env.size - 1) - lag
    delay_sec = offset_frames / ENVELOPE_SR
    abs_delay = abs(delay_sec)
    if abs_delay <= LAG_TOLERANCE_SEC:
        lag_penalty = 1.0
    else:
        lag_penalty = max(
            0.0,
            1.0 - (abs_delay - LAG_TOLERANCE_SEC)
            / (LAG_TOLERANCE_SEC * (LAG_HARD_MAX - 1.0)))
    shadowing_axis = round(overlap * lag_penalty, 4)
    return {
        "delay_sec": round(delay_sec, 3),
        "overlap": round(overlap, 4),
        "lag_penalty": round(lag_penalty, 4),
        "shadowing_axis": shadowing_axis,
    }


def shadowing_score(pronunciation_score: float,
                    shadowing_axis: float,
                    weight: float) -> float:
    """Blend a pronunciation score (0-100) with a shadowing axis (0..1).

    ``weight`` is the percentage (0-100) the axis contributes; the result is
    clamped to 0..100.
    """
    w = max(0.0, min(100.0, weight)) / 100.0
    blended = (1.0 - w) * float(pronunciation_score) + w * shadowing_axis * 100.0
    return round(max(0.0, min(100.0, blended)), 1)
