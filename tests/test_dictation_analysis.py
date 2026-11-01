# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the dictation stats aggregation helpers.

Covers the pure, Tk-free aggregation in :mod:`mimora.dictation_analysis` that
feeds the stats dashboard's Dictation tab: word-error balance, spelling-error
frequencies, first→last progress pairs, the mastery outcome distribution, the
KPI metrics, the attempt histogram and pass-rate curve, the three error kinds
by week, the top wrong words, the word-by-week heatmap, the score distribution
and the per-session attempt series.

All inputs are synthetic dictation-session dicts shaped exactly like the store
writes (``user_text`` / ``reference_text`` per take). The helpers re-derive the
word-level states from the texts via :func:`mimora.dictation.scoring.compare_sentences`
rather than trusting the possibly-stale stored ``wrong_words``/``extra_words``
fields, so stale artifacts (e.g. an ``''`` or a word not present in the text)
never leak into the totals. Runs in the fast suite: no Tk, no models.
"""

import unittest


def _take(user_text, reference_text, score=100.0, passed=True, attempt=1):
    return {
        "user_text": user_text,
        "reference_text": reference_text,
        "score": score,
        "passed": passed,
        "attempt": attempt,
    }


def _session(sid, takes, first_score, last_score, outcome,
             started_at="2026-10-28T03:11:24+08:00",
             ended_at="2026-10-28T03:11:24+08:00"):
    return {
        "id": sid,
        "first_score": first_score,
        "last_score": last_score,
        "outcome": outcome,
        "started_at": started_at,
        "ended_at": ended_at,
        "takes": takes,
    }


class WrongVsExtraCountsTest(unittest.TestCase):
    def test_missed_and_wrong_are_wrong_extra_is_separate(self):
        from mimora.dictation_analysis import wrong_vs_extra_counts
        sessions = [
            _session("a", [
                # Alignment is per REFERENCE word: "quick" is wrong (user typed
                # "quik"), "brown" is missed (omitted). wrong = 2.
                _take("the quik fox", "The quick brown fox.", 60.0, False),
                # Perfect take: no wrong, no extra.
                _take("the quick brown fox", "The quick brown fox.", 100.0, True),
            ], 60.0, 100.0, "mastered"),
            _session("b", [
                # "extra" is not in the reference: one extra word.
                _take("hello world extra", "Hello world.", 80.0, True),
            ], 80.0, 80.0, "unfinished"),
        ]
        self.assertEqual(
            wrong_vs_extra_counts(sessions),
            {"wrong": 2, "extra": 1},  # quick(wrong) + brown(missed) + extra

        )

    def test_empty_input(self):
        from mimora.dictation_analysis import wrong_vs_extra_counts
        self.assertEqual(wrong_vs_extra_counts([]), {"wrong": 0, "extra": 0})

    def test_stale_extra_word_ignored(self):
        # A take whose stored extra_words contain an artifact that is not part
        # of the typed text must not be counted (derivation, not storage).
        from mimora.dictation_analysis import wrong_vs_extra_counts
        sessions = [
            _session("a", [
                # Stored extra_words=['zzz'] but user_text has no 'zzz'.
                _take("the quik fox", "The quick brown fox.", 60.0, False),
            ], 60.0, 60.0, "unfinished"),
        ]
        self.assertEqual(
            wrong_vs_extra_counts(sessions),
            {"wrong": 2, "extra": 0},  # quick(wrong) + brown(missed); 'zzz' ignored

        )


class SpellingFrequenciesTest(unittest.TestCase):
    def test_counts_only_wrong_states(self):
        from mimora.dictation_analysis import spelling_frequencies
        sessions = [
            _session("a", [
                _take("the quik fox", "The quick brown fox.", 60.0, False),
                _take("the quik quik", "The quick brown fox.", 40.0, False),
            ], 60.0, 40.0, "unfinished"),
        ]
        # Per-reference-word wrongs: take1 -> quick; take2 -> quick, brown.
        # quick=2, brown=1 (missed states are not spelling errors).
        self.assertEqual(
            spelling_frequencies(sessions, limit=5),
            [("quick", 2), ("brown", 1)],
        )

    def test_limit_applies(self):
        from mimora.dictation_analysis import spelling_frequencies
        sessions = [
            _session("a", [
                # "x y" occupy the D/E slots -> D and E are "wrong".
                _take("a b c x y", "A B C D E F", 30.0, False),
            ], 30.0, 30.0, "unfinished"),
        ]
        self.assertEqual(len(spelling_frequencies(sessions, limit=2)), 2)

    def test_perfect_take_yields_none(self):
        from mimora.dictation_analysis import spelling_frequencies
        sessions = [
            _session("a", [
                _take("the quick brown fox", "The quick brown fox.", 100.0, True),
            ], 100.0, 100.0, "mastered"),
        ]
        self.assertEqual(spelling_frequencies(sessions), [])


class ProgressPairsTest(unittest.TestCase):
    def test_pairs_in_stored_order(self):
        from mimora.dictation_analysis import progress_pairs
        sessions = [
            _session("a", [_take("x", "x")], 50.0, 90.0, "mastered"),
            _session("b", [_take("y", "y")], 10.0, 10.0, "unfinished"),
        ]
        self.assertEqual(progress_pairs(sessions), [(50.0, 90.0), (10.0, 10.0)])

    def test_empty_input(self):
        from mimora.dictation_analysis import progress_pairs
        self.assertEqual(progress_pairs([]), [])


class OutcomeDistributionTest(unittest.TestCase):
    def test_counts_mastered_and_unfinished(self):
        from mimora.dictation_analysis import outcome_distribution
        sessions = [
            _session("a", [_take("x", "x")], 100.0, 100.0, "mastered"),
            _session("b", [_take("y", "y")], 0.0, 0.0, "unfinished"),
            _session("c", [_take("z", "z")], 50.0, 50.0, "unfinished"),
        ]
        self.assertEqual(
            outcome_distribution(sessions),
            {"mastered": 1, "unfinished": 2},
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import outcome_distribution
        self.assertEqual(outcome_distribution([]), {"mastered": 0, "unfinished": 0})


class MasteryRateTest(unittest.TestCase):
    def test_ratio_of_mastered(self):
        from mimora.dictation_analysis import mastery_rate
        sessions = [
            _session("a", [_take("x", "x")], 100.0, 100.0, "mastered"),
            _session("b", [_take("y", "y")], 0.0, 0.0, "unfinished"),
            _session("c", [_take("z", "z")], 50.0, 50.0, "mastered"),
        ]
        self.assertAlmostEqual(mastery_rate(sessions), 2 / 3)

    def test_empty_input(self):
        from mimora.dictation_analysis import mastery_rate
        self.assertEqual(mastery_rate([]), 0.0)


class AverageAttemptsTest(unittest.TestCase):
    def test_mean_of_take_counts(self):
        from mimora.dictation_analysis import average_attempts
        sessions = [
            _session("a", [_take("x", "x"), _take("x", "x")], 50.0, 90.0, "mastered"),
            _session("b", [_take("y", "y")], 10.0, 10.0, "unfinished"),
            _session("c", [], 0.0, 0.0, "unfinished"),
        ]
        self.assertAlmostEqual(average_attempts(sessions), 1.0)

    def test_empty_input(self):
        from mimora.dictation_analysis import average_attempts
        self.assertEqual(average_attempts([]), 0.0)


class AverageGainTest(unittest.TestCase):
    def test_mean_last_minus_first_over_taked_sessions(self):
        from mimora.dictation_analysis import average_gain
        sessions = [
            _session("a", [_take("x", "x")], 50.0, 90.0, "mastered"),
            _session("b", [_take("y", "y")], 10.0, 10.0, "unfinished"),
            # A 0-take session has no first/last and is excluded from the mean.
            _session("c", [], 0.0, 0.0, "unfinished"),
        ]
        self.assertAlmostEqual(average_gain(sessions), 20.0)

    def test_empty_input(self):
        from mimora.dictation_analysis import average_gain
        self.assertEqual(average_gain([]), 0.0)


class SessionDatesTest(unittest.TestCase):
    def test_uses_ended_at_and_drops_unparseable(self):
        from mimora.dictation_analysis import session_dates
        sessions = [
            _session("a", [_take("x", "x")], 50.0, 90.0, "mastered",
                     ended_at="2026-10-28T03:11:24+08:00"),
            _session("b", [_take("y", "y")], 10.0, 10.0, "unfinished",
                     started_at="2026-10-29T05:00:00+08:00",
                     ended_at=""),
            _session("c", [_take("z", "z")], 10.0, 10.0, "unfinished",
                     started_at="not-a-date", ended_at="not-a-date"),
        ]
        self.assertEqual(
            session_dates(sessions),
            [("a", "2026-10-28"), ("b", "2026-10-29")],
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import session_dates
        self.assertEqual(session_dates([]), [])


class MasteryByWeekTest(unittest.TestCase):
    def test_groups_by_week_oldest_first(self):
        from mimora.dictation_analysis import mastery_by_week
        sessions = [
            _session("a", [_take("x", "x")], 100.0, 100.0, "mastered",
                     ended_at="2026-10-28T03:11:24+08:00"),
            _session("b", [_take("y", "y")], 0.0, 0.0, "unfinished",
                     ended_at="2026-10-29T05:00:00+08:00"),
            _session("c", [_take("z", "z")], 0.0, 0.0, "unfinished",
                     ended_at="2026-11-02T05:00:00+08:00"),
        ]
        # 10-28 and 10-29 fall in the same ISO week (Mon 2026-10-26);
        # 11-02 starts the next week (Mon 2026-11-02).
        self.assertEqual(
            mastery_by_week(sessions),
            [("2026-10-26", {"mastered": 1, "unfinished": 1}),
             ("2026-11-02", {"mastered": 0, "unfinished": 1})],
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import mastery_by_week
        self.assertEqual(mastery_by_week([]), [])


class AttemptsHistogramTest(unittest.TestCase):
    def test_counts_sessions_per_attempt_count(self):
        from mimora.dictation_analysis import attempts_histogram
        sessions = [
            _session("a", [_take("x", "x"), _take("x", "x")], 50.0, 90.0, "mastered"),
            _session("b", [_take("y", "y")], 10.0, 10.0, "unfinished"),
            _session("c", [], 0.0, 0.0, "unfinished"),
        ]
        self.assertEqual(attempts_histogram(sessions),
                         [(0, 1), (1, 1), (2, 1)])

    def test_empty_input(self):
        from mimora.dictation_analysis import attempts_histogram
        self.assertEqual(attempts_histogram([]), [])


class PassRateByAttemptTest(unittest.TestCase):
    def test_rates_by_attempt_number(self):
        from mimora.dictation_analysis import pass_rate_by_attempt
        sessions = [
            _session("a", [
                _take("x", "x", 50.0, False, attempt=1),
                _take("x", "x", 100.0, True, attempt=2),
            ], 50.0, 100.0, "mastered"),
            _session("b", [
                _take("y", "y", 0.0, False, attempt=1),
            ], 0.0, 0.0, "unfinished"),
        ]
        # Attempt 1: both sessions failed -> 0.0; attempt 2: 1 passed of 1.
        self.assertEqual(
            pass_rate_by_attempt(sessions),
            [(1, 0.0), (2, 1.0)],
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import pass_rate_by_attempt
        self.assertEqual(pass_rate_by_attempt([]), [])


class ErrorTypeTotalsTest(unittest.TestCase):
    def test_three_categories(self):
        from mimora.dictation_analysis import error_type_totals
        sessions = [
            _session("a", [
                # quick=spelling(wrong), brown=missed; no extra.
                _take("the quik fox", "The quick brown fox.", 60.0, False),
                # one extra word.
                _take("hello world extra", "Hello world.", 80.0, True),
            ], 60.0, 80.0, "unfinished"),
        ]
        self.assertEqual(
            error_type_totals(sessions),
            {"missed": 1, "extra": 1, "spelling": 1},
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import error_type_totals
        self.assertEqual(error_type_totals([]),
                         {"missed": 0, "extra": 0, "spelling": 0})


class ErrorTypeTrendTest(unittest.TestCase):
    def test_groups_by_week(self):
        from mimora.dictation_analysis import error_type_trend
        sessions = [
            _session("a", [
                _take("the quik fox", "The quick brown fox.", 60.0, False),
            ], 60.0, 60.0, "unfinished",
            ended_at="2026-10-28T03:11:24+08:00"),
            _session("b", [
                _take("a b c x y", "A B C D E F", 30.0, False),
            ], 30.0, 30.0, "unfinished",
            ended_at="2026-10-29T05:00:00+08:00"),
        ]
        # Same ISO week -> one entry with the totals of both sessions:
        # missed = brown + f = 2; spelling = quick + d + e = 3.
        self.assertEqual(
            error_type_trend(sessions),
            [("2026-10-26", {"missed": 2, "extra": 0, "spelling": 3})],
        )

    def test_empty_input(self):
        from mimora.dictation_analysis import error_type_trend
        self.assertEqual(error_type_trend([]), [])


class TopWrongWordsTest(unittest.TestCase):
    def test_ranks_by_occurrence(self):
        from mimora.dictation_analysis import top_wrong_words
        sessions = [
            _session("a", [
                _take("the quik fox", "The quick brown fox.", 60.0, False),
                _take("the quik quik", "The quick brown fox.", 40.0, False),
            ], 60.0, 40.0, "unfinished"),
        ]
        # quick=2, brown=2 (second take has brown wrong too), fox=1 missed.
        self.assertEqual(top_wrong_words(sessions, limit=5),
                         [("quick", 2), ("brown", 2), ("fox", 1)])

    def test_limit_and_empty(self):
        from mimora.dictation_analysis import top_wrong_words
        self.assertEqual(top_wrong_words([], limit=5), [])


class WordByWeekHeatmapTest(unittest.TestCase):
    def test_matrix_shape_and_counts(self):
        from mimora.dictation_analysis import word_by_week_heatmap
        sessions = [
            _session("a", [
                _take("the quik fox", "The quick brown fox.", 60.0, False),
            ], 60.0, 60.0, "unfinished",
            ended_at="2026-10-28T03:11:24+08:00"),
        ]
        words, weeks, matrix = word_by_week_heatmap(sessions, top_n=3)
        self.assertIn("quick", words)
        self.assertIn("brown", words)
        self.assertEqual(weeks, ["2026-10-26"])
        # words[i] -> matrix[i] is the count in weeks[j].
        iq = words.index("quick")
        self.assertEqual(matrix[iq][0], 1)
        ib = words.index("brown")
        self.assertEqual(matrix[ib][0], 1)

    def test_empty_input(self):
        from mimora.dictation_analysis import word_by_week_heatmap
        self.assertEqual(word_by_week_heatmap([]), ([], [], []))


class ScoreDistributionTest(unittest.TestCase):
    def test_bins_split_by_passed(self):
        from mimora.dictation_analysis import score_distribution
        sessions = [
            _session("a", [
                _take("x", "x", 95.0, True),
                _take("x", "x", 85.0, True),
                _take("x", "x", 40.0, False),
            ], 40.0, 95.0, "unfinished"),
        ]
        dist = score_distribution(sessions, bins=10)
        self.assertEqual(len(dist), 10)
        # Labels are bin lower bounds ("0".."90"). Bin "90" -> 1 passed;
        # bin "80" -> 1 passed; bin "40" -> 1 failed.
        self.assertEqual(dist[9], ("90", 1, 0))
        self.assertEqual(dist[8], ("80", 1, 0))
        self.assertEqual(dist[4], ("40", 0, 1))

    def test_empty_input(self):
        from mimora.dictation_analysis import score_distribution
        # Always 10 empty bins so the chart draws a full axis even with no
        # takes in the selected range.
        self.assertEqual(len(score_distribution([])), 10)
        self.assertTrue(all(p == 0 and f == 0 for _, p, f in score_distribution([])))


class AttemptSeriesTest(unittest.TestCase):
    def test_series_exclude_zero_take_sessions(self):
        from mimora.dictation_analysis import attempt_series
        sessions = [
            _session("a", [
                _take("x", "x", 50.0, False, attempt=1),
                _take("x", "x", 100.0, True, attempt=2),
            ], 50.0, 100.0, "mastered"),
            _session("b", [], 0.0, 0.0, "unfinished"),
        ]
        series = attempt_series(sessions)
        self.assertEqual(len(series), 1)
        sid, scores, mastered = series[0]
        self.assertEqual(sid, "a")
        self.assertEqual(scores, [50.0, 100.0])
        self.assertTrue(mastered)

    def test_empty_input(self):
        from mimora.dictation_analysis import attempt_series
        self.assertEqual(attempt_series([]), [])


if __name__ == "__main__":
    unittest.main()
