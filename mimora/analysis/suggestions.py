# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Rule-based suggestion generator for the weakness panel.

Reads the two aggregate tables (word leader board, phoneme weakness)
plus a small summary of recent WPM, and produces a list of
suggestion cards the UI can render as actionable next steps.

Each suggestion carries:
* id      - stable string (``"word:<word>"`` / ``"phoneme:<ipa>"`` /
            ``"wpm:fast"`` / ``"wpm:slow"``)
* kind    - ``word`` / ``phoneme`` / ``speed``
* title   - one-line summary shown at the top of the card
* body    - 1-2 lines of detail
* target  - a dict the Drill engine can consume directly
* tags    - free-form list for filtering

The rules are simple on purpose: v1 has no LLM call, everything runs
in <1 ms. Upgrading to an LLM-driven suggestion engine later only
needs to swap this module out.
"""

from __future__ import annotations

from typing import Any, Dict, List


# WPM bands: anything above 140 is "too fast for accuracy work",
# below 80 is "slow" (may need fluency practice). 100-130 is
# the "target" band for most learners.
WPM_FAST_CUTOFF = 140.0
WPM_SLOW_CUTOFF = 80.0
WPM_TARGET_LOW = 100.0
WPM_TARGET_HIGH = 130.0


def generate_suggestions(word_agg: Dict[str, Dict[str, Any]],
                         phoneme_agg: Dict[str, Dict[str, Any]],
                         recent_wpm: float = 0.0,
                         native_language: str = "zh"
                         ) -> List[Dict[str, Any]]:
    """Produce a suggestion list from the two aggregate tables.

    Order of suggestions:
    1. Phonemes the user gets wrong AND that are natively hard for
       their language (top by severity, capped at 3).
    2. Words marked ``need_practice`` (top 3).
    3. Speed advice if the recent WPM is outside the target band.

    Suggestions are deduplicated: one card per (kind, target) pair.
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    # 1. Top phonemes.
    from mimora.analysis.phonemes import ZH_INTERFERENCE, sorted_by_weakness
    phoneme_ranked = sorted_by_weakness(phoneme_agg)
    # Only include phonemes that actually appear in the ZH interference
    # table AND are weak. The UI's phoneme tab will show everything
    # recorded, but suggestions should point at the actionable subset.
    for ipa, entry in phoneme_ranked[:5]:
        if entry["severity_avg"] < 0.2:
            continue  # not bad enough to suggest
        if native_language == "zh" and entry["priority"] > 3:
            continue  # not a typical zh trouble spot
        key = f"phoneme:{ipa}"
        if key in seen:
            continue
        seen.add(key)
        hint = ""
        example = ""
        for row in ZH_INTERFERENCE:
            if row["ipa"] == ipa:
                hint = row["hint_zh"]
                example = row["example"]
                break
        out.append({
            "id": key,
            "kind": "phoneme",
            "title": f"练音素 /{ipa}/",
            "body": (f"平均得分 {entry['avg_score']:.0%}，出现 "
                     f"{entry['count']} 次。" +
                     (f" 例词：{example}。" if example else "") +
                     (f" {hint}" if hint else "")),
            "target": {"type": "phoneme", "value": ipa},
            "tags": ["phoneme", ipa],
            "metric": round(entry["severity_avg"], 3),
        })

    # 2. Top weak words.
    for word, entry in sorted(word_agg.items(),
                              key=lambda kv: (-kv[1]["error_rate"],
                                              -kv[1]["count"])):
        if entry["status"] not in ("need_practice", "watch"):
            continue
        key = f"word:{word}"
        if key in seen:
            continue
        seen.add(key)
        etype_hint = _error_type_hint(entry.get("error_types") or {})
        out.append({
            "id": key,
            "kind": "word",
            "title": f"练单词 \"{word}\"",
            "body": (f"出现 {entry['count']} 次，错误率 "
                     f"{entry['error_rate']:.0%}。{etype_hint}"),
            "target": {"type": "word", "value": word},
            "tags": ["word", word],
            "metric": round(entry["error_rate"], 3),
        })
        if len([s for s in out if s["kind"] == "word"]) >= 3:
            break

    # 3. Speed advice.
    if recent_wpm >= WPM_FAST_CUTOFF:
        out.append({
            "id": "wpm:fast",
            "kind": "speed",
            "title": "朗读语速偏快",
            "body": (f"平均 WPM {recent_wpm:.0f}，超过目标区间 "
                     f"{WPM_TARGET_LOW:.0f}-{WPM_TARGET_HIGH:.0f}。"
                     "建议先按慢速（WPM ~90）练 3 天，再逐步提速。"),
            "target": {"type": "free_form", "value": "慢速朗读训练"},
            "tags": ["speed", "fast"],
            "metric": round(recent_wpm, 2),
        })
    elif recent_wpm <= WPM_SLOW_CUTOFF and recent_wpm > 0:
        out.append({
            "id": "wpm:slow",
            "kind": "speed",
            "title": "朗读语速偏慢",
            "body": (f"平均 WPM {recent_wpm:.0f}，低于目标区间 "
                     f"{WPM_TARGET_LOW:.0f}-{WPM_TARGET_HIGH:.0f}。"
                     "建议做长句连贯训练，减少停顿。"),
            "target": {"type": "free_form", "value": "流畅度训练"},
            "tags": ["speed", "slow"],
            "metric": round(recent_wpm, 2),
        })

    return out


def _error_type_hint(error_types: Dict[str, int]) -> str:
    """One-line summary of the main error type for a word."""
    if not error_types:
        return ""
    total = sum(error_types.values())
    if total == 0:
        return ""
    worst_type, worst_count = max(error_types.items(), key=lambda kv: kv[1])
    zh_names = {
        "pronunciation": "发音不标准",
        "missing": "漏读",
        "extra": "多读",
        "substitution": "读成了别的词",
        "fluency": "断续 / 停顿",
    }
    label = zh_names.get(worst_type, worst_type)
    pct = worst_count * 100.0 / total
    return f"主要问题：{label}（{pct:.0f}%）。"
