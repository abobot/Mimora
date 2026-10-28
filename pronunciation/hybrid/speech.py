# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Hybrid pronunciation engine (axis-level blend of phoneme + acoustic).

Runs BOTH production engines on the same take and blends their axis-level
results into one 0-100 score. The phoneme engine scores from the phrase TEXT
(espeak reference phonemes + phoneme ASR + feature-weighted edit distance);
the acoustic engine scores the AUDIO comparison (Wav2Vec2 embeddings +
cosine-DTW + phoneme/word error rates). The two engines are complementary:
phoneme catches segmental errors the ASR heard, acoustic catches prosodic
and voice-quality drift the ASR cannot see.

Fallback rules (P0):
  * ``reference_audio`` is None -> the acoustic engine cannot score (its
    score is a comparison against the reference recording), so the hybrid
    falls back to the PURE phoneme score.
  * The acoustic engine is English-only (Wav2Vec2 word ASR); a non-English
    phrase falls back to the pure phoneme score the same way.

Public API mirrors the other engines exactly (``configure`` / ``load_models`` /
``warm_up`` / ``analyze``) so the dispatcher (mimora/engine.py) treats it the same.
The returned PronunciationResult is the shared engine-neutral type; the GUI reads it identically.

This package never touches the GUI.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from pronunciation.common import PronunciationResult

from .config import get_config

TARGET_SAMPLE_RATE = 16_000
KOKORO_SAMPLE_RATE = 24_000


def load_models() -> None:
    """Load BOTH sub-engines models (phoneme ASR + acoustic ASR).

    Heavy (two ~1.2 GB repos); call from a background daemon thread at mode
    startup so the GUI stays responsive (mirrors the other engines).
    """
    from ..acoustic import load_models as _acoustic_load
    from ..phoneme import load_models as _phoneme_load
    _phoneme_load()
    _acoustic_load()


def warm_up() -> None:
    """Run a dummy pass through both engines to remove first-call latency."""
    from ..acoustic import warm_up as _acoustic_warm
    from ..phoneme import warm_up as _phoneme_warm
    _phoneme_warm()
    _acoustic_warm()


