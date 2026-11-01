# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Drill-session aggregation for the stats dashboard (pure, Tk-free).

The stored drill sessions carry ``target`` (``{"type", "value"}``),
``first_score`` / ``last_score`` / ``avg_score``, ``outcome`` and a
``takes`` list with ``{phrase, score, attempt, passed, audio_* }``. This
module re-derives the summary the drill tab needs - mastery per target
type, first-to-last progress, cross-session improvement per target,
phoneme averages, attempt/through-rate distributions, investment
structure and audio coverage - without any Tk import, so it is trivially
unit-testable and never drags Tk into the report path.

Two limits of the persisted data are documented here because they shape
every chart:

* ``started_at`` is the *save* time (``record_drill_session`` writes
  ``_now_iso()`` for both fields), so ``started_at == ended_at`` for every
  session. The only reliable time axis is ``ended_at``; charts that bucket
  by date use that field, never ``started_at``.

* There is no stored ``duration_sec`` (the design's wish to measure time
  investment is not possible), so investment is measured in *attempts* and
  *session counts*, not wall-clock time.

``target.type`` is one of ``phoneme`` / ``word`` / ``single_word`` /
``free_form``. ``word`` drills a word inside context phrases taken from
the source; ``single_word`` drills the word alone (the value IS the whole
practice phrase). They are the *same semantic target* (one word) in two
training shapes, so the per-type charts keep them separate but the
cross-session per-target charts merge them by ``value`` - see
:func:`target_progress_series`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from mimora import time_utils

#: Target types in display order (the order the per-type charts use).
TYPE_ORDER = ("phoneme", "word", "single_word", "free_form")


def _type_of(session: Dict[str, Any]) -> str:
    """The session's target type, normalised to lower case."""
    return str((session.get("target") or {}).get("type") or "").lower()


def _value_of(session: Dict[str, Any]) -> str:
    """The session's target value (the word / IPA symbol / phrase)."""
    return str((session.get("target") or {}).get("value") or "")


def _iso_date(ts: str) -> Optional[str]:
    """The ``YYYY-MM-DD`` date of an ISO timestamp (None if unparseable).

    Delegates to :func:`mimora.time_utils.iso_date` - the single shared
    implementation (kept as a thin local name so existing callers and tests do
    not change).
    """
    return time_utils.iso_date(ts)


def _week_start(iso_date: str) -> str:
    """The Monday of the week containing ``iso_date`` (ISO date string)."""
    return time_utils.week_start(iso_date)


def mastery_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, int]]]:
    """``(target_type, {"mastered": n, "unfinished": m})`` per type.

    Only types that appear in the sessions are returned, in
    :data:`TYPE_ORDER`. The value is the master rate that the stacked bar
    labels; a type with no sessions at all is omitted.
    """
    counts: Dict[str, Dict[str, int]] = {}
    for s in sessions:
        t = _type_of(s)
        if not t:
            continue
        bucket = counts.setdefault(t, {"mastered": 0, "unfinished": 0})
        outcome = str(s.get("outcome") or "unfinished")
        if outcome in bucket:
            bucket[outcome] += 1
        else:
            bucket["unfinished"] += 1
    return [(t, counts[t]) for t in TYPE_ORDER if t in counts]


def mastery_rate_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """``(target_type, mastery_rate 0..1)`` per type, ordered by :data:`TYPE_ORDER`.

    Only types present in the sessions appear. The rate is
    ``mastered / (mastered + unfinished)``; a type with no sessions is
    omitted (never 0.0-from-empty).
    """
    result: List[Tuple[str, float]] = []
    for t, dist in mastery_by_type(sessions):
        total = dist["mastered"] + dist["unfinished"]
        result.append((t, dist["mastered"] / total if total else 0.0))
    return result


def progress_pairs(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, str, float, float, int]]:
    """One ``(session_id, type, first, last, attempts)`` per session with takes.

    Sessions with zero takes are omitted (they have no first/last score to
    plot). ``first``/``last`` are floats, ``attempts`` the take count.
    """
    result: List[Tuple[str, str, float, float, int]] = []
    for s in sessions:
        takes = s.get("takes") or []
        if not takes:
            continue
        result.append((
            str(s.get("id") or ""),
            _type_of(s),
            float(s.get("first_score") or 0.0),
            float(s.get("last_score") or 0.0),
            len(takes),
        ))
    return result


