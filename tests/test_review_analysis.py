# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tk-free tests for the review-pool aggregation helpers.

Covers the eight analysis sections (index distribution, due schedule, seen vs
passed, pool composition, span, interval ratio, history load, overdue list,
KPI dashboard) plus the snapshot-history writer on ReviewStore.
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from mimora import review_analysis as ra
from mimora.review import ReviewStore


def _state(phrase="a", source="practice", first_seen=None, last_reviewed=None,
           next_review_at=None, interval_index=0, seen=0, passed=False):
    return {
        "phrase": phrase,
        "source": source,
        "first_seen": first_seen,
        "last_reviewed": last_reviewed,
        "next_review_at": next_review_at,
        "interval_index": interval_index,
        "seen": seen,
        "passed": passed,
    }


def _phrases(*states):
    return {s["phrase"]: s for s in states}


class IndexDistributionTest(unittest.TestCase):
    def test_counts_by_index(self):
        now = datetime(2026, 11, 1, 12, 0, 0)
        phrases = _phrases(
            _state("a", interval_index=0, last_reviewed=now.isoformat()),
            _state("b", interval_index=0, last_reviewed=now.isoformat()),
            _state("c", interval_index=1, last_reviewed=now.isoformat()),
            _state("d", interval_index=3, last_reviewed=now.isoformat()),
            _state("e", interval_index=6, last_reviewed=now.isoformat()),
        )
        dist = ra.index_distribution(phrases)
        self.assertEqual(dist["counts"], {0: 2, 1: 1, 3: 1, 6: 1})
        self.assertEqual(dist["unscored"], 0)

    def test_unscored_counted_separately(self):
        # A phrase recorded but never scored keeps interval_index 0 by default
        # but must not inflate the "0" bucket's health read.
        phrases = _phrases(
            _state("a", interval_index=0, last_reviewed="2026-10-30T10:00:00"),
            _state("b"),  # never reviewed
        )
        dist = ra.index_distribution(phrases)
        self.assertEqual(dist["counts"], {0: 1})
        self.assertEqual(dist["unscored"], 1)

    def test_by_source(self):
        phrases = _phrases(
            _state("a", source="practice", interval_index=0),
            _state("b", source="practice", interval_index=2),
            _state("c", source="llm", interval_index=1),
        )
        by = ra.index_distribution_by_source(phrases)
        self.assertEqual(by["practice"], {0: 1, 2: 1})
        self.assertEqual(by["llm"], {1: 1})

    def test_index_capped_at_max(self):
        # A corrupt file holding index 99 must clamp to the last step.
        now = datetime(2026, 11, 1, 12, 0, 0).isoformat()
        phrases = _phrases(_state("a", interval_index=99,
                                  last_reviewed=now))
        dist = ra.index_distribution(phrases)
        self.assertEqual(dist["counts"], {6: 1})


class DueScheduleTest(unittest.TestCase):
    def test_per_day_and_cumulative(self):
        now = datetime(2026, 11, 1, 12, 0, 0)
        phrases = _phrases(
            _state("a", next_review_at="2026-11-01T09:00:00"),   # overdue
            _state("b", next_review_at="2026-11-02T10:00:00"),   # tomorrow
            _state("c", next_review_at="2026-11-03T10:00:00"),   # +2
            _state("d"),                                          # never scored
        )
        sched = ra.due_schedule(phrases, now=now, horizon_days=5)
        self.assertEqual(sched["per_day"], {"2026-11-02": 1, "2026-11-03": 1})
        self.assertEqual(sched["overdue"], [phrases["a"]])
        self.assertEqual(sched["due_today"], 1)
        # Cumulative from today: today carries the overdue load, then each due
        # day adds its own count.
        self.assertEqual(sched["cumulative"]["2026-11-01"], 1)
        self.assertEqual(sched["cumulative"]["2026-11-02"], 2)
        self.assertEqual(sched["cumulative"]["2026-11-03"], 3)
        self.assertEqual(sched["cumulative"]["2026-11-06"], 3)

    def test_overdue_sorted_most_first(self):
        now = datetime(2026, 11, 1, 12, 0, 0)
        phrases = _phrases(
            _state("old", next_review_at="2026-10-20T09:00:00"),
            _state("new", next_review_at="2026-10-31T09:00:00"),
        )
        overdue = ra.due_schedule(phrases, now=now)["overdue"]
        self.assertEqual([p["phrase"] for p in overdue], ["old", "new"])

    def test_heatmap_cells_quantised(self):
        due = {"2026-11-01": 1, "2026-11-02": 2, "2026-11-03": 3,
               "2026-11-04": 4, "2026-11-05": 5}
        cells = ra.due_heatmap_cells(due, start=date(2026, 11, 1), weeks=2)
        self.assertEqual(len(cells), 14)
        levels = [lvl for _, _, lvl in cells]
        self.assertTrue(all(l >= 0 for l in levels))
        # The max day (5) hits the top level.
        max_lvl = max(levels)
        self.assertGreaterEqual(max_lvl, 4)


