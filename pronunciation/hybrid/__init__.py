# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Mimora hybrid pronunciation engine.

Combines the phoneme and acoustic engines into one axis-level blended score.
Public API mirrors pronunciation.phoneme / pronunciation.acoustic exactly
(``configure`` / ``load_models`` / ``warm_up`` / ``analyze``) so the dispatcher
(mimora/engine.py) treats it the same. The returned PronunciationResult is the
shared engine-neutral type.

This package never touches the GUI.
"""

from .config import HybridConfig, configure, get_config
from .speech import (
    analyze,
    load_models,
    warm_up,
    PronunciationResult,
)

__all__ = [
    "analyze",
    "load_models",
    "warm_up",
    "PronunciationResult",
    "HybridConfig",
    "configure",
    "get_config",
]
