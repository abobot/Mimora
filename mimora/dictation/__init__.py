# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Dictation mode: hear a spoken sentence, type what you heard, get scored.

The package exposes the pure-data engine and the word-level scorer so the UI
layer and the unit tests share one definition of "how dictation is scored".
The Tkinter view lives in ``mimora/ui_dictation.py`` and the controller wiring
in ``mimora/app.py``.
"""

from mimora.dictation.engine import (
    DictationAttempt,
    DictationEngine,
    DictationOutcome,
)
from mimora.dictation.scoring import (
    alignment_summary,
    compare_sentences,
    score_dictation,
    tokenize_words,
)

__all__ = [
    "DictationAttempt",
    "DictationEngine",
    "DictationOutcome",
    "alignment_summary",
    "compare_sentences",
    "score_dictation",
    "tokenize_words",
]