def progress_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, List[Tuple[str, float, float, int]]]]:
    """``(type, [(session_id, first, last, attempts)])`` grouped by type.

    The order inside each group is the session order (as stored). Used by
    the dumbbell chart to colour by type and to place same-type sessions
    together.
    """
    grouped: Dict[str, List[Tuple[str, float, float, int]]] = defaultdict(list)
    for sid, t, first, last, attempts in progress_pairs(sessions):
        grouped[t].append((sid, first, last, attempts))
    return [(t, grouped[t]) for t in TYPE_ORDER if t in grouped]


def target_progress_series(
        sessions: List[Dict[str, Any]],
        min_sessions: int = 2) -> List[Tuple[str, str, List[Tuple[str, float]]]]:
    """Cross-session progress per target value, merged across type.

    ``word`` and ``single_word`` are the same semantic target (one word) in
    two training shapes, so they are merged here by ``value`` - the design's
    "same target repeatedly drilled" chart wants one series per word, not
    two. Returns ``[(type, value, [(date, avg_score)...])]`` for targets
    trained in at least ``min_sessions`` sessions, ordered by first date.
    ``date`` is the ``ended_at`` date; ``avg_score`` the session average.
    ``free_form`` values are near-unique and would fragment the chart, so
    they are excluded.
    """
    by_target: Dict[Tuple[str, str], List[Tuple[str, float]]] = defaultdict(list)
    for s in sessions:
        t = _type_of(s)
        if t == "free_form":
            continue
        value = _value_of(s)
        if not value:
            continue
        key = ("phoneme", value) if t == "phoneme" else ("word", value)
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        by_target[key].append((date, float(s.get("avg_score") or 0.0)))
    result: List[Tuple[str, str, List[Tuple[str, float]]]] = []
    for (t, value), series in by_target.items():
        if len(series) < min_sessions:
            continue
        series.sort()
        result.append((t, value, series))
    result.sort(key=lambda item: item[2][0][0])
    return result


def phoneme_averages(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, float, int]]:
    """``(ipa, avg_last_score, sessions)`` per phoneme target, weakest first.

    Uses the session's ``last_score`` (the design prefers "current level"
    over a mean across many sessions) averaged over the phoneme's sessions.
    Only sessions whose target is a phoneme are counted. Returned sorted by
    average ascending so the weakest phoneme leads the bar chart.
    """
    by_ipa: Dict[str, List[float]] = defaultdict(list)
    for s in sessions:
        if _type_of(s) != "phoneme":
            continue
        ipa = _value_of(s)
        if not ipa:
            continue
        by_ipa[ipa].append(float(s.get("last_score") or 0.0))
    result = [(ipa, sum(v) / len(v), len(v)) for ipa, v in by_ipa.items()]
    result.sort(key=lambda item: (item[1], item[0]))
    return result


def attempts_histogram_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, List[Tuple[int, int]]]]:
    """``(type, [(attempts, session_count)])`` per type, attempts ascending.

    ``attempts`` is ``len(takes)`` (verified equal to the stored
    ``attempts`` for every session in the data). A zero-take session counts
    in the ``0`` bucket.
    """
    grouped: Dict[str, Counter] = defaultdict(Counter)
    for s in sessions:
        t = _type_of(s)
        if not t:
            continue
        grouped[t][len(s.get("takes") or [])] += 1
    return [(t, sorted(grouped[t].items())) for t in TYPE_ORDER if t in grouped]


