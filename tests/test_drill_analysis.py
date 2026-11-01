"""Tests for mimora.drill_analysis — the pure, Tk-free drill aggregation.

These functions power the Drill tab of the stats dashboard. They are pure
data transforms over the stored ``drill_sessions`` shape (from
``DrillEngine.to_storage_dict``), so they are tested without a Tk root.
"""

import unittest

from mimora import drill_analysis as da


def _take(score, attempt=1, passed=False, audio=True):
    """A minimal per-take dict in the stored shape."""
    t = {
        "phrase": "the quick brown fox",
        "score": float(score),
        "attempt": attempt,
        "passed": bool(passed),
        "audio_session_id": "as-1",
        "audio_take_id": "at-1",
        "audio_files": ({"normalized.wav": "x.wav"} if audio else None),
    }
    return t


def _session(sid, ttype, value, scores, outcome="unfinished",
             started="2026-10-26T09:00:00", ended=None,
             first=None, last=None, audio=True):
    """A minimal drill session dict in the stored shape.

    ``scores`` becomes the takes list (one per score, attempt 1-indexed).
    ``first``/``last`` default to the first/last score unless given.
    """
    takes = [_take(s, attempt=i + 1, passed=(i >= 1), audio=audio)
             for i, s in enumerate(scores)]
    first = (scores[0] if scores else 0) if first is None else first
    last = (scores[-1] if scores else 0) if last is None else last
    return {
        "id": sid,
        "target": {"type": ttype, "value": value},
        "attempts": len(scores),
        "passed": 1 if outcome == "mastered" else 0,
        "first_score": float(first),
        "last_score": float(last),
        "avg_score": float(sum(scores) / len(scores)) if scores else 0.0,
        "outcome": outcome,
        "started_at": started,
        "ended_at": ended or started,
        "takes": takes,
    }


class MasteryByTypeTest(unittest.TestCase):
    def test_mastery_by_type_splits_outcome(self):
        sessions = [
            _session("d-1", "phoneme", "r", [60, 70], "unfinished"),
            _session("d-2", "phoneme", "r", [60, 70], "mastered"),
            _session("d-3", "single_word", "hello", [60, 70], "mastered"),
        ]
        result = da.mastery_by_type(sessions)
        # Only types that appear are returned, in TYPE_ORDER.
        self.assertEqual(result, [
            ("phoneme", {"mastered": 1, "unfinished": 1}),
            ("single_word", {"mastered": 1, "unfinished": 0}),
        ])

    def test_empty_input(self):
        self.assertEqual(da.mastery_by_type([]), [])


class MasteryRateByTypeTest(unittest.TestCase):
    def test_rates(self):
        sessions = [
            _session("d-1", "single_word", "a", [1], "mastered"),
            _session("d-2", "single_word", "b", [1], "unfinished"),
        ]
        result = da.mastery_rate_by_type(sessions)
        self.assertEqual(result, [("single_word", 0.5)])

    def test_no_sessions_of_type(self):
        self.assertEqual(da.mastery_rate_by_type([]), [])


class ProgressPairsTest(unittest.TestCase):
    def test_pairs_for_sessions_with_takes(self):
        sessions = [
            _session("d-1", "word", "quick", [30, 80], first=30, last=80),
            _session("d-2", "phoneme", "r", [], first=0, last=0),  # zero takes
        ]
        result = da.progress_pairs(sessions)
        self.assertEqual(len(result), 1)
        sid, ttype, first, last, attempts = result[0]
        self.assertEqual((sid, ttype, first, last, attempts),
                         ("d-1", "word", 30.0, 80.0, 2))

    def test_empty(self):
        self.assertEqual(da.progress_pairs([]), [])


class ProgressByTypeTest(unittest.TestCase):
    def test_groups_by_type(self):
        sessions = [
            _session("d-1", "word", "quick", [30, 80]),
            _session("d-2", "phoneme", "r", [50, 60]),
        ]
        result = da.progress_by_type(sessions)
        # Ordered by TYPE_ORDER: phoneme comes before word.
        types = [t for t, _ in result]
        self.assertEqual(types, ["phoneme", "word"])

    def test_empty(self):
        self.assertEqual(da.progress_by_type([]), [])


