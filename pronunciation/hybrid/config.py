# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Configuration for the hybrid pronunciation engine.

``pronunciation/hybrid/`` combines the two production engines into one score:
the phoneme engine (espeak reference + phoneme ASR + feature-weighted edit
distance) and the acoustic engine (Wav2Vec2 embeddings + cosine-DTW) run on the
SAME take, and their axis-level results are blended into a single 0-100 score.

Like the other engines it is GUI- and application-agnostic: every tunable lives
in the small :class:`HybridConfig` below, and the library ships with working
defaults. A host injects its own values **once at startup** with
:func:`configure`, mirroring the other engines' pattern.

The two sub-engines keep their OWN :class:`AnalyzerConfig` (different fields and
model defaults), so :class:`HybridConfig` wraps one of each and forwards the
host's settings to both when :func:`configure` installs them. This is the same
reason the dispatcher (mimora/engine.py) builds each sub-config from app
settings rather than restating the fields here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..acoustic.config import AnalyzerConfig as AcousticConfig
from ..phoneme.config import AnalyzerConfig as PhonemeConfig


@dataclass(frozen=True)
class HybridConfig:
    """Settings consumed by the hybrid pronunciation analyzer.

    Wraps one :class:`AnalyzerConfig` per sub-engine. The two engines load
    different model repos and use different scoring internals, so they are
    configured separately; the host builds both from app settings and injects
    them here once at startup with :func:`configure`.

    The blend weights are the P0 placeholders (phoneme vs acoustic 0.5/0.5).
    ``w_phoneme_quality`` / ``w_recall`` / ``w_dtw`` / ``w_word`` are the
    per-axis refinements reserved for HYB-P1-2 (real-sample fitting); they
    default to 1.0, meaning "keep each sub-engine's own internal weighting".
    """

    # Sub-engine configurations. Shared defaults (not per-instance copies)
    # because both dataclasses are frozen: no code path mutates them, so one
    # default instance per HybridConfig is safe and keeps construction light.
    phoneme: PhonemeConfig = field(default_factory=PhonemeConfig)
    acoustic: AcousticConfig = field(default_factory=AcousticConfig)
    # Top-level blend weight of the phoneme engine's score vs the acoustic
    # engine's score. P0 placeholder (0.5/0.5); HYB-P1-2 fits these to real
    # samples. Values are normalized, so 0.4/0.6 and 4/6 are equivalent.
    w_phoneme: float = 0.5
    w_acoustic: float = 0.5
    # Per-axis refinement multipliers (HYB-P1-2). 1.0 = no adjustment.
    w_phoneme_quality: float = 1.0
    w_recall: float = 1.0
    w_dtw: float = 1.0
    w_word: float = 1.0


# Active configuration for this process. The default keeps the library
# autonomous; a host app replaces it once at startup via configure().
_active: HybridConfig = HybridConfig()


def configure(cfg: HybridConfig) -> None:
    """Install the analyzer configuration for this process.

    Also configures both sub-engines with their wrapped configs, so a later
    ``phoneme.analyze`` / ``acoustic.analyze`` (called by this package's own
    ``analyze``) reads the host's settings rather than standalone defaults.
    Call once at startup, before ``load_models()``/``analyze()``.
    """
    global _active
    _active = cfg
    # Deferred imports: both sub-engine config modules are already imported by
    # the dataclass annotation above, but their ``configure`` functions must not
    # run at import time (the package __init__ may not have finished building).
    from ..acoustic import configure as _configure_acoustic
    from ..phoneme import configure as _configure_phoneme
    _configure_phoneme(cfg.phoneme)
    _configure_acoustic(cfg.acoustic)


def get_config() -> HybridConfig:
    """Return the currently active analyzer configuration."""
    return _active
