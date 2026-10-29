# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Fluency axis - tempo (WPM) + prosody-shape (F0/energy) into one 0-100 score.

Pronunciation engines score *what* was said (which phonemes/words match); they
deliberately ignore *how* it flowed. This module fills that gap as an
engine-agnostic axis the host (``app.py``) computes from the same waveforms and
transcript, so the score can carry a fluency component without touching any
engine.

Why a separate module: it needs only numpy + the pure prosody helpers, and must
stay free of the heavy engine stack (torch/transformers) so computing the axis
never forces a recognizer to load. The F0/energy contours come from
``mimora/prosody.py`` (which is itself torch-free); the WPM from the ASR
transcript plus the recorded duration.

The design is deliberately simple and deterministic - it is a scoring heuristic,
not a model:

* tempo_score: how close the take's speaking rate is to the language's target
  rate (``reference_wpm``). A take at the target scores 100; the score falls
  linearly to 0 at ``tempo_tolerance`` relative deviation (default +-60%).
* prosody_score: how similar the *shape* of the user's F0 and energy contours is
  to the reference's, measured by cosine similarity on the semitone/energy
  curves resampled to a common length. High when the user's stress/intonation
  pattern follows the reference, low when it is flat or off-beat.
* score = tempo_weight * tempo_score + prosody_weight * prosody_score.

``compute_fluency`` returns a plain dict (JSON/log friendly) with the axis
score, the two components, the WPM and the tempo verdict. The host decides
whether/how to blend it into the engine score (``config.FLUENCY_WEIGHT``).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from mimora.prosody_utils import resample_series, to_semitones

# Verdict thresholds on the wpm/reference_wpm ratio. Inside [slow_max, fast_min]
# the rate reads as natural; below/above it is flagged so the user knows which
# way to move. The defaults keep a generous band (a learner is not a metronome).
RATIO_SLOW_MAX = 0.85
RATIO_FAST_MIN = 1.25


def _tempo_score(wpm: float, reference_wpm: float, tolerance: float) -> float:
    """0-100 score for how close ``wpm`` is to ``reference_wpm``.

    Linear falloff to 0 at ``tolerance`` (fractional) relative deviation, so
    ``tolerance=0.6`` means a take at 0.4x or 1.6x the target scores 0. A
    non-positive reference (no configured target) yields 100 - tempo carries no
    penalty when there is nothing to compare against.
    """
    if reference_wpm <= 0 or wpm <= 0:
        return 100.0
    deviation = abs(wpm / reference_wpm - 1.0)
    if tolerance <= 0:
        tolerance = 0.6
    return max(0.0, min(100.0, 100.0 - 100.0 * deviation / tolerance))


def _prosody_score(user: Sequence[float], reference: Sequence[float],
                   points: int = 64) -> float:
    """0-100 cosine-similarity score between two resampled contours.

    Both contours are thinned to ``points`` samples so a longer/short take still
    compares shape-for-shape, then the cosine similarity of the two vectors
    (centred on their means) is mapped to 0-100. Identical shapes score 100; a
    flat user contour against a lively reference scores low; either contour
    empty/constant scores 100 (nothing to penalise - avoids a 0 for a silent
    take that the score should not punish twice).
    """
    u = np.asarray(resample_series(list(user), points), dtype=float)
    r = np.asarray(resample_series(list(reference), points), dtype=float)
    if u.size == 0 or r.size == 0 or float(u.std()) <= 1e-9 or float(r.std()) <= 1e-9:
        return 100.0
    # Centre both, so a constant offset (register difference) is not a penalty:
    # we compare *shape*, not absolute level. The prosody module already
    # expresses F0 in semitones (centred) and energy in a 0-250 scale.
    uc = u - u.mean()
    rc = r - r.mean()
    denom = float(np.linalg.norm(uc) * np.linalg.norm(rc))
    if denom <= 1e-9:
        return 100.0
    cos = float(np.dot(uc, rc) / denom)
    # cosine in [-1, 1]; map to [0, 100] with 0 (orthogonal) at 50 so an
    # anti-correlated contour scores below the midline, not a flat 0 for a
    # merely different shape.
    return max(0.0, min(100.0, (cos + 1.0) * 50.0))


