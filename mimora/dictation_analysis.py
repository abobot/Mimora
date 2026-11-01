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

import datetime as _dt
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Extended analysis for the full dictation dashboard.
#
# The stored sessions are consistent: ``attempts == len(takes)`` always, and
# ``first_score`` / ``last_score`` are never null. Two limits of the data are
# documented here because they shape every time-based chart:
#
# * ``started_at`` is actually the *save* time. ``to_storage_dict`` emits no
#   per-take ``timestamp``, so ``record_dictation_session`` falls back to
#   ``_now_iso()`` and ``started_at == ended_at`` for every session we have
#   seen. The only reliable time axis is the session's ``ended_at``; charts
#   that bucket by date use that field, never ``started_at``.
#
# * ``ended_at`` is never null for a stored session, so the design's wish to
#   split ``unfinished`` into "abandoned" vs "tried but not passed" is not
#   possible from the persisted data (the engine only records a session once
#   it ends, whether mastered or max-attempts reached). The outcome chart
#   therefore keeps the two documented categories.
#
# Per-word error arrays are plain ``str`` (never ``{word, ...}`` dicts), so
# word frequency is a straight ``Counter`` update. A word can appear in both
# ``wrong_words`` and ``spelling_errors`` for the same take (typed but wrong);
# the error-type charts count them in their own category and the
# per-word breakdown does not try to merge the two.
# ---------------------------------------------------------------------------


def mastery_rate(sessions: List[Dict[str, Any]]) -> float:
    """Fraction of sessions that ended mastered, 0.0-1.0 (0.0 if none)."""
    if not sessions:
        return 0.0
    outcome = outcome_distribution(sessions)
    return outcome["mastered"] / len(sessions)


def average_attempts(sessions: List[Dict[str, Any]]) -> float:
    """Mean number of takes per session (0.0 when there are no sessions)."""
    if not sessions:
        return 0.0
    return sum(len(s.get("takes") or []) for s in sessions) / len(sessions)


def average_gain(sessions: List[Dict[str, Any]]) -> float:
    """Mean ``last_score - first_score`` across sessions that have takes."""
    gains = [float(s.get("last_score") or 0.0) - float(s.get("first_score") or 0.0)
             for s in sessions if (s.get("takes") or [])]
    if not gains:
        return 0.0
    return sum(gains) / len(gains)


def session_dates(sessions: List[Dict[str, Any]],
                  ) -> List[Tuple[str, str]]:
    """One ``(session_id, date_str)`` per session with a parseable date.

    ``date_str`` is the ISO date part of ``ended_at`` (the reliable time
    axis; see the module note above). Sessions without a parseable date are
    dropped so the weekly charts are never polluted by an unknown bucket.
    """
    result: List[Tuple[str, str]] = []
    for s in sessions:
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if date:
            result.append((str(s.get("id") or ""), date))
    return result


def _iso_date(ts: str) -> Optional[str]:
    """The ``YYYY-MM-DD`` date of an ISO timestamp (None if unparseable)."""
    text = (ts or "").strip()
    if not text:
        return None
    try:
        # ISO with a +08:00 offset parses directly; tolerate a bare date.
        if "T" in text or "+" in text:
            parsed = _dt.datetime.fromisoformat(text)
        else:
            parsed = _dt.date.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, _dt.datetime):
        return parsed.date().isoformat()
    return parsed.isoformat()


def mastery_by_week(sessions: List[Dict[str, Any]],
                    ) -> List[Tuple[str, Dict[str, int]]]:
    """Mastered/unfinished counts per ISO week, oldest first.

    Returns ``[(week_label, {"mastered": n, "unfinished": m})]`` where
    ``week_label`` is the Monday date of the week (``YYYY-MM-DD``). Sessions
    without a parseable date are skipped.
    """
    by_week: Dict[str, List[str]] = {}
    for s in sessions:
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        monday = _week_start(date)
        by_week.setdefault(monday, []).append(str(s.get("outcome") or "unfinished"))
    result = []
    for week in sorted(by_week):
        dist = Counter(by_week[week])
        result.append((week, {
            "mastered": int(dist.get("mastered", 0)),
            "unfinished": int(dist.get("unfinished", 0)),
        }))
    return result


def _week_start(iso_date: str) -> str:
    """The Monday of the week containing ``iso_date`` (ISO date string)."""
    d = _dt.date.fromisoformat(iso_date)
    monday = d - _dt.timedelta(days=d.weekday())
    return monday.isoformat()


def attempts_histogram(sessions: List[Dict[str, Any]],
                       ) -> List[Tuple[int, int]]:
    """``(attempts, session_count)`` for each distinct attempts value, sorted."""
    counts = Counter(len(s.get("takes") or []) for s in sessions)
    return sorted(counts.items())


def pass_rate_by_attempt(sessions: List[Dict[str, Any]],
                         ) -> List[Tuple[int, float]]:
    """``(attempt_number, pass_rate)`` for attempt numbers seen in any take.

    ``pass_rate`` is the fraction of takes at that 1-indexed attempt number
    whose ``passed`` flag is true. Missing attempt numbers are omitted (the
    curve is drawn over the ones that exist).
    """
    totals: Dict[int, List[int]] = {}
    for s in sessions:
        for t in s.get("takes") or []:
            n = int(t.get("attempt") or 0)
            if n < 1:
                continue
            pair = totals.setdefault(n, [0, 0])
            pair[1] += 1
            if t.get("passed"):
                pair[0] += 1
    result = []
    for n in sorted(totals):
        p, total = totals[n]
        result.append((n, p / total if total else 0.0))
    return result