class SeenByPassedTest(unittest.TestCase):
    def test_grouping(self):
        now = datetime(2026, 11, 1, 12, 0, 0).isoformat()
        phrases = _phrases(
            _state("a", seen=3, passed=True, last_reviewed=now),
            _state("b", seen=8, passed=False, last_reviewed=now),
            _state("c", seen=1, passed=True, last_reviewed=now),
            _state("d", seen=0),  # never reviewed: excluded
        )
        groups = ra.seen_by_passed(phrases)
        self.assertEqual(groups["passed"], [1, 3])
        self.assertEqual(groups["failed"], [8])

    def test_stubborn_phrases(self):
        now = datetime(2026, 11, 1, 12, 0, 0).isoformat()
        phrases = _phrases(
            _state("low", seen=2, passed=False, last_reviewed=now),
            _state("high", seen=20, passed=False, last_reviewed=now),
            _state("pass", seen=10, passed=True, last_reviewed=now),
        )
        stubborn = ra.stubborn_phrases(phrases, top_n=2)
        self.assertEqual([p["phrase"] for p in stubborn], ["high", "low"])


class SourceCompositionTest(unittest.TestCase):
    def test_composition(self):
        now = datetime(2026, 11, 1, 12, 0, 0).isoformat()
        phrases = _phrases(
            _state("a", source="practice", interval_index=0, passed=False,
                   last_reviewed=now),
            _state("b", source="practice", interval_index=1, passed=True,
                   last_reviewed=now),
            _state("c", source="llm", interval_index=0, passed=True,
                   last_reviewed=now),
            _state("d", source="llm"),  # never reviewed
        )
        comp = ra.source_composition(phrases)
        self.assertEqual(comp["totals"], {"practice": 2, "llm": 2})
        self.assertEqual(comp["by_index"]["practice"], {0: 1, 1: 1})
        self.assertEqual(comp["by_index"]["llm"], {0: 2})  # c and d both idx 0
        self.assertEqual(comp["pass_rate"]["practice"], 0.5)
        self.assertEqual(comp["pass_rate"]["llm"], 1.0)

    def test_pass_rate_none_without_reviews(self):
        phrases = _phrases(_state("a", source="llm"))  # never reviewed
        comp = ra.source_composition(phrases)
        self.assertIsNone(comp["pass_rate"]["llm"])
        # A source with no phrases at all has no reviewed phrases either.
        self.assertIsNone(comp["pass_rate"]["practice"])


class SpanTest(unittest.TestCase):
    def test_span_vs_index(self):
        phrases = _phrases(
            _state("a", first_seen="2026-10-01T10:00:00",
                   last_reviewed="2026-10-20T10:00:00", interval_index=2,
                   passed=True, seen=5),
            _state("b", first_seen="2026-10-01T10:00:00",
                   last_reviewed="2026-10-02T10:00:00", interval_index=0,
                   passed=False, seen=1),
            _state("c"),  # never reviewed: excluded
        )
        rows = ra.span_vs_index(phrases)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], 1)  # b span
        self.assertEqual(rows[1][0], 19)  # a span
        self.assertEqual(rows[1][1], 2)
        self.assertTrue(rows[1][2])

    def test_interval_ratio_histogram(self):
        # expected 1-day interval; actual 2 days -> ratio 2.0.
        phrases = _phrases(
            _state("a", last_reviewed="2026-10-31T10:00:00",
                   next_review_at="2026-11-02T10:00:00", interval_index=0),
        )
        hist = ra.interval_ratio_histogram(phrases)
        # Buckets: (0.25,0.5,0.75,1.0,1.5,2.0,3.0). ratio 2.0 falls in 2-3
        # (the "< hi" check puts an exact 2.0 into the bucket whose high is
        # the next boundary above it, i.e. 3.0).
        self.assertEqual(hist, [("2-3", 1)])


