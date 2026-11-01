"""Tests for mimora.advance_analysis — the cross-file "advance" aggregation.

These functions power the Advance tab of the stats dashboard. They join the
practice sessions, drill sessions, dictation sessions and the review pool, so
they are pure data transforms over the stored shapes and are tested without a
Tk root (mirroring tests/test_drill_analysis.py).
"""

import unittest

from mimora import advance_analysis as aa


def _practice_session(sid, started="2026-10-26T09:00:00", takes=None):
    """A minimal practice session dict in the stored shape."""
    return {
        "id": sid,
        "started_at": started,
        "ended_at": started,
        "duration_sec": 60,
        "takes": takes or [],
    }


def _take(tid, phrase="the quick brown fox", score=80.0, error_words=None,
          phoneme_scores=None, audio_files=None, session_id="s-1"):
    """A minimal practice take dict in the stored shape."""
    return {
        "id": tid,
        "session_id": session_id,
        "phrase": phrase,
        "score": float(score),
        "error_words": error_words or [],
        "phoneme_scores": phoneme_scores or [],
        "audio_files": audio_files,
    }


def _drill_take(phrase="the quick brown fox", score=80.0, attempt=1,
                passed=True, audio_session_id="s-1", audio_take_id="t-1",
                audio_files=None):
    """A minimal drill take dict in the stored shape."""
    return {
        "phrase": phrase,
        "score": float(score),
        "attempt": attempt,
        "passed": bool(passed),
        "audio_session_id": audio_session_id,
        "audio_take_id": audio_take_id,
        "audio_files": audio_files,
    }


def _drill_session(sid, ttype="word", value="fox", outcome="unfinished",
                   started="2026-10-26T10:00:00", takes=None):
    """A minimal drill session dict in the stored shape."""
    return {
        "id": sid,
        "target": {"type": ttype, "value": value},
        "started_at": started,
        "ended_at": started,
        "outcome": outcome,
        "takes": takes or [],
    }


def _dictation_session(sid, started="2026-10-27T10:00:00", takes=None):
    """A minimal dictation session dict in the stored shape."""
    return {
        "id": sid,
        "started_at": started,
        "ended_at": started,
        "takes": takes or [],
    }


def _review(entries):
    """Build a review.json ``phrases`` dict from {phrase: state} pairs."""
    return {phrase: dict(state) for phrase, state in entries.items()}


# ---------------------------------------------------------------------------
# Section 8 - normalisation
# ---------------------------------------------------------------------------

class NormalizeTest(unittest.TestCase):
    def test_normalize_phrase_lowercases_and_collapses(self):
        self.assertEqual(aa.normalize_phrase("  The QUICK   Brown  "), "the quick brown")

    def test_normalize_phrase_strips_punct_keeps_hyphens_apostrophes(self):
        self.assertEqual(aa.normalize_phrase("Hello, world!"), "hello world")
        self.assertEqual(aa.normalize_phrase("mother-in-law's"), "mother-in-law's")

    def test_normalize_phrase_none_and_empty(self):
        self.assertEqual(aa.normalize_phrase(None), "")
        self.assertEqual(aa.normalize_phrase(""), "")

    def test_normalize_word_strips_trailing_apostrophe(self):
        self.assertEqual(aa.normalize_word("children's"), "children")
        self.assertEqual(aa.normalize_word("children\u2019s"), "children")
        self.assertEqual(aa.normalize_word("'em"), "'em")
        self.assertEqual(aa.normalize_word("fox"), "fox")

    def test_normalize_ipa_strips_slashes(self):
        self.assertEqual(aa.normalize_ipa("/r/"), "r")
        self.assertEqual(aa.normalize_ipa(" r "), "r")
        self.assertEqual(aa.normalize_ipa(None), "")


# ---------------------------------------------------------------------------
# Section 0 - key overview
# ---------------------------------------------------------------------------