def analyze(user_audio: np.ndarray,
            expected_text: str,
            reference_audio: Optional[np.ndarray] = None,
            user_sr: int = TARGET_SAMPLE_RATE,
            reference_sr: int = KOKORO_SAMPLE_RATE,
            voice: Optional[str] = None,
            is_reference: bool = False) -> PronunciationResult:
    """Score the take with both engines and blend the axis-level results.

    Args:
        user_audio: user recorded waveform (1-D float32).
        expected_text: the reference phrase the user was asked to repeat.
        reference_audio: Kokoro-synthesised reference waveform. The acoustic
            engine cannot score without it (its score is a comparison against
            the reference), so None falls back to the pure phoneme score.
        user_sr: sample rate of user_audio (recording path is 16 kHz).
        reference_sr: sample rate of reference_audio (Kokoro is 24 kHz).
        voice: Kokoro voice the reference was synthesized with (for logs).
        is_reference: marks the reference self-test (excluded from calibration).

    Returns:
        PronunciationResult with the blended score. The 0-5 ``bucket`` comes
        from the phoneme engine's calibrated bucket system applied to the
        blended score (HYB-P1-2), so the GUI reads the same human-calibrated
        grade it already understands. ``prosody`` is left empty; the host
        fills it from the raw waveforms.
    """
    cfg = get_config()
    from ..phoneme import analyze as _phoneme_analyze

    # The phoneme engine always runs: it needs only the phrase text.
    phoneme_result = _phoneme_analyze(
        user_audio=user_audio,
        expected_text=expected_text,
        reference_audio=reference_audio,
        user_sr=user_sr,
        reference_sr=reference_sr,
        voice=voice,
        is_reference=is_reference,
    )

    # The acoustic engine runs only when it has a reference recording AND the
    # phrase is English (its Wav2Vec2 word ASR is English-only). Otherwise the
    # hybrid degrades to the pure phoneme score - honest fallback, not a crash.
    acoustic_result = None
    if reference_audio is not None and _is_english(expected_text):
        try:
            from ..acoustic import analyze as _acoustic_analyze
            acoustic_result = _acoustic_analyze(
                user_audio=user_audio,
                expected_text=expected_text,
                reference_audio=reference_audio,
                user_sr=user_sr,
                reference_sr=reference_sr,
                voice=voice,
                is_reference=is_reference,
            )
        except Exception:
            logging.exception("[hybrid] acoustic engine failed; using pure phoneme score")
            acoustic_result = None

    if acoustic_result is None:
        logging.info("[hybrid] no acoustic score (reference missing or non-English); using pure phoneme score")
        result = phoneme_result
        result.scored = True
        result.feedback = _fallback_feedback(phoneme_result)
        return result

    # Blend the two axis-level scores (P0 placeholder weights from HybridConfig).
    w_p = cfg.w_phoneme
    w_a = cfg.w_acoustic
    total = w_p + w_a
    if total <= 0:
        total = 1.0
        w_p = w_a = 0.5
    blended = (w_p * phoneme_result.score + w_a * acoustic_result.score) / total
    blended = round(min(100.0, max(0.0, blended)), 2)

    # Build the blended result. Score is the blend; the GUI fields come from the
    # phoneme engine (it has the richer per-phrase diagnostics), with the acoustic
    # distance diagnostics carried for the settings/log line.
    # The 0-5 bucket comes from the phoneme engine's calibrated bucket system
    # (HYB-P1-2): the blended score is coarsened onto the same human-calibrated
    # grade, so the hybrid reports a grade identical in KIND to the phoneme engine.
    from dataclasses import replace
    from ..phoneme.speech import bucketize_score as _bucketize
    bucket, user_percent, grade, grade_value, passed = _bucketize(blended)
    result = replace(
        phoneme_result,
        score=blended,
        passed=passed,
        feedback=_blend_feedback(blended, phoneme_result.score, acoustic_result.score),
        bucket=bucket,
        user_percent=user_percent,
        grade=grade,
        grade_value=grade_value,
        # Carry the acoustic diagnostics (safe extras; ignored by the GUI elsewhere).
        acoustic_distance=acoustic_result.acoustic_distance,
        acoustic_per_step=acoustic_result.acoustic_per_step,
        acoustic_baseline=acoustic_result.acoustic_baseline,
    )

    logging.info(
        "[hybrid] blended=%.1f (phoneme=%.1f acoustic=%.1f w=%.2f/%.2f) | is_ref=%s | voice=%s",
        blended, phoneme_result.score, acoustic_result.score, w_p / total, w_a / total,
        is_reference, voice,
    )

    return result


def _is_english(text: str) -> bool:
    """Heuristic: is *text* plain ASCII English (the acoustic ASR alphabet)?

    The acoustic Wav2Vec2 word ASR is English-only; feeding it non-ASCII text
    would produce a meaningless (but not crashing) word error rate. The heuristic
    is deliberately conservative: only a clearly ASCII phrase is treated as
    English, anything with non-ASCII letters falls back to pure phoneme.
    """
    return all(ord(c) < 128 for c in text.strip())


def _fallback_feedback(phoneme_result: PronunciationResult) -> str:
    """Feedback for the pure-phoneme fallback, marking that the acoustic axis is off."""
    base = phoneme_result.feedback or "Phoneme score only (acoustic axis unavailable)."
    return base + " [hybrid: acoustic axis unavailable - phoneme score only]"


def _blend_feedback(blended: float, phoneme: float, acoustic: float) -> str:
    """Human-readable blend summary for the feedback line."""
    return "Hybrid score %.0f (phoneme %.0f + acoustic %.0f). The acoustic axis may not catch segmental errors the ASR heard." % (blended, phoneme, acoustic)