class OverdueListTest(unittest.TestCase):
    def test_overdue_sorted_and_null_excluded(self):
        now = datetime(2026, 11, 1, 12, 0, 0)
        phrases = _phrases(
            _state("old", next_review_at="2026-10-20T09:00:00"),
            _state("never"),  # null next_review_at: excluded
            _state("recent", next_review_at="2026-10-31T09:00:00"),
        )
        overdue = ra.overdue_list(phrases, now=now)
        self.assertEqual([p["phrase"] for p in overdue], ["old", "recent"])
        self.assertEqual(overdue[0]["overdue_days"], 12)


class KpiDashboardTest(unittest.TestCase):
    def test_kpis(self):
        now = datetime(2026, 11, 1, 12, 0, 0)
        phrases = _phrases(
            _state("a", interval_index=2, passed=True, seen=3,
                   last_reviewed="2026-10-31T10:00:00"),
            _state("b", interval_index=0, passed=False, seen=8,
                   last_reviewed="2026-10-31T10:00:00",
                   next_review_at="2026-10-20T09:00:00"),  # overdue + stubborn
            _state("c", interval_index=0, passed=True, seen=2,
                   last_reviewed="2026-10-31T10:00:00"),
            _state("d"),  # never reviewed
        )
        kpi = ra.kpi_dashboard(phrases, now=now)
        self.assertEqual(kpi["total"], 4)
        self.assertEqual(kpi["due_today"], 1)
        self.assertEqual(kpi["overdue"], 1)
        self.assertEqual(kpi["avg_index"], 0.5)
        self.assertEqual(kpi["pass_rate"], 2 / 3)
        self.assertEqual(kpi["stubborn"], 1)  # b: seen 8, not passed
        self.assertEqual(kpi["never_reviewed"], 1)
        self.assertEqual(kpi["by_source"], {"practice": 4})


class HistoryLoadTest(unittest.TestCase):
    def test_load_history_skips_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2026-11-01.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"date": "2026-11-01", "avg_index": 1.0,
                           "pass_rate": 0.5, "by_index": {0: 2}, "due": 1,
                           "total": 2}, fh)
            with open(os.path.join(d, "2026-11-02.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("not json")
            with open(os.path.join(d, "2026-11-03.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"date": "2026-11-03", "avg_index": 1.5,
                           "pass_rate": None, "by_index": {1: 1}, "due": 0,
                           "total": 1}, fh)
            hist = ra.load_history(d)
            self.assertEqual([h["date"] for h in hist],
                             ["2026-11-01", "2026-11-03"])


class SnapshotHistoryTest(unittest.TestCase):
    def test_writes_daily_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            store = ReviewStore(path=os.path.join(d, "review.json"))
            store.load()
            now = datetime(2026, 11, 1, 12, 0, 0)
            store.record_phrase("hello", source="practice", now=now)
            store.record_result("hello", passed=True, now=now)
            store.record_phrase("world", source="llm", now=now)
            store.record_result("world", passed=False, now=now)
            path = store.snapshot_history(history_dir=os.path.join(d, "hist"))
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as fh:
                snap = json.load(fh)
            self.assertEqual(snap["date"], now.date().isoformat())
            self.assertEqual(snap["total"], 2)
            self.assertEqual(snap["pass_rate"], 0.5)
            self.assertEqual(snap["by_index"], {"0": 2})  # str keys on disk
            # Idempotent same-day: overwrites, still one file.
            store.snapshot_history(history_dir=os.path.join(d, "hist"))
            files = [f for f in os.listdir(os.path.join(d, "hist"))
                     if f.endswith(".json")]
            self.assertEqual(files, ["2026-11-01.json"])


if __name__ == "__main__":
    unittest.main()
