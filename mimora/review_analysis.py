# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Aggregation helpers for the spaced-repetition review pool (review.json).

Every function here is **pure** (Tk-free, no engine imports) so it can be
unit-tested without a GUI and shared by the desktop stats dashboard and the
web frontend. The inputs are the phrase states from :class:`mimora.review.
ReviewStore` (``review.json`` ``phrases`` dict: ``{phrase: state}``).

The eight sections map to the review-pool analysis the user asked for:

1. index distribution (how many phrases sit at each Ebbinghaus step),
2. due schedule (future due-load per day + overdue count),
3. seen vs passed (do more reviews actually lead to a pass?),
4. pool composition (practice vs llm banks, by depth and pass rate),
5. time span (how long a phrase has been in the pool vs its depth),
6. history trajectory (per-day snapshots, from config/review_history/),
7. overdue list (what to review today, most overdue first),
8. KPI dashboard (total / due today / overdue / avg index / pass rate).

``interval_index`` is the current-state snapshot: it does not record how a
phrase got here. For the trajectory (section 6) the history directory must
be populated by :meth:`ReviewStore.snapshot_history`, which appends one JSON
file per day.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Re-export the canonical interval schedule so consumers never import it
# directly from mimora.review (which drags in nothing heavy, but this module
# must stay a standalone aggregation library).
from mimora.review import EBBINGHAUS_INTERVALS_DAYS, SOURCE_CHOICES


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def phrases_as_list(phrases: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the ``phrases`` mapping into a list of state dicts.

    ``review.json`` stores states under the phrase text; every consumer here
    wants them as a flat list, with the phrase text available on each state
    (the store already puts ``phrase`` inside the state, but a hand-edited
    file may omit it, so we fill it from the key).
    """
    out: List[Dict[str, Any]] = []
    for key, state in (phrases or {}).items():
        item = dict(state or {})
        item.setdefault("phrase", key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 1. Index distribution
# ---------------------------------------------------------------------------

def index_distribution(phrases: Dict[str, Any]) -> Dict[str, Any]:
    """How many phrases sit at each Ebbinghaus step.

    Returns ``{counts: {index: n}, unscored: n}``. ``unscored`` counts
    phrases whose ``last_reviewed`` is null (they were recorded when first
    seen but never scored): those carry ``interval_index == 0`` only by
    default, so listing them separately keeps index 0 honest.
    """
    counts: Dict[int, int] = defaultdict(int)
    unscored = 0
    for p in phrases_as_list(phrases):
        idx = int(p.get("interval_index", 0) or 0)
        if p.get("last_reviewed") is None:
            # A recorded-but-never-scored phrase carries interval_index 0 only
            # by default. Counting it in the "0" bar would inflate the
            # shallowest bucket, so it is reported separately instead.
            unscored += 1
            continue
        counts[idx] += 1
    # Normalise to 0..len(intervals)-1 (never report a key above the last
    # step; a corrupt file could hold one).
    max_idx = len(EBBINGHAUS_INTERVALS_DAYS) - 1
    counts = {min(i, max_idx): n for i, n in counts.items()}
    return {"counts": counts, "unscored": unscored}


def index_distribution_by_source(phrases: Dict[str, Any]
                                 ) -> Dict[str, Dict[int, int]]:
    """Index distribution split by source (``practice`` / ``llm``)."""
    by_src: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for p in phrases_as_list(phrases):
        src = p.get("source", "practice")
        idx = int(p.get("interval_index", 0) or 0)
        by_src[src][idx] += 1
    return {src: dict(inner) for src, inner in by_src.items()}


# ---------------------------------------------------------------------------
# 2. Due schedule
# ---------------------------------------------------------------------------

def due_schedule(phrases: Dict[str, Any],
                 now: Optional[datetime] = None,
                 horizon_days: int = 60) -> Dict[str, Any]:
    """Future due-load per day plus the overdue bucket.

    ``now`` defaults to the current time (the schedule is always measured
    from "today"). Returns::

        {
          "per_day": {"YYYY-MM-DD": n},      # due on each future day
          "cumulative": {"YYYY-MM-DD": n},   # running total from today
          "overdue": [ {state...}, ... ],    # sorted, most overdue first
          "due_today": n,
        }

    Phrase times are parsed **without dropping the offset** (they are local
    ISO strings; ``date.fromisoformat(value[:10])`` would be wrong across a
    midnight boundary, so we keep the full datetime and compare against
    ``now``, then bucket by the *local* calendar date).
    """
    now = now or datetime.now()
    per_day: Dict[str, int] = defaultdict(int)
    overdue: List[Dict[str, Any]] = []
    for p in phrases_as_list(phrases):
        due = _parse_iso(p.get("next_review_at"))
        if due is None:
            continue  # never scored: not a review yet
        if due <= now:
            overdue.append(p)
        else:
            day = due.date().isoformat()
            if day <= (now + timedelta(days=horizon_days)).date().isoformat():
                per_day[day] += 1
    # Overdue, most overdue first (earliest next_review_at = most overdue).
    overdue.sort(key=lambda p: _parse_iso(p.get("next_review_at")) or now)
    # Cumulative from today, over the horizon.
    cumulative: Dict[str, int] = defaultdict(int)
    running = len(overdue)
    today = now.date()
    for offset in range(horizon_days + 1):
        day = (today + timedelta(days=offset)).isoformat()
        running += per_day.get(day, 0)
        cumulative[day] = running
    return {
        "per_day": dict(per_day),
        "cumulative": dict(cumulative),
        "overdue": overdue,
        "due_today": len(overdue),
    }


def due_heatmap_cells(due_per_day: Dict[str, int], start: date,
                      weeks: int = 10) -> List[Tuple[int, int, int]]:
    """GitHub-style cells for the due-load calendar.

    Mirrors :func:`mimora.ui_stats.heatmap_layout` but for **future** due
    counts, starting ``weeks`` weeks back from ``start`` so a peak that is
    about to hit shows up in the middle of the grid. ``level`` is 0-4 on a
    quantile scale of the non-zero due counts.
    """
    if weeks <= 0:
        return []
    grid_start = start - timedelta(days=start.weekday())
    days = [grid_start + timedelta(days=i) for i in range(weeks * 7)]
    values = [due_per_day.get(d.isoformat(), 0) for d in days]
    nonzero = sorted(v for v in values if v > 0) or [1.0]

    def q(p: float) -> float:
        return nonzero[min(len(nonzero) - 1, int(p * len(nonzero)))]

    thresholds = [q(0.25), q(0.5), q(0.75), q(1.0)]

    def level(v: int) -> int:
        if v <= 0:
            return 0
        lvl = 1
        for i, t in enumerate(thresholds):
            if v >= t:
                lvl = i + 1
        return lvl

    cells = []
    for week in range(weeks):
        for row in range(7):
            v = values[week * 7 + row]
            cells.append((week, row, level(v)))
    return cells


# ---------------------------------------------------------------------------
# 3. Seen vs passed
# ---------------------------------------------------------------------------

def seen_by_passed(phrases: Dict[str, Any]) -> Dict[str, Any]:
    """Distribution of ``seen`` grouped by the last result ``passed``.

    Returns ``{"passed": [seen...], "failed": [seen...]}``. Only phrases with
    ``last_reviewed`` set are included (an unscored phrase has ``seen == 0``
    and belongs to neither group).
    """
    groups: Dict[str, List[int]] = {"passed": [], "failed": []}
    for p in phrases_as_list(phrases):
        if p.get("last_reviewed") is None:
            continue
        key = "passed" if p.get("passed") else "failed"
        groups[key].append(int(p.get("seen", 0) or 0))
    return {k: sorted(v) for k, v in groups.items()}


def stubborn_phrases(phrases: Dict[str, Any], top_n: int = 20
                     ) -> List[Dict[str, Any]]:
    """Top-N phrases that fail but are seen a lot (the "钉子户" list).

    Filters to ``passed == false`` (and actually reviewed), sorts by ``seen``
    descending, truncates to ``top_n``. Each entry carries the full state plus
    a precomputed ``overdue_days``.
    """
    now = datetime.now()
    candidates = [
        p for p in phrases_as_list(phrases)
        if p.get("last_reviewed") is not None and not p.get("passed")
    ]
    for p in candidates:
        due = _parse_iso(p.get("next_review_at"))
        p["overdue_days"] = max(0, (now.date() - due.date()).days) if due else 0
    candidates.sort(key=lambda p: int(p.get("seen", 0) or 0), reverse=True)
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# 4. Pool composition
# ---------------------------------------------------------------------------

def source_composition(phrases: Dict[str, Any]) -> Dict[str, Any]:
    """Pool composition per source.

    Returns ``{"by_index": {source: {index: n}}, "totals": {source: n},
    "pass_rate": {source: float|None}}``. ``pass_rate`` is the fraction of
    *reviewed* phrases per source that last passed; ``None`` when the source
    has no reviewed phrases (caller should show "—", not 0%).
    """
    by_index: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    totals: Dict[str, int] = defaultdict(int)
    reviewed: Dict[str, int] = defaultdict(int)
    passed: Dict[str, int] = defaultdict(int)
    for p in phrases_as_list(phrases):
        src = p.get("source", "practice")
        idx = int(p.get("interval_index", 0) or 0)
        by_index[src][idx] += 1
        totals[src] += 1
        if p.get("last_reviewed") is not None:
            reviewed[src] += 1
            if p.get("passed"):
                passed[src] += 1
    pass_rate: Dict[str, Optional[float]] = {}
    for src in SOURCE_CHOICES:
        pass_rate[src] = (passed[src] / reviewed[src]) if reviewed[src] else None
    return {
        "by_index": {src: dict(inner) for src, inner in by_index.items()},
        "totals": dict(totals),
        "pass_rate": pass_rate,
    }


# ---------------------------------------------------------------------------
# 5. Time span
# ---------------------------------------------------------------------------

def span_vs_index(phrases: Dict[str, Any]
                  ) -> List[Tuple[int, int, bool, int]]:
    """(span_days, interval_index, passed, seen) per reviewed phrase.

    ``span_days`` = days between ``first_seen`` and ``last_reviewed``.
    Phrases never reviewed are excluded. The tuple order makes it usable as
    the scatter input (x=span, y=index, colour=passed).
    """
    out: List[Tuple[int, int, bool, int]] = []
    for p in phrases_as_list(phrases):
        first = _parse_iso(p.get("first_seen"))
        last = _parse_iso(p.get("last_reviewed"))
        if first is None or last is None:
            continue
        span = max(0, (last.date() - first.date()).days)
        out.append((span, int(p.get("interval_index", 0) or 0),
                    bool(p.get("passed")), int(p.get("seen", 0) or 0)))
    out.sort(key=lambda t: t[0])
    return out


def interval_ratio_histogram(phrases: Dict[str, Any],
                             buckets: Sequence[float] = (0.25, 0.5, 0.75, 1.0,
                                                         1.5, 2.0, 3.0)
                             ) -> List[Tuple[str, int]]:
    """Distribution of the actual/expected review-interval ratio.

    For each phrase with both a last review and a next review, the expected
    gap is ``intervals[interval_index]`` days (the interval the schedule just
    applied). The actual gap is ``last_reviewed → next_review_at``. A ratio
    of 1.0 means the schedule was honoured exactly; a big spread means manual
    intervention or a bug. Returns ``(label, count)`` pairs where the label
    is the bucket range (e.g. ``"0.5-0.75"``).
    """
    ratios: List[float] = []
    for p in phrases_as_list(phrases):
        last = _parse_iso(p.get("last_reviewed"))
        nxt = _parse_iso(p.get("next_review_at"))
        if last is None or nxt is None:
            continue
        idx = int(p.get("interval_index", 0) or 0)
        expected = EBBINGHAUS_INTERVALS_DAYS[idx]
        if expected <= 0:
            continue
        actual = (nxt - last).total_seconds() / 86400.0
        ratios.append(actual / expected)
    counts = Counter()
    for r in ratios:
        bucket = None
        for i, hi in enumerate(buckets):
            if r < hi:
                bucket = i
                break
        if bucket is None:
            bucket = len(buckets) - 1
        low = 0.0 if bucket == 0 else buckets[bucket - 1]
        high = buckets[bucket]
        counts[f"{low:g}-{high:g}"] += 1
    return [(label, counts[label]) for label in sorted(
        counts, key=lambda l: (float(l.split("-")[0]), float(l.split("-")[1])))]


# ---------------------------------------------------------------------------
# 6. History trajectory (snapshots)
# ---------------------------------------------------------------------------

def load_history(history_dir: str) -> List[Dict[str, Any]]:
    """Load per-day review-pool snapshots (oldest first).

    Each file is ``config/review_history/<YYYY-MM-DD>.json`` written by
    :meth:`ReviewStore.snapshot_history` in the form
    ``{"date": ..., "avg_index": float, "pass_rate": float|None,
    "by_index": {index: n}, "due": n, "total": n}``. Missing or corrupt
    files are skipped. Returns a list sorted by date.
    """
    import json
    from pathlib import Path
    out: List[Dict[str, Any]] = []
    directory = Path(history_dir)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                item = json.load(fh)
            if isinstance(item, dict) and "date" in item:
                out.append(item)
        except (OSError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# 7. Overdue list
# ---------------------------------------------------------------------------

def overdue_list(phrases: Dict[str, Any],
                 now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Phrases past their next review, most overdue first.

    Phrases with ``next_review_at`` null are never reviewed and excluded
    (otherwise the list explodes with fresh, unscored phrases). Each entry
    carries ``overdue_days`` (whole days, floored at 0 for "due today").
    """
    now = now or datetime.now()
    result: List[Dict[str, Any]] = []
    for p in phrases_as_list(phrases):
        due = _parse_iso(p.get("next_review_at"))
        if due is None:
            continue
        if due <= now:
            item = dict(p)
            item["overdue_days"] = (now.date() - due.date()).days
            result.append(item)
    result.sort(key=lambda p: p["overdue_days"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# 8. KPI dashboard
# ---------------------------------------------------------------------------

def kpi_dashboard(phrases: Dict[str, Any],
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """The summary KPIs for the review dashboard.

    Returns total / due-today / overdue / avg index / pass rate / stubborn
    count (``seen > 5`` and not passed) / never-reviewed count / per-source
    totals. ``pass_rate`` is over *reviewed* phrases only.
    """
    now = now or datetime.now()
    items = phrases_as_list(phrases)
    total = len(items)
    avg_index = (sum(int(p.get("interval_index", 0) or 0) for p in items)
                 / total) if total else 0.0
    reviewed = [p for p in items if p.get("last_reviewed") is not None]
    pass_rate = (sum(1 for p in reviewed if p.get("passed")) / len(reviewed)
                 if reviewed else None)
    never = sum(1 for p in items if p.get("last_reviewed") is None)
    stubborn = sum(1 for p in items
                   if p.get("last_reviewed") is not None
                   and not p.get("passed")
                   and int(p.get("seen", 0) or 0) > 5)
    # Count overdue directly (items is already a flat list, so the
    # dict-shaped overdue_list helper does not apply here).
    due_today = 0
    for p in items:
        due = _parse_iso(p.get("next_review_at"))
        if due is not None and due <= now:
            due_today += 1
    by_source = defaultdict(int)
    for p in items:
        by_source[p.get("source", "practice")] += 1
    return {
        "total": total,
        "due_today": due_today,
        "overdue": due_today,
        "avg_index": avg_index,
        "pass_rate": pass_rate,
        "stubborn": stubborn,
        "never_reviewed": never,
        "by_source": dict(by_source),
    }