def error_type_trend(sessions: List[Dict[str, Any]],
                     ) -> List[Tuple[str, Dict[str, int]]]:
    """Per-week ``{missed, extra, spelling}`` take-level totals, oldest first.

    ``missed`` = reference words the user omitted; ``extra`` = surplus user
    words; ``spelling`` = reference words typed but wrong (the user wrote
    *something* for the slot, just not the right word). These are the three
    distinct learning gaps the dashboard distinguishes. Each value is a total
    across all takes in that week; weeks with no session are omitted.
    """
    by_week: Dict[str, Dict[str, int]] = {}
    for s in sessions:
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        week = _week_start(date)
        bucket = by_week.setdefault(week, {"missed": 0, "extra": 0, "spelling": 0})
        for t in s.get("takes") or []:
            ref = t.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(t.get("user_text") or "", ref)
            for st in details.get("status") or []:
                if st["state"] == "missed":
                    bucket["missed"] += 1
                elif st["state"] == "wrong":
                    bucket["spelling"] += 1
            bucket["extra"] += len(details.get("extra_words") or [])
    result = [(week, by_week[week]) for week in sorted(by_week)]
    return result


def error_type_totals(sessions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Grand totals of the three error kinds (missed / extra / spelling)."""
    totals = {"missed": 0, "extra": 0, "spelling": 0}
    for s in sessions:
        for t in s.get("takes") or []:
            ref = t.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(t.get("user_text") or "", ref)
            for st in details.get("status") or []:
                if st["state"] == "missed":
                    totals["missed"] += 1
                elif st["state"] == "wrong":
                    totals["spelling"] += 1
            totals["extra"] += len(details.get("extra_words") or [])
    return totals


def top_wrong_words(sessions: List[Dict[str, Any]],
                    limit: int = 20) -> List[Tuple[str, int]]:
    """Most-frequently wrong words (missed or misspelled), by occurrence.

    Normalised to lower case / stripped punctuation by ``compare_sentences``
    itself, so "The" and "the" and "the," all count as one token. Returns
    the top ``limit`` as ``(word, count)``.
    """
    freq: Counter = Counter()
    for s in sessions:
        for t in s.get("takes") or []:
            ref = t.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(t.get("user_text") or "", ref)
            for st in details.get("status") or []:
                if st["state"] in ("missed", "wrong"):
                    freq[st["word"]] += 1
    return freq.most_common(limit)


def word_by_week_heatmap(sessions: List[Dict[str, Any]],
                         top_n: int = 15,
                         ) -> Tuple[List[str], List[str], List[List[int]]]:
    """Heatmap data: ``(words, weeks, matrix)``.

    ``words`` is the top ``top_n`` wrong words by total occurrence, ``weeks``
    the ISO week labels (Monday dates, oldest first) that have at least one
    session, and ``matrix[i][j]`` the occurrence count of ``words[i]`` in
    ``weeks[j]``. Sparse (most cells zero) - the caller picks the palette.
    """
    # Word -> per-week counters, and the set of weeks.
    per_word: Dict[str, Counter] = {}
    weeks: set = set()
    for s in sessions:
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        week = _week_start(date)
        weeks.add(week)
        for t in s.get("takes") or []:
            ref = t.get("reference_text") or ""
            if not ref:
                continue
            details = compare_sentences(t.get("user_text") or "", ref)
            for st in details.get("status") or []:
                if st["state"] in ("missed", "wrong"):
                    counter = per_word.setdefault(st["word"], Counter())
                    counter[week] += 1
    ranked = sorted(per_word.items(),
                    key=lambda kv: sum(kv[1].values()), reverse=True)[:top_n]
    week_list = sorted(weeks)
    words = [w for w, _ in ranked]
    matrix = [[per_word[w].get(week, 0) for week in week_list] for w in words]
    return words, week_list, matrix


def score_distribution(sessions: List[Dict[str, Any]],
                       bins: int = 10,
                       ) -> List[Tuple[str, int, int]]:
    """Score histogram as ``(bin_label, passed_count, failed_count)``.

    The score axis 0-100 is split into ``bins`` equal bins; each bin counts
    the takes whose ``score`` falls in it, split by the take-level ``passed``
    flag. ``bin_label`` is the bin's lower edge as ``"0"`` .. ``"100"``.
    This is the take-level distribution - it answers "how close were the
    failures", not whether a session was ultimately mastered.
    """
    edge = 100.0 / bins
    counters: Dict[int, List[int]] = {}
    for s in sessions:
        for t in s.get("takes") or []:
            score = float(t.get("score") or 0.0)
            idx = min(bins - 1, int(score / edge)) if score < 100.0 else bins - 1
            pair = counters.setdefault(idx, [0, 0])
            if t.get("passed"):
                pair[0] += 1
            else:
                pair[1] += 1
    result = []
    for i in range(bins):
        p, f = counters.get(i, [0, 0])
        result.append((f"{int(i * edge)}", p, f))
    return result


def attempt_series(sessions: List[Dict[str, Any]],
                   ) -> List[Tuple[str, List[float], bool]]:
    """Per-session score series, oldest first.

    Returns ``[(session_id, [scores...], mastered)]`` for sessions that have
    at least one take - a session with zero takes has no series. ``mastered``
    is the session-level outcome flag so the overlay chart can colour by
    outcome (green for mastered, red for unfinished).
    """
    result: List[Tuple[str, List[float], bool]] = []
    for s in sessions:
        takes = s.get("takes") or []
        if not takes:
            continue
        scores = [float(t.get("score") or 0.0) for t in takes]
        result.append((str(s.get("id") or ""), scores,
                       (s.get("outcome") or "unfinished") == "mastered"))
    return result