class TargetProgressSeriesTest(unittest.TestCase):
    def test_merges_word_and_single_word_by_value(self):
        # The same word as a context drill (word) and as a single-phrase drill
        # (single_word) is the same semantic target.
        sessions = [
            _session("d-1", "word", "quick", [50], ended="2026-10-26T09:00:00"),
            _session("d-2", "single_word", "quick", [80],
                     ended="2026-10-27T09:00:00"),
        ]
        result = da.target_progress_series(sessions, min_sessions=2)
        self.assertEqual(len(result), 1)
        ttype, value, series = result[0]
        self.assertEqual((ttype, value), ("word", "quick"))
        self.assertEqual([s[1] for s in series], [50.0, 80.0])

    def test_excludes_free_form(self):
        sessions = [
            _session("d-1", "free_form", "a phrase", [50]),
            _session("d-2", "free_form", "a phrase", [60]),
        ]
        self.assertEqual(da.target_progress_series(sessions), [])

    def test_min_sessions_filters(self):
        sessions = [
            _session("d-1", "phoneme", "r", [50], ended="2026-10-26T09:00:00"),
        ]
        self.assertEqual(da.target_progress_series(sessions, min_sessions=2),
                         [])

    def test_empty(self):
        self.assertEqual(da.target_progress_series([]), [])


class PhonemeAveragesTest(unittest.TestCase):
    def test_weakest_first(self):
        sessions = [
            _session("d-1", "phoneme", "r", [20], last=20),
            _session("d-2", "phoneme", "ŋ", [90], last=90),
        ]
        result = da.phoneme_averages(sessions)
        # /r/ (20) is weaker than /ŋ/ (90), so it comes first.
        self.assertEqual([(ipa, avg) for ipa, avg, _ in result],
                         [("r", 20.0), ("ŋ", 90.0)])

    def test_averages_multiple_sessions(self):
        sessions = [
            _session("d-1", "phoneme", "r", [30], last=30),
            _session("d-2", "phoneme", "r", [60], last=60),
        ]
        result = da.phoneme_averages(sessions)
        self.assertEqual(result, [("r", 45.0, 2)])

    def test_empty(self):
        self.assertEqual(da.phoneme_averages([]), [])


class AttemptsHistogramByTypeTest(unittest.TestCase):
    def test_histogram(self):
        sessions = [
            _session("d-1", "phoneme", "r", [1, 2, 3]),
            _session("d-2", "phoneme", "r", [1]),
            _session("d-3", "single_word", "a", [1, 2]),
        ]
        result = dict(da.attempts_histogram_by_type(sessions))
        self.assertEqual(result["phoneme"], [(1, 1), (3, 1)])
        self.assertEqual(result["single_word"], [(2, 1)])

    def test_empty(self):
        self.assertEqual(da.attempts_histogram_by_type([]), [])


class PassRateByAttemptByTypeTest(unittest.TestCase):
    def test_rates(self):
        sessions = [
            _session("d-1", "single_word", "a", [50, 80, 90]),
            _session("d-2", "single_word", "b", [50, 80]),
        ]
        result = dict(da.pass_rate_by_attempt_by_type(sessions))
        # attempt 1: neither passed (scores < 70) -> 0.0
        # attempt 2: both passed -> 1.0
        # attempt 3: only session 1 has one, passed -> 1.0
        self.assertEqual(result["single_word"], [(1, 0.0), (2, 1.0), (3, 1.0)])

    def test_empty(self):
        self.assertEqual(da.pass_rate_by_attempt_by_type([]), [])


class InvestmentByTypeTest(unittest.TestCase):
    def test_order_by_attempts_desc(self):
        sessions = [
            _session("d-1", "phoneme", "r", [1] * 3),
            _session("d-2", "single_word", "a", [1] * 10),
        ]
        result = da.investment_by_type(sessions)
        self.assertEqual(result, [("single_word", 1, 10), ("phoneme", 1, 3)])

    def test_empty(self):
        self.assertEqual(da.investment_by_type([]), [])


class InvestmentScatterTest(unittest.TestCase):
    def test_points(self):
        sessions = [
            _session("d-1", "single_word", "a", [1] * 5, last=40),
            _session("d-2", "phoneme", "r", [1], last=90),
        ]
        result = da.investment_scatter(sessions)
        # sorted by attempts desc: 'a' (5) before /r/ (1)
        self.assertEqual(result[0][1:], ("a", 5, 40.0))
        self.assertEqual(result[1][1:], ("r", 1, 90.0))

    def test_last_score_is_most_recent(self):
        # The session helper sets last_score from `last`, so a word drilled
        # twice with a lower final score reports that final score.
        sessions = [
            _session("d-1", "single_word", "a", [80, 40], last=40),
        ]
        result = da.investment_scatter(sessions)
        self.assertEqual(result[0][1:], ("a", 2, 40.0))

    def test_free_form_excluded(self):
        sessions = [
            _session("d-1", "free_form", "phrase", [1] * 3, last=10),
        ]
        self.assertEqual(da.investment_scatter(sessions), [])

    def test_limit(self):
        sessions = [
            _session(f"d-{i}", "single_word", f"w{i}", [1], last=10)
            for i in range(5)
        ]
        self.assertEqual(len(da.investment_scatter(sessions, limit=2)), 2)

    def test_empty(self):
        self.assertEqual(da.investment_scatter([]), [])


