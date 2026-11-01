# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the dictation stats aggregation helpers.

Covers the pure, Tk-free aggregation in :mod:`mimora.dictation_analysis` that
feeds the stats dashboard's Dictation tab: word-error balance, spelling-error
frequencies, first→last progress pairs and the mastery outcome distribution.

All inputs are synthetic dictation-session dicts shaped exactly like the store
writes (``user_text`` / ``reference_text`` per take). The helpers re-derive the
word-level states from the texts via :func:`mimora.dictation.scoring.compare_sentences`
rather than trusting the possibly-stale stored ``wrong_words``/``extra_words``
fields, so stale artifacts (e.g. an ``''`` or a word not present in the text)
never leak into the totals. Runs in the fast suite: no Tk, no models.
"""

import unittest


def _take(user_text, reference_text, score=100.0, passed=True):
    return {
        "user_text": user_text,
        "reference_text": reference_text,
        "score": score,
        "passed": passed,
    }


def _session(sid, takes, first_score, last_score, outcome):
    return {
        "id": sid,
        "first_score": first_score,
        "last_score": last_score,
        "outcome": outcome,
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


if __name__ == "__main__":
    unittest.main()
