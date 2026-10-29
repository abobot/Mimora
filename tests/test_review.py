# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/review.py (Ebbinghaus spaced-repetition store).

Run: python -m unittest tests.test_review
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from mimora.review import (EBBINGHAUS_INTERVALS_DAYS, ReviewStore)


def _tmp_path() -> str:
    return os.path.join(tempfile.mkdtemp(), "review.json")


class TestReviewStore(unittest.TestCase):
    """The Ebbinghaus schedule: record, advance, fail-reset, due lookup."""

    def setUp(self):
        self.store = ReviewStore(_tmp_path())
        self.store.load()
        self.now = datetime(2026, 10, 30, 12, 0, 0)

    def test_interval_schedule(self):
        self.assertEqual(EBBINGHAUS_INTERVALS_DAYS, (1, 2, 4, 7, 15, 30, 60))

    def test_record_phrase_creates_state_and_is_not_due(self):
        st = self.store.record_phrase("Hello world.", source="llm",
                                      now=self.now)
        self.assertEqual(st["source"], "llm")
        self.assertEqual(st["interval_index"], 0)
        self.assertIsNone(st["next_review_at"])
        self.assertFalse(self.store.is_due("Hello world.", now=self.now))

    def test_first_result_schedules_one_day(self):
        self.store.record_phrase("Hello world.", now=self.now)
        self.store.record_result("Hello world.", True, now=self.now)
        st = self.store.phrase_state("Hello world.")
        self.assertEqual(st["interval_index"], 0)
        self.assertEqual(st["next_review_at"],
                         (self.now + timedelta(days=1)).isoformat(timespec="seconds"))
        self.assertFalse(self.store.is_due("Hello world.", now=self.now))
        self.assertTrue(self.store.is_due(
            "Hello world.", now=self.now + timedelta(days=1)))

    def test_pass_advances_interval(self):
        self.store.record_phrase("A.", now=self.now)
        self.store.record_result("A.", True, now=self.now)
        self.store.record_result("A.", True, now=self.now + timedelta(days=1))
        self.assertEqual(self.store.phrase_state("A.")["interval_index"], 1)
        self.assertEqual(self.store.phrase_state("A.")["next_review_at"],
                         (self.now + timedelta(days=3)).isoformat(timespec="seconds"))

    def test_fail_resets_interval(self):
        self.store.record_phrase("A.", now=self.now)
        self.store.record_result("A.", True, now=self.now)
        self.store.record_result("A.", True, now=self.now + timedelta(days=1))
        self.store.record_result("A.", False, now=self.now + timedelta(days=2))
        st = self.store.phrase_state("A.")
        self.assertEqual(st["interval_index"], 0)
        self.assertEqual(st["next_review_at"],
                         (self.now + timedelta(days=3)).isoformat(timespec="seconds"))

    def test_interval_caps_at_max(self):
        self.store.record_phrase("A.", now=self.now)
        for i in range(10):
            self.store.record_result("A.", True,
                                     now=self.now + timedelta(days=i))
        self.assertEqual(self.store.phrase_state("A.")["interval_index"],
                         len(EBBINGHAUS_INTERVALS_DAYS) - 1)

    def test_due_phrases_sorted_most_overdue_first(self):
        t0 = self.now
        # "soon." is reviewed now -> first interval (1 day) -> due t0+1d.
        self.store.record_phrase("soon.", now=t0)
        self.store.record_result("soon.", True, now=t0)
        # "late." is reviewed 2 days ago after one prior pass -> interval 1
        # (2 days) -> due at t0 (already overdue at t0+1d+1min).
        self.store.record_phrase("late.", now=t0 - timedelta(days=3))
        self.store.record_result("late.", True, now=t0 - timedelta(days=3))
        self.store.record_result("late.", True, now=t0 - timedelta(days=2))
        due = self.store.due_phrases(now=t0 + timedelta(days=1, minutes=1))
        self.assertEqual(due, ["late.", "soon."])  # most overdue first

    def test_due_filter_by_source(self):
        self.store.record_phrase("llm phrase", source="llm", now=self.now)
        self.store.record_result("llm phrase", True, now=self.now)
        self.store.record_phrase("text phrase", source="practice", now=self.now)
        self.store.record_result("text phrase", True, now=self.now)
        t = self.now + timedelta(days=1, minutes=1)
        self.assertEqual(self.store.due_phrases(now=t, source="llm"),
                         ["llm phrase"])
        self.assertEqual(self.store.due_phrases(now=t, source="practice"),
                         ["text phrase"])

    def test_result_without_prior_record_creates_phrase(self):
        # A scored take on an untracked phrase must still schedule a review.
        self.store.record_result("Mystery.", True, now=self.now)
        self.assertEqual(self.store.phrase_state("Mystery.")["interval_index"], 0)
        self.assertTrue(self.store.is_due(
            "Mystery.", now=self.now + timedelta(days=1)))

    def test_record_phrase_is_idempotent_and_preserves_state(self):
        self.store.record_phrase("A.", source="practice", now=self.now)
        self.store.record_result("A.", True, now=self.now)
        # Re-recording must not reset first_seen / interval.
        self.store.record_phrase("A.", source="llm", now=self.now)
        st = self.store.phrase_state("A.")
        self.assertEqual(st["source"], "practice")  # source kept
        self.assertEqual(st["interval_index"], 0)
        self.assertIsNotNone(st["first_seen"])

    def test_persistence_round_trip(self):
        path = _tmp_path()
        s1 = ReviewStore(path)
        s1.load()
        s1.record_phrase("Persist me.", source="llm", now=self.now)
        s1.record_result("Persist me.", True, now=self.now)
        s2 = ReviewStore(path)
        s2.load()
        self.assertEqual(len(s2.all_phrases()), 1)
        st = s2.phrase_state("Persist me.")
        self.assertEqual(st["source"], "llm")
        self.assertEqual(st["interval_index"], 0)

    def test_corrupt_file_starts_fresh(self):
        path = _tmp_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        s = ReviewStore(path)
        s.load()
        self.assertEqual(s.all_phrases(), [])
        s.record_phrase("A.", now=self.now)
        self.assertEqual(len(s.all_phrases()), 1)

    def test_all_phrases_filter(self):
        self.store.record_phrase("llm one", source="llm", now=self.now)
        self.store.record_phrase("text one", source="practice", now=self.now)
        self.assertEqual(len(self.store.all_phrases(source="llm")), 1)
        self.assertEqual(len(self.store.all_phrases(source="practice")), 1)
        self.assertEqual(len(self.store.all_phrases()), 2)


if __name__ == "__main__":
    unittest.main()
