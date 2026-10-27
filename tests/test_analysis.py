# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/analysis.

Covers the two aggregators, the IPA lookup, the ZH interference table
and the rule-based suggestion generator. Runs with the fast suite -
no Tk, no model, no network.
"""

import unittest

from mimora.analysis import (
    ZH_INTERFERENCE,
    aggregate_error_words,
    aggregate_phoneme_scores,
    generate_suggestions,
    interference_priority,
    sorted_by_error_rate,
    sorted_by_weakness,
    top_words,
    word_to_ipa,
)


def _fake_take(phrase="hello world", score=75.0, **fields):
    """Build a take dict with the fields the aggregators read.

    The default is a clean take with no errors and no phonemes, so
    tests only need to override what they are checking.
    """
    take = {
        "id": "t-test",
        "timestamp": "2026-10-26T18:00:00+08:00",
        "phrase": phrase,
        "score": score,
        "grade": "",
        "grade_value": 0.0,
        "attempts": 1,
        "audio_duration_sec": 2.0,
        "transcript": phrase,
        "words_per_minute": 60.0,
        "error_words": [],
        "phoneme_scores": [],
        "session_id": "s-test",
    }
    take.update(fields)
    return take


def _session(id_="s-1"):
    return {"id": id_, "takes": []}


class ErrorWordsAggregationTest(unittest.TestCase):

    def test_counts_word_occurrences_and_error_rate(self):
        takes = [
            (_session(), _fake_take(phrase="the thought is loud",
                                   error_words=[{"word": "Thought",
                                                "score": 0.3,
                                                "error_type": "pronunciation"}])),
            (_session("s-2"), _fake_take(phrase="thought about it",
                                        error_words=[{"word": "thought",
                                                     "score": 0.4,
                                                     "error_type": "pronunciation"}])),
            (_session("s-3"), _fake_take(phrase="think about thoughts",
                                        error_words=[{"word": "Thought",
                                                     "score": 0.8,
                                                     "error_type": "pronunciation"}])),
        ]
        agg = aggregate_error_words(takes)
        self.assertIn("thought", agg)
        self.assertEqual(agg["thought"]["count"], 3)
        self.assertEqual(agg["thought"]["error_count"], 2)
        self.assertEqual(agg["thought"]["error_rate"], round(2 / 3, 4))
        self.assertEqual(agg["thought"]["status"], "need_practice")

    def test_status_improving_when_recent_scores_are_high(self):
        # Two bad scores, then three good - recent5 mean >= 0.7 -> improving.
        takes = [
            (_session("s-1"), _fake_take(error_words=[{"word": "a", "score": 0.5, "error_type": "pronunciation"}])),
            (_session("s-2"), _fake_take(error_words=[{"word": "a", "score": 0.5, "error_type": "pronunciation"}])),
            (_session("s-3"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}])),
            (_session("s-4"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}])),
            (_session("s-5"), _fake_take(error_words=[{"word": "a", "score": 0.95, "error_type": "pronunciation"}])),
        ]
        agg = aggregate_error_words(takes)
        self.assertEqual(agg["a"]["status"], "improving")

    def test_top_words_excludes_improving(self):
        takes = [
            (_session("s-1"), _fake_take(error_words=[{"word": "a", "score": 0.85, "error_type": "pronunciation"}])),
            (_session("s-2"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}])),
            (_session("s-3"), _fake_take(error_words=[{"word": "a", "score": 0.95, "error_type": "pronunciation"}])),
            (_session("s-4"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}])),
            (_session("s-5"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}])),
            # "b" is a real problem word
            (_session("s-1"), _fake_take(error_words=[{"word": "b", "score": 0.3, "error_type": "pronunciation"}])),
            (_session("s-2"), _fake_take(error_words=[{"word": "b", "score": 0.4, "error_type": "pronunciation"}])),
            (_session("s-3"), _fake_take(error_words=[{"word": "b", "score": 0.5, "error_type": "pronunciation"}])),
        ]
        agg = aggregate_error_words(takes)
        top = top_words(agg, n=5)
        self.assertNotIn("a", top)  # improving
        self.assertIn("b", top)

    def test_lookback_limits_recent_takes(self):
        takes = [
            (_session(f"s-{i}"), _fake_take(error_words=[{"word": "a", "score": 0.9, "error_type": "pronunciation"}]))
            for i in range(20)
        ]
        agg = aggregate_error_words(takes, lookback=5)
        self.assertEqual(agg["a"]["count"], 5)

    def test_sorted_by_error_rate_puts_need_practice_first(self):
        takes = [
            (_session("s-1"), _fake_take(error_words=[
                {"word": "a", "score": 0.9, "error_type": "pronunciation"},
                {"word": "b", "score": 0.3, "error_type": "pronunciation"},
            ])),
            (_session("s-2"), _fake_take(error_words=[
                {"word": "b", "score": 0.3, "error_type": "pronunciation"},
                {"word": "b", "score": 0.3, "error_type": "pronunciation"},
            ])),
            (_session("s-3"), _fake_take(error_words=[
                {"word": "b", "score": 0.3, "error_type": "pronunciation"},
            ])),
        ]
        ranked = sorted_by_error_rate(aggregate_error_words(takes))
        # "b" is need_practice (3 errors, all low), should come first.
        self.assertEqual(ranked[0][0], "b")


class PhonemeAggregationTest(unittest.TestCase):

    def test_counts_and_averages(self):
        takes = [
            (_session("s-1"), _fake_take(phoneme_scores=[
                {"ipa": "θ", "score": 0.2, "count": 2},
                {"ipa": "r", "score": 0.8, "count": 1},
            ])),
            (_session("s-2"), _fake_take(phoneme_scores=[
                {"ipa": "θ", "score": 0.4, "count": 1},
                {"ipa": "r", "score": 0.9, "count": 1},
            ])),
        ]
        agg = aggregate_phoneme_scores(takes)
        self.assertEqual(agg["θ"]["count"], 2)
        self.assertAlmostEqual(agg["θ"]["avg_score"], 0.3, places=3)
        self.assertEqual(agg["θ"]["priority"], 1)  # top-ranked ZH problem

    def test_sorted_by_weakness_puts_worst_first(self):
        takes = [
            (_session(), _fake_take(phoneme_scores=[
                {"ipa": "θ", "score": 0.9, "count": 1},
                {"ipa": "r", "score": 0.2, "count": 1},
            ])),
        ]
        agg = aggregate_phoneme_scores(takes)
        ranked = sorted_by_weakness(agg)
        self.assertEqual(ranked[0][0], "r")  # worse avg_score


class InterferencePriorityTest(unittest.TestCase):

    def test_th_is_top_priority_for_zh(self):
        self.assertEqual(interference_priority("θ", native_language="zh"), 1)

    def test_unknown_ipa_returns_neutral(self):
        self.assertEqual(interference_priority("x", native_language="zh"), 99)

    def test_unknown_language_returns_neutral(self):
        self.assertEqual(interference_priority("θ", native_language="xx"), 99)

    def test_table_is_nonempty(self):
        self.assertGreater(len(ZH_INTERFERENCE), 5)


class IpaMapTest(unittest.TestCase):

    def test_known_word_returns_expected_ipa(self):
        # Any of these is a plausible transcription.
        self.assertIn("θ", word_to_ipa("thought"))
        self.assertIn("r", word_to_ipa("run"))

    def test_empty_string_returns_empty(self):
        self.assertEqual(word_to_ipa(""), "")

    def test_unknown_word_falls_back_to_heuristic(self):
        # Not in the known table but the heuristic should still
        # produce *something* (at least a vowel).
        result = word_to_ipa("zzz")
        self.assertIsInstance(result, str)
        self.assertTrue(result)


class SuggestionGeneratorTest(unittest.TestCase):

    def test_produces_phoneme_suggestion_for_weak_zh_problem(self):
        takes = [
            (_session(), _fake_take(phoneme_scores=[
                {"ipa": "θ", "score": 0.2, "count": 2},
                {"ipa": "θ", "score": 0.3, "count": 1},
            ])),
        ]
        agg = aggregate_phoneme_scores(takes, native_language="zh")
        out = generate_suggestions({}, agg)
        phoneme_ids = [s["id"] for s in out if s["kind"] == "phoneme"]
        self.assertIn("phoneme:θ", phoneme_ids)

    def test_produces_word_suggestion_for_need_practice_word(self):
        takes = [
            (_session(f"s-{i}"), _fake_take(error_words=[
                {"word": "thought", "score": 0.3, "error_type": "pronunciation"}
            ]))
            for i in range(3)
        ]
        word_agg = aggregate_error_words(takes)
        # All 3 occurrences are bad, error_rate = 1.0, count = 3.
        # Not "improving" (recent5 mean is 0.3), so it should be "need_practice".
        self.assertEqual(word_agg["thought"]["status"], "need_practice")
        out = generate_suggestions(word_agg, {})
        word_ids = [s["id"] for s in out if s["kind"] == "word"]
        self.assertIn("word:thought", word_ids)

    def test_produces_speed_suggestion_when_too_fast(self):
        out = generate_suggestions({}, {}, recent_wpm=160.0)
        speed_ids = [s["id"] for s in out if s["kind"] == "speed"]
        self.assertIn("wpm:fast", speed_ids)

    def test_no_speed_suggestion_when_in_target_band(self):
        out = generate_suggestions({}, {}, recent_wpm=120.0)
        self.assertFalse([s for s in out if s["kind"] == "speed"])


if __name__ == "__main__":
    unittest.main()
