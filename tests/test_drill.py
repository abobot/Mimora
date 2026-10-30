# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for mimora/drill/engine.py.

Covers phrase cycling, mastery detection, and the storage dict shape.
Runs with the fast suite - no Tk, no engine load.
"""

import unittest

from mimora.drill.engine import DrillEngine


class PhraseCyclingTest(unittest.TestCase):

    def test_word_drill_picks_phrases_containing_target(self):
        source = "The thought is deep. She thinks deeply. Run freely."
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             source_text=source)
        phrases = engine.candidate_phrases
        self.assertTrue(phrases)
        self.assertTrue(any("thought" in p.lower() for p in phrases))
        self.assertFalse(any("run freely" in p.lower() for p in phrases))

    def test_word_drill_falls_back_to_synthetic_when_no_matches(self):
        engine = DrillEngine(target={"type": "word", "value": "xylophone"},
                             source_text="the cat sat on the mat")
        phrases = engine.candidate_phrases
        self.assertTrue(phrases)  # synthetic pool

    def test_phoneme_drill_finds_words_with_target_ipa(self):
        # "thought" has IPA /θɔːt/ which contains "θ".
        source = "The thought is deep. The cat sat."
        engine = DrillEngine(target={"type": "phoneme", "value": "θ"},
                             source_text=source)
        phrases = engine.candidate_phrases
        self.assertTrue(any("thought" in p.lower() for p in phrases))

    def test_free_form_drill_returns_value_verbatim(self):
        engine = DrillEngine(target={"type": "free_form",
                                     "value": "Custom phrase here."})
        phrases = engine.candidate_phrases
        self.assertEqual(phrases, ["Custom phrase here."])

    def test_priority_words_move_those_phrases_first(self):
        # The cat sentence contains a priority word, so it leads even though
        # the thought-only sentence comes first in the source. Trailing
        # periods are dropped by the sentence split, so normalize before
        # comparing.
        source = ("The thought is deep. "
                  "The cat thinks about the thought.")
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             source_text=source,
                             priority_words=("cat",))
        phrases = [p.rstrip(".").lower() for p in engine.candidate_phrases]
        self.assertEqual(phrases[0], "the cat thinks about the thought")

    def test_priority_words_stable_within_groups(self):
        # Every sentence contains the target "thought"; the two sentences
        # with the priority word "cat" lead (source order preserved), then
        # the rest follow in source order.
        source = ("The thought is deep. The cat ponders the thought. "
                  "Another thought. The cat knows the thought.")
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             source_text=source,
                             priority_words=("cat",))
        phrases = [p.rstrip(".").lower() for p in engine.candidate_phrases]
        self.assertEqual(phrases[:2], ["the cat ponders the thought",
                                       "the cat knows the thought"])
        self.assertEqual(phrases[2:], ["the thought is deep",
                                       "another thought"])

    def test_no_priority_words_keeps_source_order(self):
        source = "Alpha one. Beta two. Alpha three."
        engine = DrillEngine(target={"type": "word", "value": "Alpha"},
                             source_text=source)
        self.assertEqual([p.rstrip(".") for p in engine.candidate_phrases],
                         ["Alpha one", "Alpha three"])

    def test_priority_word_matches_on_word_boundary_only(self):
        # A single-letter priority word like "i" must match only the standalone
        # word "i", not substrings inside "sits"/"thick". With substring
        # matching the "i" would pull every sentence ahead and the priority
        # ordering silently does nothing; with word-boundary matching only the
        # sentence containing the standalone "i" leads.
        source = ("The cat sits here. "
                  "I am the best. "
                  "The thick fog.")
        engine = DrillEngine(target={"type": "word", "value": "the"},
                             source_text=source,
                             priority_words=("i",))
        phrases = [p.rstrip(".").lower() for p in engine.candidate_phrases]
        # The sentence with the standalone word "i" leads; the two sentences
        # that only contain "i" as a substring follow in source order.
        self.assertEqual(phrases[0], "i am the best")
        self.assertEqual(phrases[1], "the cat sits here")
        self.assertEqual(phrases[2], "the thick fog")

    def test_single_word_drill_returns_word_verbatim(self):
        # A single-word drill (double-click a phrase word) practices the word
        # alone: the value IS the whole practice phrase, no sentence around it.
        engine = DrillEngine(target={"type": "single_word", "value": "thought"},
                             source_text="The thought is deep.")
        phrases = engine.candidate_phrases
        self.assertEqual(phrases, ["thought"])

    def test_next_phrase_cycles_and_reshuffles(self):
        source = "Sentence one. Sentence two. Sentence three."
        engine = DrillEngine(target={"type": "word", "value": "Sentence"},
                             source_text=source,
                             max_attempts=10)
        seen = [engine.next_phrase() for _ in range(6)]
        # Not all the same.
        self.assertGreater(len(set(seen)), 1)


class MasteryTest(unittest.TestCase):

    def test_mastered_requires_three_consecutive_passes(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             source_text="The thought is deep.")
        engine.record_take("a", 70)  # fail
        engine.record_take("b", 90)  # pass
        engine.record_take("c", 90)  # pass
        self.assertFalse(engine.mastered)
        engine.record_take("d", 90)  # pass (third in a row)
        self.assertTrue(engine.mastered)

    def test_fail_resets_streak(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             source_text="The thought is deep.")
        for _ in range(3):
            engine.record_take("a", 90)
        self.assertTrue(engine.mastered)
        engine.record_take("a", 50)  # fail breaks streak
        self.assertEqual(engine.mastered_streak(), 0)

    def test_done_when_max_attempts_reached(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=95.0,
                             max_attempts=3,
                             source_text="The thought is deep.")
        for _ in range(3):
            engine.record_take("a", 50)
        self.assertTrue(engine.done)
        self.assertFalse(engine.mastered)


class ProgressTest(unittest.TestCase):

    def test_progress_shape(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             source_text="The thought is deep.")
        engine.record_take("a", 60)
        engine.record_take("b", 90)
        progress = engine.progress()
        self.assertEqual(progress["current"], 2)
        self.assertEqual(progress["passed"], 1)
        self.assertEqual(len(progress["scores"]), 2)
        self.assertIn("threshold", progress)

    def test_finish_returns_summary(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             source_text="The thought is deep.")
        engine.record_take("a", 70)
        engine.record_take("b", 90)
        engine.record_take("c", 95)
        outcome = engine.finish()
        self.assertEqual(outcome.total_attempts, 3)
        self.assertEqual(outcome.total_passed, 2)
        self.assertEqual(outcome.first_score, 70.0)
        self.assertEqual(outcome.last_score, 95.0)
        self.assertAlmostEqual(outcome.avg_score, 85.0, places=2)
        self.assertFalse(outcome.mastered)  # only 2 in a row

    def test_to_storage_dict_matches_store_shape(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             source_text="The thought is deep.")
        engine.record_take("a", 90)
        data = engine.to_storage_dict()
        self.assertEqual(data["target"]["type"], "word")
        self.assertEqual(data["attempts"], 1)
        self.assertEqual(data["passed"], 1)
        # "outcome" is a literal string - "mastered" or "unfinished".
        self.assertIn(data["outcome"], ("mastered", "unfinished"))
        self.assertEqual(len(data["takes"]), 1)
        self.assertIn("score", data["takes"][0])
        # Recording refs default to empty so a take without audio still
        # serializes (the history browser reads these keys).
        self.assertIn("audio_session_id", data["takes"][0])
        self.assertIn("audio_take_id", data["takes"][0])
        self.assertIn("audio_files", data["takes"][0])
        self.assertIsNone(data["takes"][0]["audio_files"])

    def test_storage_dict_carries_attached_audio_refs(self):
        """A take whose recording was attached keeps its refs in storage."""
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             source_text="The thought is deep.")
        take = engine.record_take("a", 90)
        take.audio_session_id = "live-1"
        take.audio_take_id = "t-1"
        take.audio_files = {"raw": "x/raw.wav", "normalized": "x/norm.wav"}
        take2 = engine.record_take("b", 70)  # no audio attached
        data = engine.to_storage_dict()
        self.assertEqual(data["takes"][0]["audio_session_id"], "live-1")
        self.assertEqual(data["takes"][0]["audio_take_id"], "t-1")
        self.assertEqual(data["takes"][0]["audio_files"]["normalized"],
                         "x/norm.wav")
        self.assertEqual(data["takes"][1]["audio_session_id"], "")
        self.assertIsNone(data["takes"][1]["audio_files"])

    def test_max_attempts_plumbs_into_progress_total(self):
        engine = DrillEngine(target={"type": "word", "value": "thought"},
                             pass_threshold=80.0,
                             max_attempts=7,
                             source_text="The thought is deep.")
        self.assertEqual(engine.progress()["total"], 7)


if __name__ == "__main__":
    unittest.main()
