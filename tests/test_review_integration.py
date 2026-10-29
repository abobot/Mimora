# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Integration tests for the app-level Ebbinghaus review wiring.

Covers the controller methods that connect phrase generation to the review
store: ``_select_phrase``, ``_due_review_phrases`` and ``_record_review_result``
(and the review flag on ``on_take_scored``). These run without a Tk root -
the controller is built with ``__new__`` and the review store is pointed at a
temporary file.

Run: python -m unittest tests.test_review_integration
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from mimora import config
from mimora.review import ReviewStore


def _tmp_review_path() -> str:
    return os.path.join(tempfile.mkdtemp(), "review.json")


class ReviewIntegrationTests(unittest.TestCase):
    """app.py review methods against a real (temp-file) ReviewStore."""

    def setUp(self):
        from mimora.app import PronunciationTrainerGUI
        self.c = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
        self.path = _tmp_review_path()
        self._orig_path = config.review_path
        config.review_path = lambda: self.path
        self.c._review_enabled = True
        self.c._review_store = None
        self.c._source_text_provider = mock.MagicMock()
        self.c.llm_mgr = mock.MagicMock()

    def tearDown(self):
        config.review_path = self._orig_path

    def test_due_review_phrases_disabled_returns_empty(self):
        self.c._review_enabled = False
        self.assertEqual(self.c._due_review_phrases("A.", "practice"), [])

    def test_due_review_phrases_builds_store_lazily(self):
        self.assertEqual(self.c._due_review_phrases("A.", "practice"), [])
        self.assertIsNotNone(self.c._review_store)

    def test_due_review_phrases_filters_practice_to_current_text(self):
        store = self.c._due_review_phrases.__self__._review_store
        # Build a store with one due practice sentence not in the current text.
        st = ReviewStore(self.path)
        st.load()
        st.record_phrase("Old sentence.", source="practice",
                         now=datetime(2026, 10, 1))
        st.record_result("Old sentence.", True, now=datetime(2026, 10, 1))
        self.c._review_store = st
        # Not in the current text -> filtered out.
        self.assertEqual(self.c._due_review_phrases(
            "Current sentence here.", "practice"), [])

    def test_due_review_phrases_keeps_practice_in_text(self):
        st = ReviewStore(self.path)
        st.load()
        st.record_phrase("Due now.", source="practice",
                         now=datetime(2026, 10, 1))
        st.record_result("Due now.", True, now=datetime(2026, 10, 1))
        self.c._review_store = st
        due = self.c._due_review_phrases("Due now. Other.", "practice")
        self.assertEqual(due, ["Due now."])

    def test_select_phrase_prefers_due(self):
        st = ReviewStore(self.path)
        st.load()
        st.record_phrase("Due phrase.", source="practice",
                         now=datetime(2026, 10, 1))
        st.record_result("Due phrase.", True, now=datetime(2026, 10, 1))
        self.c._review_store = st
        phrase = self.c._select_phrase("Due phrase.", "full", "practice")
        self.assertEqual(phrase, "Due phrase.")
        self.c._source_text_provider.generate_phrase.assert_not_called()

    def test_select_phrase_generates_fresh_when_nothing_due(self):
        self.c._source_text_provider.generate_phrase.return_value = "fresh."
        phrase = self.c._select_phrase("A. B.", "full", "practice")
        self.assertEqual(phrase, "fresh.")
        self.c._source_text_provider.generate_phrase.assert_called_once_with(
            "A. B.", length="full")

    def test_select_phrase_records_generated_phrase(self):
        self.c._source_text_provider.generate_phrase.return_value = "track me."
        self.c._select_phrase("A.", "full", "practice")
        st = ReviewStore(self.path)
        st.load()
        self.assertEqual(len(st.all_phrases()), 1)
        self.assertEqual(st.all_phrases()[0]["phrase"], "track me.")

    def test_select_phrase_touches_due(self):
        st = ReviewStore(self.path)
        st.load()
        st.record_phrase("Due.", source="practice",
                         now=datetime(2026, 10, 1))
        st.record_result("Due.", True, now=datetime(2026, 10, 1))
        self.c._review_store = st
        self.c._select_phrase("Due.", "full", "practice")
        # The re-presentation itself must be recorded (touch) so it is not
        # lost; the SRS advance happens on scoring.
        st2 = ReviewStore(self.path)
        st2.load()
        self.assertEqual(st2.phrase_state("Due.")["seen"], 1)

    def test_record_review_result_advances(self):
        self.c._record_review_result("Phrase.", True)
        st = ReviewStore(self.path)
        st.load()
        self.assertEqual(st.phrase_state("Phrase.")["interval_index"], 0)
        self.assertIsNotNone(st.phrase_state("Phrase.")["next_review_at"])

    def test_record_review_result_disabled_is_noop(self):
        self.c._review_enabled = False
        self.c._record_review_result("Phrase.", True)
        st = ReviewStore(self.path)
        st.load()
        self.assertEqual(st.all_phrases(), [])

    def test_record_review_result_store_failure_is_silent(self):
        self.c._review_store = mock.MagicMock()
        self.c._review_store.record_result.side_effect = RuntimeError("boom")
        # Must not raise.
        self.c._record_review_result("Phrase.", True)


if __name__ == "__main__":
    unittest.main()