class KpiOverviewTest(unittest.TestCase):
    def test_counts(self):
        takes = [
            _take("t-1", error_words=[{"word": "fox"}]),
            _take("t-2", error_words=[{"word": "fox"}, {"word": "dog"}],
                  audio_files={"normalized.wav": "x.wav"}),
            _take("t-3", audio_files={"normalized.wav": "x.wav"}),
        ]
        drill = [_drill_session("d-1", outcome="mastered"),
                 _drill_session("d-2", outcome="unfinished")]
        dictation = [_dictation_session("dt-1")]
        review = _review({"the quick brown fox": {"passed": True}})

        kpi = aa.kpi_overview(takes, drill, dictation, review)
        self.assertEqual(kpi["practice_takes"], 3)
        self.assertEqual(kpi["error_words"], 2)  # distinct: fox, dog
        self.assertEqual(kpi["review_pool"], 1)
        self.assertEqual(kpi["drill_sessions"], 2)
        self.assertEqual(kpi["dictation_sessions"], 1)
        self.assertAlmostEqual(kpi["audio_coverage"], 2 / 3)
        self.assertEqual(kpi["mastered_drills"], 1)

    def test_audio_coverage_none_when_no_takes(self):
        kpi = aa.kpi_overview([], [], [], {})
        self.assertIsNone(kpi["audio_coverage"])
        self.assertEqual(kpi["practice_takes"], 0)

    def test_distinct_error_words_dedupe(self):
        takes = [
            _take("t-1", error_words=[{"word": "Fox"}]),
            _take("t-2", error_words=[{"word": "fox"}, {"word": "FOX"}]),
        ]
        self.assertEqual(aa.kpi_overview(takes, [], [], {})["error_words"], 1)


# ---------------------------------------------------------------------------
# Section 1 - error-word flow
# ---------------------------------------------------------------------------

class WordFlowTest(unittest.TestCase):
    def test_flow_counts(self):
        takes = [
            _take("t-1", error_words=[{"word": "fox"}, {"word": "dog"}]),
            _take("t-2", error_words=[{"word": "cat"}]),
        ]
        drill = [
            # fox drilled + mastered
            _drill_session("d-1", ttype="single_word", value="fox", outcome="mastered"),
            # dog drilled but not mastered
            _drill_session("d-2", ttype="word", value="dog", outcome="unfinished"),
            # horse drilled but never an error word -> not counted
            _drill_session("d-3", ttype="single_word", value="horse", outcome="mastered"),
        ]
        result = aa.word_flow(takes, drill)
        self.assertEqual(result["error_words"], 3)  # fox, dog, cat
        self.assertEqual(result["drilled"], 2)      # fox, dog
        self.assertEqual(result["mastered"], 1)     # fox
        self.assertEqual(result["not_drilled"], 1)  # cat
        self.assertEqual(result["not_mastered"], 1) # dog

    def test_flow_nodes_shape(self):
        counts = {"error_words": 10, "drilled": 4, "mastered": 2,
                  "not_drilled": 6, "not_mastered": 2}
        nodes, links = aa.word_flow_nodes(counts)
        # 5 nodes: err(0), drilled/not_drilled(1), mastered/not_mastered(2)
        self.assertEqual([n[0] for n in nodes],
                         ["err", "drilled", "not_drilled", "mastered", "not_mastered"])
        self.assertEqual([n[3] for n in nodes], [0, 1, 1, 2, 2])
        self.assertEqual([n[2] for n in nodes], [10, 4, 6, 2, 2])
        self.assertEqual(links, [("err", "drilled", 4), ("err", "not_drilled", 6),
                                 ("drilled", "mastered", 2),
                                 ("drilled", "not_mastered", 2)])

    def test_empty(self):
        result = aa.word_flow([], [])
        self.assertEqual(result, {"error_words": 0, "drilled": 0, "mastered": 0,
                                  "not_drilled": 0, "not_mastered": 0})


# ---------------------------------------------------------------------------
# Section 3 replacement - drill take origin
# ---------------------------------------------------------------------------

class DrillTakeOriginTest(unittest.TestCase):
    def test_linked_and_unlinked(self):
        takes = [
            _take("t-1", session_id="s-1"),
            _take("t-2", session_id="s-1"),
        ]
        drill = [
            _drill_session("d-1", takes=[
                _drill_take(audio_session_id="s-1", audio_take_id="t-1"),
                _drill_take(audio_session_id="s-1", audio_take_id="t-2"),
                # dangling ref (no resolving practice take)
                _drill_take(audio_session_id="s-9", audio_take_id="t-9"),
            ]),
            _drill_session("d-2", takes=[
                _drill_take(audio_session_id=None, audio_take_id=None),
            ]),
        ]
        result = aa.drill_take_origin(drill, takes)
        self.assertEqual(result, {"referenced": 2, "unreferenced": 2})

    def test_empty(self):
        self.assertEqual(aa.drill_take_origin([], []), {"referenced": 0, "unreferenced": 0})


# ---------------------------------------------------------------------------
# Section 2 - phoneme weakness
# ---------------------------------------------------------------------------

