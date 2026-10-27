# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/persistence/stats.py.

Covers: fresh start, atomic save + reload, session idle split, deletion with
totals recompute, and the error-word / phoneme-score extractors (the ones
that feed the future weakness aggregator).

Runs with the fast suite (``python -m unittest discover -s tests -v``). No
model download, no Tk.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from mimora.persistence.stats import (
    STATS_SCHEMA_VERSION,
    StatsStore,
    compute_words_per_minute,
    extract_error_words,
    extract_phoneme_scores,
)


class _FakeResult:
    """Minimal duck-typed replacement for a PronunciationResult."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FreshStartTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mimora-stats-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = StatsStore(path=os.path.join(self.tmp, "stats.json"))

    def test_empty_data_on_first_load(self):
        self.store.load()
        self.assertEqual(self.store.data["version"], STATS_SCHEMA_VERSION)
        self.assertEqual(self.store.data["sessions"], [])
        self.assertEqual(self.store.data["drill_sessions"], [])

    def test_save_then_reload_round_trip(self):
        self.store.load()
        take = self.store.record_take("hello world", 82.0,
                                      transcript="hello world",
                                      audio_duration_sec=2.0)
        self.assertIsNotNone(take)
        self.store.end_current_session()

        fresh = StatsStore(path=self.store.path)
        fresh.load()
        sessions = fresh.all_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]["takes"]), 1)
        self.assertEqual(sessions[0]["totals"]["takes"], 1)
        self.assertEqual(sessions[0]["totals"]["unique_phrases"], 1)

    def test_corrupt_primary_starts_empty_when_no_bak(self):
        # No .bak to recover from: the store falls back to a fresh skeleton
        # and renames the unreadable primary so it cannot block future writes.
        self.store.load()
        self.store.record_take("first", 70.0)
        # Corrupt the primary file.
        with open(self.store.path, "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        fresh = StatsStore(path=self.store.path)
        fresh.load()
        self.assertTrue(isinstance(fresh.data, dict))
        self.assertIn("sessions", fresh.data)


class SessionLifecycleTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mimora-stats-lifecycle-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # The store now reads idle_timeout from config at call time, so
        # the test must move the fake timestamp far enough to exceed
        # config.IDLE_TIMEOUT (default 1800s). We rewind by 2 hours to
        # be safely above any reasonable config default.
        self.store = StatsStore(path=os.path.join(self.tmp, "stats.json"))

    def test_first_take_opens_a_session(self):
        take = self.store.record_take("hello", 70.0)
        self.assertIsNotNone(take)
        self.assertEqual(len(self.store.all_sessions()), 1)
        self.assertEqual(len(self.store.all_sessions()[0]["takes"]), 1)

    def test_idle_split_creates_second_session(self):
        # Patch the module-level config.IDLE_TIMEOUT so the test does not
        # have to wait 30 real minutes or rewind the timestamp by hours.
        import mimora.config as mconfig
        original = mconfig.IDLE_TIMEOUT
        try:
            mconfig.IDLE_TIMEOUT = 1  # 1 second
            self.store.record_take("first", 70.0)
            # Rewind the only take's timestamp 10 seconds in the past.
            old_dt = datetime.now().astimezone() - timedelta(seconds=10)
            for session in self.store.data["sessions"]:
                for take in session["takes"]:
                    take["timestamp"] = old_dt.isoformat(timespec="seconds")
            self.store.save()
            self.store.record_take("after idle", 80.0)
            sessions = self.store.all_sessions()
            self.assertEqual(len(sessions), 2)
        finally:
            mconfig.IDLE_TIMEOUT = original

    def test_end_current_session_fills_ended_at(self):
        self.store.record_take("one", 75.0)
        self.store.end_current_session()
        current = self.store.current_session()
        self.assertIsNotNone(current["ended_at"])
        # duration_sec may be 0 if the session ended within the same second
        # it started (the truncation of int(seconds)).
        self.assertGreaterEqual(current["duration_sec"], 0)


class DeleteTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mimora-stats-delete-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = StatsStore(path=os.path.join(self.tmp, "stats.json"))
        self.store.load()
        self.store.record_take("alpha", 60.0)
        self.store.record_take("beta", 80.0)
        self.store.record_take("alpha", 90.0)

    def test_delete_single_take_recomputes_totals(self):
        session = self.store.all_sessions()[0]
        self.assertEqual(session["totals"]["takes"], 3)
        first_take = session["takes"][0]
        self.assertTrue(self.store.delete_take(session["id"],
                                               first_take["id"],
                                               delete_audio=False))
        # Reload from disk to confirm the recomputed totals survived.
        fresh = StatsStore(path=self.store.path)
        fresh.load()
        session = fresh.all_sessions()[0]
        self.assertEqual(session["totals"]["takes"], 2)
        # max_score should have dropped from 90 to 80 (we deleted the 60,
        # so max is still 90; unique_phrases stays 2).
        self.assertEqual(session["totals"]["max_score"], 90.0)

    def test_delete_unknown_take_id_is_noop(self):
        session = self.store.all_sessions()[0]
        before = len(session["takes"])
        self.assertFalse(self.store.delete_take(session["id"], "nonexistent"))
        self.assertEqual(len(session["takes"]), before)

    def test_clear_all_removes_everything(self):
        self.store.clear_all(delete_audio=False)
        fresh = StatsStore(path=self.store.path)
        fresh.load()
        self.assertEqual(fresh.all_sessions(), [])
        self.assertEqual(fresh.all_drill_sessions(), [])


class ExtractorTest(unittest.TestCase):

    def test_error_words_from_word_diff_missing_and_substitution(self):
        result = _FakeResult(
            word_diff=[
                {"expected": "Thought", "heard": ""},        # missing
                {"expected": "World", "heard": "Worl"},      # substitution
            ],
            reference_words=[
                {"word": "The", "correct": True},
                {"word": "Alpha", "correct": False},         # pronunciation
            ],
        )
        out = extract_error_words(result)
        words = {entry["word"]: entry["error_type"] for entry in out}
        self.assertEqual(words["thought"], "missing")
        self.assertEqual(words["world"], "substitution")
        self.assertEqual(words["alpha"], "pronunciation")

    def test_error_words_empty_when_result_is_none(self):
        self.assertEqual(extract_error_words(None), [])

    def test_phoneme_scores_from_weak_phonemes(self):
        result = _FakeResult(
            weak_phonemes=[
                {"phoneme": "θ", "severity": 0.9, "count": 3},
                {"phoneme": "r", "severity": 0.3, "count": 1},
            ],
        )
        out = extract_phoneme_scores(result)
        self.assertEqual(len(out), 2)
        # Inverted severity: high severity -> low score.
        theta = next(e for e in out if e["ipa"] == "θ")
        self.assertAlmostEqual(theta["score"], 0.1, places=4)
        self.assertEqual(theta["count"], 3)


class WordsPerMinuteTest(unittest.TestCase):

    def test_wpm_with_known_inputs(self):
        # 12 words in 12 seconds = 60 wpm.
        self.assertEqual(compute_words_per_minute("a b c d e f g h i j k l",
                                                  12.0), 60.0)

    def test_wpm_with_empty_transcript_returns_zero(self):
        self.assertEqual(compute_words_per_minute("", 12.0), 0.0)

    def test_wpm_with_zero_duration_returns_zero(self):
        self.assertEqual(compute_words_per_minute("hello", 0.0), 0.0)


class CorruptFileRecoveryTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mimora-stats-corrupt-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_bad_file_becomes_corrupt_snapshot(self):
        bad = os.path.join(self.tmp, "stats.json")
        with open(bad, "w") as fh:
            fh.write("{ broken")
        store = StatsStore(path=bad)
        store.load()
        self.assertTrue(isinstance(store.data, dict))
        # The bad file should have been renamed out of the way.
        self.assertFalse(os.path.exists(bad))


if __name__ == "__main__":
    unittest.main()