def _speed_label(wpm: float, reference_wpm: float) -> str:
    """Human verdict for the tempo: "too slow" / "natural" / "too fast" / ""."""
    if reference_wpm <= 0 or wpm <= 0:
        return ""
    ratio = wpm / reference_wpm
    if ratio < RATIO_SLOW_MAX:
        return "too slow"
    if ratio > RATIO_FAST_MIN:
        return "too fast"
    return "natural"


def compute_fluency(
    user_audio: np.ndarray,
    user_sr: int,
    reference_audio: Optional[np.ndarray],
    reference_sr: int,
    transcript: str,
    audio_duration_sec: float,
    *,
    reference_wpm: float = 150.0,
    tempo_weight: float = 0.5,
    prosody_weight: float = 0.5,
    tempo_tolerance: float = 0.6,
) -> Dict[str, object]:
    """Compute the fluency axis for one take.

    Parameters mirror what ``app.py`` has at analysis time:
      * ``user_audio`` / ``user_sr`` - the recorded (normalized) waveform.
      * ``reference_audio`` / ``reference_sr`` - the synthesized reference the
        user repeated; ``None`` (or empty) disables the prosody-shape half.
      * ``transcript`` - the ASR transcript, for the word count.
      * ``audio_duration_sec`` - the recorded duration (seconds).
      * ``reference_wpm`` - the language's target speaking rate; 0/negative
        disables the tempo penalty.
      * ``tempo_weight`` / ``prosody_weight`` - blend of the two halves
        (normalized to sum to 1 internally).
      * ``tempo_tolerance`` - fractional tempo deviation scoring 0.

    Returns ``{"score", "tempo_score", "prosody_score", "wpm", "wpm_ratio",
    "speed_label"}``. ``score`` is the 0-100 axis; the caller blends it into the
    engine score with its own weight. ``speed_label`` is the localized-neutral
    verdict string (i18n at display time), "" when no reference WPM is set.
    """
    # --- Tempo half: words per minute from the transcript + duration. ---
    words = len(transcript.split()) if transcript else 0
    if audio_duration_sec > 0 and words > 0:
        wpm = words / (audio_duration_sec / 60.0)
    else:
        wpm = 0.0
    tempo = _tempo_score(wpm, reference_wpm, tempo_tolerance)

    # --- Prosody-shape half: F0/energy contour similarity vs the reference. ---
    if reference_audio is not None and len(reference_audio) > 0 \
            and len(user_audio) > 0:
        try:
            # compute_prosody returns plain lists (JSON-friendly). Importing here
            # (not at module top) keeps this module importable without librosa
            # for callers that only need the pure helpers.
            from mimora import prosody
            contours = prosody.compute_prosody(
                user_audio=user_audio,
                user_sr=user_sr,
                reference_audio=reference_audio,
                reference_sr=reference_sr,
            )
            f0 = to_semitones(contours.get("f0", []))
            ref_f0 = to_semitones(contours.get("ref_f0", []))
            f0_sim = _prosody_score(f0, ref_f0)
            energy_sim = _prosody_score(
                contours.get("energy", []), contours.get("ref_energy", []))
            prosody = 0.5 * f0_sim + 0.5 * energy_sim
        except Exception:
            # A prosody failure must not discard the take: fall back to the tempo
            # half alone rather than crashing the whole axis.
            prosody = tempo
    else:
        prosody = 100.0  # no reference to compare shape against

    total_weight = tempo_weight + prosody_weight
    if total_weight <= 0:
        score = 0.0
    else:
        score = (tempo_weight * tempo + prosody_weight * prosody) / total_weight
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "tempo_score": round(tempo, 1),
        "prosody_score": round(prosody, 1),
        "wpm": round(wpm, 1),
        "wpm_ratio": round(wpm / reference_wpm, 2) if reference_wpm > 0 else 0.0,
        "speed_label": _speed_label(wpm, reference_wpm),
    }