class PhonemeWeaknessTest(unittest.TestCase):
    def test_count_weighted_average_and_threshold(self):
        takes = [
            # /r/ weighted avg: (0.3*2 + 0.9*1)/3 = 0.5 -> exactly threshold, NOT weak
            _take("t-1", phoneme_scores=[{"ipa": "r", "score": 0.3, "count": 2},
                                         {"ipa": "r", "score": 0.9, "count": 1},
                                         {"ipa": "s", "score": 0.2, "count": 1}]),
            _take("t-2", phoneme_scores=[{"ipa": "s", "score": 0.6, "count": 1}]),
        ]
        drill = [
            _drill_session("d-1", ttype="phoneme", value="/s/", outcome="mastered"),
        ]
        result = aa.phoneme_weakness(takes, drill, weak_threshold=0.5)
        self.assertEqual(result["threshold"], 0.5)
        # only "s" (avg 0.4) is weak; "r" is exactly 0.5 and not below.
        self.assertEqual(result["weak_count"], 1)
        self.assertEqual(result["weak"], [("s", 0.4, True, True)])
        self.assertEqual(result["drilled_count"], 1)
        self.assertEqual(result["mastered_count"], 1)
        # /r/ was drilled? no -> not in no_practice (it has practice data)
        self.assertEqual(result["drill_ipa_no_practice"], [])

    def test_drill_ipa_no_practice(self):
        takes = [_take("t-1", phoneme_scores=[{"ipa": "r", "score": 0.3, "count": 1}])]
        drill = [_drill_session("d-1", ttype="phoneme", value="/k/", outcome="mastered")]
        result = aa.phoneme_weakness(takes, drill)
        self.assertEqual(result["drill_ipa_no_practice"], ["k"])

    def test_sorted_weakest_first(self):
        takes = [
            _take("t-1", phoneme_scores=[{"ipa": "a", "score": 0.2, "count": 1},
                                         {"ipa": "b", "score": 0.1, "count": 1}]),
        ]
        result = aa.phoneme_weakness(takes, [])
        self.assertEqual([ipa for ipa, _, _, _ in result["weak"]], ["b", "a"])

    def test_empty(self):
        result = aa.phoneme_weakness([], [])
        self.assertEqual(result["weak_count"], 0)
        self.assertEqual(result["weak"], [])


# ---------------------------------------------------------------------------
# Section 4 - activity timeline
# ---------------------------------------------------------------------------

class ActivityTimelineTest(unittest.TestCase):
    def test_daily_counts_by_kind(self):
        sessions = [
            _practice_session("s-1", started="2026-10-26T09:00:00"),
            _practice_session("s-2", started="2026-10-26T14:00:00"),
            _practice_session("s-3", started="2026-10-27T09:00:00"),
        ]
        drill = [
            _drill_session("d-1", started="2026-10-26T10:00:00"),
            _drill_session("d-2", started="2026-10-28T10:00:00"),
        ]
        dictation = [
            _dictation_session("dt-1", started="2026-10-26T11:00:00"),
        ]
        result = aa.activity_timeline(sessions, drill, dictation)
        self.assertEqual(result["days"], ["2026-10-26", "2026-10-27", "2026-10-28"])
        self.assertEqual(result["practice"], [2, 1, 0])
        self.assertEqual(result["drill"], [1, 0, 1])
        self.assertEqual(result["dictation"], [1, 0, 0])

    def test_drill_uses_ended_at_not_started(self):
        # drill started_at is the save time; ended_at is the real axis.
        sessions = []
        drill = [_drill_session("d-1", started="2026-10-26T10:00:00",
                                takes=[_drill_take()])]
        # Give it an ended_at on a different day.
        drill[0]["ended_at"] = "2026-10-30T11:00:00"
        result = aa.activity_timeline(sessions, drill, [])
        self.assertEqual(result["days"], ["2026-10-30"])
        self.assertEqual(result["drill"], [1])

    def test_empty(self):
        result = aa.activity_timeline([], [], [])
        self.assertEqual(result["days"], [])


# ---------------------------------------------------------------------------
# Section 5 - audio coverage matrix
# ---------------------------------------------------------------------------

class AudioCoverageTest(unittest.TestCase):
    def test_split_with_without(self):
        takes = [
            _take("t-1", audio_files={"normalized.wav": "x.wav"}),
            _take("t-2"),
        ]
        drill = [
            _drill_session("d-1", takes=[
                _drill_take(audio_files={"normalized.wav": "x.wav"}),
                _drill_take(),
            ]),
        ]
        result = aa.audio_coverage_matrix(takes, drill)
        self.assertEqual(result["practice"], {"with": 1, "without": 1})
        self.assertEqual(result["drill"], {"with": 1, "without": 1})

    def test_empty(self):
        result = aa.audio_coverage_matrix([], [])
        self.assertEqual(result, {"practice": {"with": 0, "without": 0},
                                  "drill": {"with": 0, "without": 0}})


