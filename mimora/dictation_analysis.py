# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Dictation-session aggregation for the stats dashboard (pure, Tk-free).

The stored dictation sessions carry ``first_score`` / ``last_score`` /
``outcome`` plus a ``takes`` list with ``user_text`` and ``reference_text``.
The per-word diagnostics (wrong vs extra words, spelling errors) are *not*
present in every stored session - older sessions predate them - so they are
re-derived here from the take texts through
:func:`mimora.dictation.scoring.compare_sentences`, the same alignment the
live scoring uses. That keeps the stats exactly in agreement with what the
user saw, for old and new sessions alike.

This module is deliberately Tk-free (like ``review_analysis``) so it is
trivially unit-testable and never drags Tk into the report path.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple

from mimora.dictation.scoring import compare_sentences


def wrong_vs_extra_counts(sessions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Total wrong-word vs extra-word occurrences across all sessions.

    ``wrong`` counts reference words the user missed or misspelled (states
    ``missed`` / ``wrong``); ``extra`` counts user words with no reference
    counterpart. Both are per-take occurrence counts, so the two bars measure
    the balance of the two error kinds directly.
    """
    wrong = 0
    extra = 0
    for session in sessions:
        for take in session.get("takes") or []:
            ref = take.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(take.get("user_text") or "", ref)
            wrong += sum(1 for st in details.get("status") or []
                         if st["state"] in ("missed", "wrong"))
            extra += len(details.get("extra_words") or [])
    return {"wrong": wrong, "extra": extra}


def spelling_frequencies(sessions: List[Dict[str, Any]],
                         limit: int = 12) -> List[Tuple[str, int]]:
    """Most-frequently misspelled words (state ``wrong``), by occurrence.

    A ``wrong`` reference word is one the user typed but wrote incorrectly -
    a spelling error, not an omission. Returns the top ``limit`` as
    ``(word, count)`` pairs, most frequent first.
    """
    freq: Counter = Counter()
    for session in sessions:
        for take in session.get("takes") or []:
            ref = take.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(take.get("user_text") or "", ref)
            for st in details.get("status") or []:
                if st["state"] == "wrong":
                    freq[st["word"]] += 1
    return freq.most_common(limit)


def progress_pairs(sessions: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """One ``(first_score, last_score)`` pair per session, in stored order.

    The dashboard draws this as a dumbbell / slope chart, the same shape as
    the drill progress: each session's line runs from its first take score to
    its last, so a rising line is progress within that session.
    """
    pairs: List[Tuple[float, float]] = []
    for session in sessions:
        pairs.append((float(session.get("first_score") or 0.0),
                      float(session.get("last_score") or 0.0)))
    return pairs


def outcome_distribution(sessions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Mastery counts by outcome string (``mastered`` / ``unfinished``)."""
    dist: Counter = Counter((s.get("outcome") or "unfinished")
                            for s in sessions)
    return {
        "mastered": int(dist.get("mastered", 0)),
        "unfinished": int(dist.get("unfinished", 0)),
    }
