# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Analyse historical takes: error-word and phoneme weakness aggregation.

Everything in this package is pure data-in / data-out. It never
touches Tkinter, never opens a file - the caller hands in a list of
take dicts and gets back ranked aggregate tables. That keeps every
function trivially unit-testable and makes it easy to swap the
suggestion engine (rule-based today, LLM in v2) without touching
the UI.
"""

from mimora.analysis.error_words import (
    aggregate_error_words,
    sorted_by_error_rate,
    top_words,
)
from mimora.analysis.phonemes import (
    ZH_INTERFERENCE,
    aggregate_phoneme_scores,
    example_word_for,
    hint_for,
    interference_priority,
    sorted_by_weakness,
)
from mimora.analysis.ipa_map import (
    ipa_from_words,
    word_to_ipa,
)
from mimora.analysis.suggestions import generate_suggestions

__all__ = [
    "aggregate_error_words",
    "sorted_by_error_rate",
    "top_words",
    "aggregate_phoneme_scores",
    "sorted_by_weakness",
    "interference_priority",
    "hint_for",
    "example_word_for",
    "ZH_INTERFERENCE",
    "word_to_ipa",
    "ipa_from_words",
    "generate_suggestions",
]