def pass_rate_by_attempt_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, List[Tuple[int, float]]]]:
    """``(type, [(attempt_number, pass_rate)])`` per type.

    ``pass_rate`` is the fraction of takes at that 1-indexed attempt number
    whose ``passed`` flag is true, across sessions of that type. Missing
    attempt numbers are omitted so the curve only spans numbers that exist.
    """
    grouped: Dict[str, Dict[int, List[int]]] = defaultdict(lambda: defaultdict(
        lambda: [0, 0]))
    for s in sessions:
        t = _type_of(s)
        if not t:
            continue
        for take in s.get("takes") or []:
            n = int(take.get("attempt") or 0)
            if n < 1:
                continue
            pair = grouped[t][n]
            pair[1] += 1
            if take.get("passed"):
                pair[0] += 1
    result: List[Tuple[str, List[Tuple[int, float]]]] = []
    for t in TYPE_ORDER:
        if t not in grouped:
            continue
        points = []
        for n in sorted(grouped[t]):
            p, total = grouped[t][n]
            points.append((n, p / total if total else 0.0))
        result.append((t, points))
    return result


def investment_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    """``(type, sessions, attempts)`` per type, attempts descending.

    ``attempts`` is the sum of ``len(takes)`` across the type's sessions.
    This is the investment structure the treemap / stacked bar reads; there
    is no stored duration, so attempts are the only reliable investment
    measure.
    """
    counts: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for s in sessions:
        t = _type_of(s)
        if not t:
            continue
        counts[t][0] += 1
        counts[t][1] += len(s.get("takes") or [])
    return sorted(((t, c[0], c[1]) for t, c in counts.items()),
                  key=lambda item: (-item[2], item[0]))


def investment_scatter(
        sessions: List[Dict[str, Any]],
        limit: int = 40) -> List[Tuple[str, str, int, float]]:
    """``(type, value, total_attempts, last_score)`` per target value.

    Each row is one target value (word/single_word merged by value, phoneme
    by IPA) - the design's investment-vs-outcome scatter. ``total_attempts``
    is the sum of takes across the target's sessions; ``last_score`` the
    most recent session's last score. Returns the top ``limit`` targets by
    total attempts (the most-invested first), excluding free_form.
    """
    by_target: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
    last: Dict[Tuple[str, str], float] = {}
    for s in sessions:
        t = _type_of(s)
        if t == "free_form":
            continue
        value = _value_of(s)
        if not value:
            continue
        key = ("phoneme", value) if t == "phoneme" else ("word", value)
        by_target[key][0] += 1
        by_target[key][1] += len(s.get("takes") or [])
        last[key] = float(s.get("last_score") or 0.0)
    items = [(t, value, attempts, last[(t, value)])
             for (t, value), (_, attempts) in by_target.items()]
    items.sort(key=lambda item: (-item[2], item[0]))
    return items[:limit]


def session_trajectories(
        sessions: List[Dict[str, Any]],
        min_attempts: int = 2) -> List[Tuple[str, str, str, List[float]]]:
    """``(session_id, type, outcome, [scores])`` for sessions with takes.

    ``min_attempts`` filters out single-take sessions whose trajectory is a
    single point (the design notes they carry little information). Scores
    are in attempt order; ``outcome`` is mastered/unfinished so the overlay
    chart can colour by result.
    """
    result: List[Tuple[str, str, str, List[float]]] = []
    for s in sessions:
        takes = s.get("takes") or []
        if len(takes) < min_attempts:
            continue
        scores = [float(t.get("score") or 0.0) for t in takes]
        result.append((
            str(s.get("id") or ""),
            _type_of(s),
            str(s.get("outcome") or "unfinished"),
            scores,
        ))
    return result


def audio_coverage(
        sessions: List[Dict[str, Any]]) -> Tuple[int, int]:
    """``(takes_with_audio, total_takes)`` across all drill sessions.

    A take has audio when its ``audio_files`` is a non-empty dict (the
    ``with_audio`` history mode attaches ``normalized.wav`` etc.). In
    metadata-only / none modes the field is None, so the coverage ratio
    drops - that is the signal the design wants ("many takes have no
    replayable audio").
    """
    with_audio = 0
    total = 0
    for s in sessions:
        for take in s.get("takes") or []:
            total += 1
            files = take.get("audio_files")
            if files and any(files.values()):
                with_audio += 1
    return with_audio, total


