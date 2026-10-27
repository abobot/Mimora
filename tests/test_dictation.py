# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/dictation (scoring + engine).

Covers word-level comparison, scoring, phrase cycling, mastery detection and
the storage-dict shape. Runs with the fast suite - no Tk, no engine load.
"""

import unittest

from mimora.dictation.engine import DictationEngine
from mimora.dictation.scoring import (
    compare_sentences,
    score_dictation,
    tokenize_words,
)


class WordAlignmentTest(unittest.TestCase):
    """compare_sentences: exact matches, misses, spelling errors, extras."""

    def test_exact_match_scores_full(self):
        result = compare_sentences("The quick brown fox.", "The quick brown fox.")
        self.assertEqual(result["exact_matches"], 4)
        self.assertEqual(result["accuracy"], 100.0)
        self.assertEqual([s["state"] for s in result["status"]],
                         ["correct"] * 4)
        self.assertEqual(result["extra_words"], [])

    def test_case_and_punctuation_are_normalised(self):
        result = compare_sentences("Hello, world.", "hello world")
        self.assertEqual(result["accuracy"], 100.0)

    def test_missing_word_is_missed(self):
        # "brown" omitted -> 3/4 correct.
        result = compare_sentences("The quick fox.", "The quick brown fox.")
        self.assertEqual(result["exact_matches"], 3)
        self.assertAlmostEqual(result["accuracy"], 75.0)
        states = [s["state"] for s in result["status"]]
        self.assertIn("missed", states)

    def test_typo_is_wrong_not_missed(self):
        result = compare_sentences("The quik brown fox.", "The quick brown fox.")
        states = [s["state"] for s in result["status"]]
        self.assertIn("wrong", states)
        self.assertEqual(result["exact_matches"], 3)

    def test_extra_word_is_flagged(self):
        result = compare_sentences("The quick very brown fox.", "The quick brown fox.")
        self.assertTrue(result["extra_words"])
        self.assertGreaterEqual(result["accuracy"], 0)

    def test_inserted_word_in_middle_keeps_later_words_correct(self):
        # An insertion must not shift every later word into "wrong".
        result = compare_sentences("The quick brown jumping fox.",
                                   "The quick brown fox.")
        states = [s["state"] for s in result["status"]]
        self.assertEqual(states[-1], "correct")  # "fox" stays correct

    def test_empty_user_scores_zero(self):
        result = compare_sentences("", "The quick brown fox.")
        self.assertEqual(result["accuracy"], 0.0)
        self.assertEqual(result["word_count"], 4)

    def test_empty_reference_marks_all_extra(self):
        result = compare_sentences("some words", "")
        self.assertEqual(result["word_count"], 0)
        self.assertEqual(result["accuracy"], 0.0)
        self.assertTrue(result["extra_words"])

    def test_status_is_aligned_to_reference_words(self):
        result = compare_sentences("The fox.", "The quick brown fox.")
        self.assertEqual(len(result["status"]), 4)
        for entry in result["status"]:
            self.assertIn(entry["state"], ("correct", "missed", "wrong"))


class TokenizeTest(unittest.TestCase):
    """tokenize_words: public helper the UI uses to size word slots."""

    def test_counts_words_like_comparison(self):
        text = "The quick brown fox jumps over the lazy dog."
        self.assertEqual(len(tokenize_words(text)), 9)

    def test_empty_text(self):
        self.assertEqual(tokenize_words(""), [])
        self.assertEqual(tokenize_words("   "), [])

    def test_keeps_contractions(self):
        words = tokenize_words("Don't stop believing.")
        self.assertEqual(words, ["don't", "stop", "believing"])

    def test_matches_comparison_reference_words(self):
        result = compare_sentences("x", "One two three.")
        self.assertEqual(tokenize_words("One two three."),
                         result["reference_words"])


class ScoreTest(unittest.TestCase):
    """score_dictation wraps the comparison with a 0-100 score."""

    def test_score_equals_accuracy(self):
        result = score_dictation("The quick brown fox.", "The quick brown fox.")
        self.assertEqual(result["score"], 100.0)
        self.assertIn("passed", result)
        self.assertEqual(result["total_words"], 4)

    def test_partial_score_rounds(self):
        result = score_dictation("The quick fox.", "The quick brown fox.")
        self.assertEqual(result["score"], 75.0)


class EngineTest(unittest.TestCase):

    def test_next_phrase_comes_from_source(self):
        engine = DictationEngine(source_text="One two. Three four.")
        phrases = engine.candidate_phrases
        self.assertGreaterEqual(len(phrases), 2)
        self.assertIn("One two", phrases)

    def test_empty_source_falls_back_to_pool(self):
        engine = DictationEngine(source_text="")
        self.assertTrue(engine.candidate_phrases)

    def test_phrase_cycles(self):
        engine = DictationEngine(source_text="A. B. C.", max_attempts=20)
        seen = [engine.next_phrase() for _ in range(6)]
        self.assertGreater(len(set(seen)), 1)

    def test_record_attempt_sets_pass_by_threshold(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B.")
        rec = engine.record_attempt("A", "A", 90)
        self.assertTrue(rec.passed)
        rec2 = engine.record_attempt("B", "B", 70)
        self.assertFalse(rec2.passed)

    def test_mastered_after_three_consecutive_passes(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B. C.")
        engine.record_attempt("a", "a", 90)
        engine.record_attempt("b", "b", 90)
        self.assertFalse(engine.mastered)
        engine.record_attempt("c", "c", 90)
        self.assertTrue(engine.mastered)

    def test_fail_resets_streak(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B. C.")
        for _ in range(3):
            engine.record_attempt("x", "x", 90)
        self.assertTrue(engine.mastered)
        engine.record_attempt("y", "y", 50)
        self.assertEqual(engine.mastered_streak(), 0)

    def test_done_when_max_attempts_reached(self):
        engine = DictationEngine(pass_threshold=95.0, max_attempts=3,
                                 source_text="A. B. C.")
        for _ in range(3):
            engine.record_attempt("x", "x", 50)
        self.assertTrue(engine.done)
        self.assertFalse(engine.mastered)

    def test_progress_shape(self):
        engine = DictationEngine(source_text="A. B. C.")
        engine.record_attempt("a", "a", 90)
        p = engine.progress()
        self.assertEqual(p["current"], 1)
        self.assertEqual(p["passed"], 1)
        self.assertEqual(p["scores"], [90.0])
        self.assertIn("threshold", p)

    def test_position_tracks_navigation(self):
        engine = DictationEngine(source_text="A. B. C. D. E.")
        # Fresh engine: the first next_phrase() is sentence 1 (the view consumes
        # one phrase on open, so its counter starts at 1).
        engine.next_phrase()
        self.assertEqual(engine.position, 1)
        engine.next_phrase()
        self.assertEqual(engine.position, 2)
        engine.previous_phrase()
        self.assertEqual(engine.position, 1)
        p = engine.progress()
        self.assertEqual(p["current"], 1)

    def test_position_clamped_to_floor(self):
        engine = DictationEngine(source_text="A. B. C.")
        engine.next_phrase()
        for _ in range(3):
            engine.previous_phrase()
        self.assertEqual(engine.position, 1)

    def test_finish_summary(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B. C.")
        engine.record_attempt("a", "a", 70)
        engine.record_attempt("b", "b", 90)
        outcome = engine.finish()
        self.assertEqual(outcome.total_attempts, 2)
        self.assertEqual(outcome.total_passed, 1)
        self.assertEqual(outcome.first_score, 70.0)
        self.assertEqual(outcome.last_score, 90.0)

    def test_to_storage_dict_matches_store_shape(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B.")
        engine.record_attempt("a", "a", 90)
        data = engine.to_storage_dict()
        self.assertEqual(data["attempts"], 1)
        self.assertEqual(data["passed"], 1)
        self.assertIn(data["outcome"], ("mastered", "unfinished"))
        self.assertEqual(len(data["takes"]), 1)
        take = data["takes"][0]
        self.assertIn("user_text", take)
        self.assertIn("reference_text", take)
        self.assertIn("score", take)
        self.assertIn("passed", take)
        self.assertIn("wrong_words", take)
        self.assertIn("extra_words", take)

    def test_set_source_text_rebuilds_candidates(self):
        engine = DictationEngine(source_text="One two. Three four.")
        self.assertIn("One two", engine.candidate_phrases)
        engine.set_source_text("Five six. Seven eight.")
        self.assertIn("Five six", engine.candidate_phrases)
        self.assertNotIn("One two", engine.candidate_phrases)

    def test_set_source_text_empty_falls_back_to_pool(self):
        engine = DictationEngine(source_text="One two.")
        engine.set_source_text("")
        self.assertTrue(engine.candidate_phrases)

    def test_wrong_words_are_stored_per_attempt(self):
        engine = DictationEngine(source_text="The quick brown fox.")
        engine.record_attempt("The quik fox.", "The quick brown fox.", 66.7,
                              wrong_words=["quick", "brown"],
                              extra_words=["extra"])
        rec = engine.attempts[-1]
        self.assertEqual(rec.wrong_words, ["quick", "brown"])
        self.assertEqual(rec.extra_words, ["extra"])

    def test_finish_aggregates_distinct_wrong_words(self):
        engine = DictationEngine(pass_threshold=80.0, source_text="A. B. C.")
        engine.record_attempt("x", "A B", 50, wrong_words=["a", "b"])
        engine.record_attempt("y", "A C", 50, wrong_words=["a", "c"])
        outcome = engine.finish()
        self.assertEqual(outcome.wrong_words, ["a", "b", "c"])

    def test_storage_dict_includes_aggregated_words(self):
        engine = DictationEngine(source_text="A. B. C.")
        engine.record_attempt("x", "A B", 50, wrong_words=["a"],
                              extra_words=["zzz"])
        data = engine.to_storage_dict()
        self.assertEqual(data["wrong_words"], ["a"])
        self.assertEqual(data["extra_words"], ["zzz"])
        self.assertEqual(data["takes"][0]["wrong_words"], ["a"])


if __name__ == "__main__":
    unittest.main()
