# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Aggregate per-take ``error_words[]`` into a per-word leader board.

Input is a list of ``(session, take)`` tuples; output is one dict per
distinct word with a status of ``need_practice`` / ``watch`` /
``improving``. The status drives the colour in the weakness UI and is
what the suggestion engine keys off.

The status rules come from the plan doc:
    * need_practice  - error rate > 0.6 AND seen at least 3 times
    * watch          - error rate 0.4-0.6, OR seen exactly 2 times
    * improving      - last 5 occurrences all score >= 0.7
    * (default)      - otherwise (rare; usually means a one-off)
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Tuple


# The score cutoff used by the extractor: below this, an occurrence
# counts as an error. Matches phonemes.PHONEME_ERROR_CUTOFF.
WORD_ERROR_CUTOFF = 0.75

# A word only reaches "need_practice" if it appears at least this many
# times. Anything less is noise; a single mispronunciation is not a
# pattern.
MIN_OCCURRENCES_FOR_NEED = 3

# "Improving" requires the last N occurrences to all be above 0.7.
IMPROVING_WINDOW = 5
IMPROVING_CUTOFF = 0.70


def aggregate_error_words(takes: List[Tuple[Dict[str, Any], Dict[str, Any]]],
                          lookback: int = 30
                          ) -> Dict[str, Dict[str, Any]]:
    """Aggregate error_words across the last N takes into a per-word table.

    ``takes`` is ``[(session, take), ...]``, newest last. Returns
    ``{word: {count, error_count, error_rate, avg_score, last_score,
              error_types, in_phrases, status}}``.

    ``status`` is one of ``need_practice`` / ``watch`` / ``improving``
    / ``other``, decided by the rules in the module docstring.
    """
    if lookback is not None and len(takes) > lookback:
        takes = takes[-lookback:]
    per_word: Dict[str, Dict[str, Any]] = {}
    for _session, take in takes:
        phrase = (take.get("phrase") or "").strip()
        for ew in take.get("error_words") or []:
            word = (ew.get("word") or "").strip().lower()
            if not word:
                continue
            score = float(ew.get("score") or 0.0)
            etype = ew.get("error_type") or "pronunciation"
            entry = per_word.setdefault(word, {
                "count": 0,
                "error_count": 0,
                "scores": [],
                "error_types": {},
                "in_phrases": set(),
            })
            entry["count"] += 1
            entry["scores"].append(score)
            if score < WORD_ERROR_CUTOFF:
                entry["error_count"] += 1
            entry["error_types"][etype] = \
                int(entry["error_types"].get(etype, 0)) + 1
            if phrase:
                entry["in_phrases"].add(phrase)
    for word, entry in per_word.items():
        scores = entry.pop("scores") or [0.0]
        entry["avg_score"] = round(mean(scores), 4)
        entry["last_score"] = round(scores[-1], 4)
        entry["error_rate"] = round(
            entry["error_count"] / entry["count"] if entry["count"] else 0.0, 4)
        # Status determination (order matters: improving is checked first
        # because it can override a stale error rate that a recent
        # improvement has since fixed).
        recent5 = scores[-IMPROVING_WINDOW:]
        if recent5 and mean(recent5) >= IMPROVING_CUTOFF:
            entry["status"] = "improving"
        elif entry["error_rate"] > 0.6 and entry["count"] >= MIN_OCCURRENCES_FOR_NEED:
            entry["status"] = "need_practice"
        elif entry["error_rate"] >= 0.4 or entry["count"] == 2:
            entry["status"] = "watch"
        else:
            entry["status"] = "other"
        # Cap in_phrases for the UI.
        entry["in_phrases"] = sorted(entry["in_phrases"])[:5]
    return per_word


def sorted_by_error_rate(word_agg: Dict[str, Dict[str, Any]]
                         ) -> List[Tuple[str, Dict[str, Any]]]:
    """Sort a word aggregate by (status rank, error_rate desc, count desc).

    The weakness UI's word tab wants "need_practice" words first,
    "watch" next, "improving" and "other" last. Within a status,
    higher error_rate first, then more occurrences.
    """
    rank = {"need_practice": 0, "watch": 1, "improving": 2, "other": 3}
    return sorted(
        word_agg.items(),
        key=lambda kv: (rank.get(kv[1].get("status"), 9),
                        -kv[1].get("error_rate", 0.0),
                        -kv[1].get("count", 0)),
    )


def top_words(word_agg: Dict[str, Dict[str, Any]], n: int = 5
              ) -> List[str]:
    """Return the ``n`` words the user should work on first (by status,
    then error rate). Empty list when nothing is worth flagging.
    """
    items = sorted_by_error_rate(word_agg)
    # Skip words the engine already considers "improving" - the user
    # does not need to keep drilling those.
    return [word for word, entry in items if entry["status"] in ("need_practice", "watch")][:n]