# ---------------------------------------------------------------------------
# Section 6 - phrase funnel
# ---------------------------------------------------------------------------

class PhraseFunnelTest(unittest.TestCase):
    def test_stages_and_links(self):
        takes = [
            _take("t-1", phrase="The quick brown fox", error_words=[{"word": "fox"}]),
            _take("t-2", phrase="Hello, world!"),
            _take("t-3", phrase="A third phrase", error_words=[{"word": "a"}]),
        ]
        drill = [
            _drill_session("d-1", takes=[_drill_take(phrase="the quick brown fox"),
                                         _drill_take(phrase="never practiced phrase")]),
        ]
        review = _review({
            "the quick brown fox": {"passed": True},
            "hello world": {"passed": False},
        })
        result = aa.phrase_funnel(takes, drill, review)
        stages = dict(result["stages"])
        # 3 distinct practice phrases; "Hello, world!" normalises to "hello world"
        self.assertEqual(stages["Practice phrases"], 3)
        # phrases with error_words: fox phrase + a third phrase = 2
        self.assertEqual(stages["With errors"], 2)
        # review pool: 2 entries
        self.assertEqual(stages["In review"], 2)
        # drilled phrases: fox + never practiced = 2
        self.assertEqual(stages["Drilled"], 2)
        # review passed: only fox
        self.assertEqual(stages["Passed"], 1)
        # links: practice->errors, errors->review, review->drill, drill->pass
        self.assertEqual(result["links"], [2, 1, 1, 1])

    def test_punctuation_insensitive_join(self):
        # "Hello, world!" practice must match "hello world" review key.
        takes = [_take("t-1", phrase="Hello, world!")]
        drill = []
        review = _review({"hello world": {"passed": True}})
        result = aa.phrase_funnel(takes, drill, review)
        stages = dict(result["stages"])
        self.assertEqual(stages["In review"], 1)
        self.assertEqual(stages["Passed"], 1)

    def test_empty(self):
        result = aa.phrase_funnel([], [], {})
        self.assertEqual(result["stages"], [
            ("Practice phrases", 0), ("With errors", 0), ("In review", 0),
            ("Drilled", 0), ("Passed", 0)])
        self.assertEqual(result["links"], [0, 0, 0, 0])


# ---------------------------------------------------------------------------
# Section 7 - activity correlation
# ---------------------------------------------------------------------------

class ActivityCorrelationTest(unittest.TestCase):
    def test_pearson_positive(self):
        # Two practice sessions on 10-26, one on 10-27, one on 10-28, and the
        # same drill pattern -> perfectly correlated (non-constant series).
        sessions = [
            _practice_session("s-1", started="2026-10-26T09:00:00"),
            _practice_session("s-2", started="2026-10-26T14:00:00"),
            _practice_session("s-3", started="2026-10-27T09:00:00"),
            _practice_session("s-4", started="2026-10-28T09:00:00"),
        ]
        drill = [
            _drill_session("d-1", started="2026-10-26T10:00:00"),
            _drill_session("d-2", started="2026-10-26T11:00:00"),
            _drill_session("d-3", started="2026-10-27T10:00:00"),
            _drill_session("d-4", started="2026-10-28T10:00:00"),
        ]
        # perfectly correlated with practice
        result = aa.activity_correlation(sessions, drill, [])
        self.assertAlmostEqual(result["practice_drill"], 1.0)
        self.assertEqual(result["days"], 3)
        # dictation empty -> constant zero series -> None
        self.assertIsNone(result["practice_dictation"])
        self.assertIsNone(result["drill_dictation"])

    def test_none_when_zero_variance(self):
        # single day for practice -> zero variance
        sessions = [_practice_session("s-1", started="2026-10-26T09:00:00")]
        drill = [_drill_session("d-1", started="2026-10-26T10:00:00")]
        result = aa.activity_correlation(sessions, drill, [])
        self.assertIsNone(result["practice_drill"])

    def test_empty(self):
        result = aa.activity_correlation([], [], [])
        self.assertEqual(result["days"], 0)
        self.assertIsNone(result["practice_drill"])


class JoinGotchasTest(unittest.TestCase):
    def test_four_notes(self):
        self.assertEqual(len(aa.JOIN_GOTCHAS), 4)
        self.assertTrue(all(isinstance(g, str) and g for g in aa.JOIN_GOTCHAS))


if __name__ == "__main__":
    unittest.main()