class SessionTrajectoriesTest(unittest.TestCase):
    def test_trajectories(self):
        sessions = [
            _session("d-1", "single_word", "a", [40, 60, 80], "mastered"),
            _session("d-2", "single_word", "b", [10], "unfinished"),
        ]
        result = da.session_trajectories(sessions, min_attempts=2)
        self.assertEqual(len(result), 1)
        sid, ttype, outcome, scores = result[0]
        self.assertEqual((sid, ttype, outcome), ("d-1", "single_word",
                                                 "mastered"))
        self.assertEqual(scores, [40.0, 60.0, 80.0])

    def test_empty(self):
        self.assertEqual(da.session_trajectories([]), [])


class AudioCoverageTest(unittest.TestCase):
    def test_coverage(self):
        sessions = [
            _session("d-1", "word", "quick", [50, 60], audio=True),
            _session("d-2", "word", "brown", [50, 60], audio=False),
        ]
        self.assertEqual(da.audio_coverage(sessions), (2, 4))

    def test_empty(self):
        self.assertEqual(da.audio_coverage([]), (0, 0))


class TargetByWeekHeatmapTest(unittest.TestCase):
    def test_matrix(self):
        sessions = [
            _session("d-1", "phoneme", "r", [50], last=50,
                     ended="2026-10-26T09:00:00"),
            _session("d-2", "phoneme", "r", [70], last=70,
                     ended="2026-10-26T11:00:00"),
            _session("d-3", "single_word", "quick", [80], last=80,
                     ended="2026-11-02T09:00:00"),
        ]
        targets, weeks, matrix = da.target_by_week_heatmap(sessions, top_n=5)
        self.assertEqual(targets, ["/r/", "quick"])  # /r/ trained twice
        self.assertEqual(weeks, ["2026-10-26", "2026-11-02"])
        # /r/ averages 60 in week 1, 0 in week 2; quick 0 / 80.
        self.assertEqual(matrix, [[60.0, 0.0], [0.0, 80.0]])

    def test_empty(self):
        self.assertEqual(da.target_by_week_heatmap([]),
                         ([], [], []))


class StubbornTargetsTest(unittest.TestCase):
    def test_counts_targets(self):
        sessions = [
            # 10 attempts, last 60 -> stubborn
            _session("d-1", "single_word", "a", [60] * 10, last=60),
            # 10 attempts, last 80 -> not stubborn (passed threshold)
            _session("d-2", "single_word", "b", [80] * 10, last=80),
            # 9 attempts -> not stubborn (below min)
            _session("d-3", "single_word", "c", [60] * 9, last=60),
            # merged by value: 4 + 8 = 12 attempts, last 50 -> stubborn
            _session("d-4", "word", "quick", [50] * 4, last=50),
            _session("d-5", "single_word", "quick", [50] * 8, last=50),
        ]
        self.assertEqual(da.stubborn_targets(sessions), 2)

    def test_free_form_excluded(self):
        sessions = [
            _session("d-1", "free_form", "phrase", [50] * 10, last=50),
        ]
        self.assertEqual(da.stubborn_targets(sessions), 0)

    def test_empty(self):
        self.assertEqual(da.stubborn_targets([]), 0)


class WeeklyVolumeByTypeTest(unittest.TestCase):
    def test_volume(self):
        sessions = [
            _session("d-1", "phoneme", "r", [1, 2], ended="2026-10-26T09:00:00"),
            _session("d-2", "single_word", "a", [1], ended="2026-11-02T09:00:00"),
        ]
        result = da.weekly_volume_by_type(sessions)
        self.assertEqual(result, [
            ("2026-10-26", {"phoneme": 2}),
            ("2026-11-02", {"single_word": 1}),
        ])

    def test_empty(self):
        self.assertEqual(da.weekly_volume_by_type([]), [])


class TypeOrderTest(unittest.TestCase):
    def test_type_order(self):
        self.assertEqual(da.TYPE_ORDER,
                         ("phoneme", "word", "single_word", "free_form"))


if __name__ == "__main__":
    unittest.main()
