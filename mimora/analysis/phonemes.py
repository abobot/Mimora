# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Phoneme weakness aggregation and the ZH interference table.

Two things live here because they are used together in the UI: the
ZH_INTERFERENCE table (which phonemes a Chinese speaker finds hard
and why) and the aggregation that ranks every recorded phoneme by
how bad the user's recent takes were on it.

The table is deliberately simple data - a list of dicts, one entry per
IPA symbol. Adding a new language is a new module alongside this one
(``phonemes_es.py``, ``phonemes_ja.py`` ...) with its own table and
``interference_priority()`` wrapper, wired in at the analysis package
level. That keeps the "one language profile per module" rule the rest
 of Mimora follows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# ZH_INTERFERENCE - Chinese speaker's typical IPA trouble spots.
# Ordered by severity (rank 1 = most common mistake). ``confuses_with``
# is the IPA symbol (or empty string) it tends to be heard as.
# ---------------------------------------------------------------------------
ZH_INTERFERENCE = [
    {"ipa": "θ", "hint_zh": "咬舌音，中文无对应", "confuses_with": "s",
     "example": "think", "severity_rank": 1},
    {"ipa": "ð", "hint_zh": "咬舌浊音，中文无对应", "confuses_with": "z",
     "example": "this", "severity_rank": 1},
    {"ipa": "v", "hint_zh": "唇齿摩擦，中文常读成 w", "confuses_with": "w",
     "example": "very", "severity_rank": 2},
    {"ipa": "r", "hint_zh": "卷舌，中文 r 不卷", "confuses_with": "l",
     "example": "red", "severity_rank": 2},
    {"ipa": "ɜ", "hint_zh": "中元音，中文无对应", "confuses_with": "e",
     "example": "bird", "severity_rank": 2},
    {"ipa": "æ", "hint_zh": "美 /æ/，中文常读成 e", "confuses_with": "e",
     "example": "cat", "severity_rank": 3},
    {"ipa": "ʃ", "hint_zh": "sh 音，中文 s/sh 易混", "confuses_with": "s",
     "example": "ship", "severity_rank": 3},
    {"ipa": "dʒ", "hint_zh": "j 音，中文易读成 tʃ", "confuses_with": "tʃ",
     "example": "jam", "severity_rank": 3},
    {"ipa": "ŋ", "hint_zh": "后鼻音，中文易丢 n", "confuses_with": "n",
     "example": "sing", "severity_rank": 3},
    {"ipa": "l", "hint_zh": "亮 l / 暗 l 区分不清", "confuses_with": "n",
     "example": "love", "severity_rank": 3},
    {"ipa": "h", "hint_zh": "轻辅音，中文 h 常过重", "confuses_with": "",
     "example": "house", "severity_rank": 4},
]


def interference_priority(ipa: str, native_language: str = "zh") -> int:
    """Return the interference priority (1 = most common mistake) or 99.

    Called from the phoneme aggregation and the weakness UI to sort
    phonemes by how much the user's native language makes them hard.
    Unknown languages return 99 - a neutral priority that keeps the
    phoneme in its input order.
    """
    if native_language != "zh":
        return 99
    for entry in ZH_INTERFERENCE:
        if entry["ipa"] == ipa:
            return int(entry["severity_rank"])
    return 99


def hint_for(ipa: str) -> str:
    """Return the Chinese hint for one IPA, or empty string if unknown."""
    for entry in ZH_INTERFERENCE:
        if entry["ipa"] == ipa:
            return entry["hint_zh"]
    return ""


def example_word_for(ipa: str) -> str:
    """Return the example English word for one IPA, or empty string."""
    for entry in ZH_INTERFERENCE:
        if entry["ipa"] == ipa:
            return entry["example"]
    return ""


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

# Below this score a phoneme occurrence counts as an "error" in the
# aggregate's ``error_count``. Matches error_words's cutoff.
PHONEME_ERROR_CUTOFF = 0.75


def aggregate_phoneme_scores(takes: List[Tuple[Dict[str, Any], Dict[str, Any]]],
                             lookback: int = 30,
                             native_language: str = "zh"
                             ) -> Dict[str, Dict[str, Any]]:
    """Aggregate phoneme-level scores into a per-IPA weakness table.

    ``takes`` is ``[(session, take), ...]``, newest last. ``lookback``
    limits to the last N takes overall - the same semantics as the
    error-word aggregator. ``native_language`` picks the interference
    table (only "zh" is meaningfully populated today).

    Returns ``{ipa: {count, avg_score, error_count, severity_avg,
                     words, priority}}``. ``words`` is a sorted list
    capped at 5, so the UI can render it without truncation logic.
    """
    if lookback is not None and len(takes) > lookback:
        takes = takes[-lookback:]
    out: Dict[str, Dict[str, Any]] = {}
    for _session, take in takes:
        for entry in take.get("phoneme_scores") or []:
            ipa = entry.get("ipa")
            if not ipa:
                continue
            score = float(entry.get("score") or 0.0)
            agg = out.setdefault(ipa, {
                "count": 0, "scores": [], "words": set(),
                "error_count": 0,
            })
            agg["count"] += 1
            agg["scores"].append(score)
            if score < PHONEME_ERROR_CUTOFF:
                agg["error_count"] += 1
            in_word = (entry.get("in_word") or "").strip().lower()
            if in_word:
                agg["words"].add(in_word)
    # Post-process: turn internal fields into the shape the UI wants.
    for ipa, agg in out.items():
        scores = agg.pop("scores") or [0.0]
        agg["avg_score"] = round(sum(scores) / len(scores), 4)
        agg["severity_avg"] = round(1.0 - agg["avg_score"], 4)
        agg["words"] = sorted(agg["words"])[:5]
        agg["priority"] = interference_priority(ipa, native_language)
    return out


def sorted_by_weakness(phoneme_agg: Dict[str, Dict[str, Any]]
                       ) -> List[Tuple[str, Dict[str, Any]]]:
    """Sort an aggregate by (severity_avg desc, count desc, ipa asc).

    The UI's heatmap wants the worst phonemes first; the "priority"
    field is informational (a hint that a phoneme is natively hard for
    the user) but does not dominate the sort - a phoneme the user
    actually gets right should not be pushed up just because it is
    natively hard.
    """
    return sorted(
        phoneme_agg.items(),
        key=lambda kv: (-kv[1]["severity_avg"], -kv[1]["count"], kv[0]),
    )