def target_by_week_heatmap(
        sessions: List[Dict[str, Any]],
        top_n: int = 12) -> Tuple[List[str], List[str], List[List[float]]]:
    """``(targets, weeks, matrix)`` of per-week average score.

    ``targets`` is the top ``top_n`` most-trained target values (word /
    single_word merged by value, phoneme by IPA, free_form excluded), each
    a human label (``/θ/`` for phoneme, the word otherwise). ``weeks`` is
    the ISO week labels (Monday dates) that have at least one session,
    oldest first. ``matrix[i][j]`` is the average ``last_score`` of
    target ``i`` in week ``j``, or ``0.0`` when the target had no session
    that week (so the heatmap shows a hole for untrained weeks).
    """
    per_target: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list))
    weeks: set = set()
    totals: Counter = Counter()
    for s in sessions:
        t = _type_of(s)
        if t == "free_form":
            continue
        value = _value_of(s)
        if not value:
            continue
        key = ("phoneme", value) if t == "phoneme" else ("word", value)
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        week = _week_start(date)
        weeks.add(week)
        per_target[key][week].append(float(s.get("last_score") or 0.0))
        totals[key] += 1
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0][0]))[:top_n]
    week_list = sorted(weeks)
    targets: List[str] = []
    matrix: List[List[float]] = []
    for (t, value), _ in ranked:
        label = f"/{value}/" if t == "phoneme" else value
        targets.append(label)
        row = []
        for week in week_list:
            scores = per_target[(t, value)][week]
            row.append(sum(scores) / len(scores) if scores else 0.0)
        matrix.append(row)
    return targets, week_list, matrix


#: A target is "stubborn" (the design's 顽固目标) when it has been drilled
#: this many total attempts and its most recent score still sits below
#: needs-work. The thresholds are deliberately simple: the scatter chart
#: reads the same two axes (attempts vs last score), so the KPI is the count
#: of targets in its bottom-right quadrant.
STUBBORN_MIN_ATTEMPTS = 10
STUBBORN_MAX_SCORE = 70.0


def stubborn_targets(sessions: List[Dict[str, Any]]) -> int:
    """The number of targets that were drilled a lot but remain weak.

    A target is stubborn when its **total attempts** across all its sessions
    reach :data:`STUBBORN_MIN_ATTEMPTS` and its most recent ``last_score``
    is below :data:`STUBBORN_MAX_SCORE`. ``word`` and ``single_word`` are
    merged by value; ``free_form`` is excluded (near-unique phrases would
    never repeat enough to count). This is the headline KPI the design
    calls 顽固目标 — the count of targets that have had enough practice to
    improve but have not.

    ``total_attempts`` is the sum of ``len(takes)`` across the target's
    sessions (NOT the session count — a 10-attempt session and a 1-attempt
    session are very different investments, and the scatter chart reads the
    same axis).
    """
    by_target: Dict[Tuple[str, str], int] = defaultdict(int)
    last: Dict[Tuple[str, str], float] = {}
    for s in sessions:
        t = _type_of(s)
        if t == "free_form":
            continue
        value = _value_of(s)
        if not value:
            continue
        key = ("phoneme", value) if t == "phoneme" else ("word", value)
        by_target[key] += len(s.get("takes") or [])
        last[key] = float(s.get("last_score") or 0.0)
    return sum(1 for key, attempts in by_target.items()
               if attempts >= STUBBORN_MIN_ATTEMPTS
               and last.get(key, 0.0) < STUBBORN_MAX_SCORE)


def weekly_volume_by_type(
        sessions: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, int]]]:
    """``(week_label, {type: attempts})`` per week, oldest first.

    ``attempts`` is the sum of ``len(takes)`` across the type's sessions in
    that week (the reliable investment measure; there is no stored
    duration). The stacked bar the design asks for (按周训练量堆叠) reads
    this directly - one bar per week, one stack segment per target type.
    Only types that appear in some week are present in the dicts.
    """
    per_week: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in sessions:
        t = _type_of(s)
        if not t:
            continue
        ended = s.get("ended_at") or s.get("started_at") or ""
        date = _iso_date(ended)
        if not date:
            continue
        per_week[_week_start(date)][t] += len(s.get("takes") or [])
    return sorted((week, dict(bucket)) for week, bucket in per_week.items())
