# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Drill-mode logic: progress tracking, mastery detection, phrase selection.

Pure Python, no Tkinter, no engine imports. The controller (app.py)
owns one ``DrillEngine`` instance per active drill session, feeds it
each scored take, and reads its progress from it. Everything the UI
needs - current phrase, progress bar, mini-chart data, outcome on
finish - comes from this class.

Design notes:

* A "word drill" drills one target word; the engine picks phrases
  from the user's source text that contain that word (case-insensitive)
  and cycles through them. If no phrase contains the target, the
  engine falls back to a small list of synthetic phrases.
* A "phoneme drill" drills one IPA symbol; the engine uses
  :func:`analysis.ipa_map.word_to_ipa` to find target words and then
  behaves the same as a word drill.
* "Mastered" is three consecutive takes at or above ``pass_threshold``
  (default 80). Anything else (a score drop, an interruption) resets
  the streak.
* The engine keeps the last N takes in memory for the mini-chart and
  reports the first/last/average score on finish.

The engine is deliberately independent of PronunciationResult: it only
needs a numeric score (0-100) and the phrase it was scored on. That
keeps it testable without a whisper load.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mimora import config


# ---------------------------------------------------------------------------
# Synthetic phrase pool used when the user's source text has no hits.
# These are short, common English sentences so the drill is always
# doable even on a fresh install.
# ---------------------------------------------------------------------------
_SYNTHETIC_PHRASES = [
    "The thought is a thought.",
    "Think about what you think.",
    "Three thorough thoughts about the weather.",
    "The ship ships shoes from Shanghai.",
    "Should we ship the shoes soon?",
    "This is the third shirt.",
    "The child changed their chairs.",
    "A quick brown fox jumps over the lazy dog.",
    "She sells seashells by the seashore.",
    "The rain in Spain falls mainly on the plain.",
    "Peter Piper picked a peck of pickled peppers.",
]


@dataclass
class DrillTake:
    """One scored take inside a drill session."""
    phrase: str
    score: float
    attempt: int  # 1-indexed position in the drill
    passed: bool  # score >= pass_threshold
    # Optional recording references, attached by the controller after the
    # take is scored. Empty when the take has no saved audio (HISTORY_MODE
    # off, or the save failed). They let the drill session record be
    # replayed from the history browser, just like a normal take.
    audio_session_id: str = ""
    audio_take_id: str = ""
    audio_files: Optional[Dict[str, str]] = None


@dataclass
class DrillOutcome:
    """Summary of a finished drill session."""
    target_type: str
    target_value: str
    total_attempts: int
    total_passed: int
    first_score: float
    last_score: float
    avg_score: float
    mastered: bool
    takes: List[DrillTake]


@dataclass
class DrillEngine:
    """State machine for one drill session.

    Instantiate with a target like ``{"type": "word", "value": "thought"}``
    or ``{"type": "phoneme", "value": "θ"}``. The controller then calls
    ``next_phrase`` for each new take, ``record_take`` after scoring,
    and ``finish`` when the user ends the drill.

    ``source_text`` is the plain-text corpus the phrases are drawn from;
    if it is empty or lacks the target, the engine falls back to the
    synthetic pool above.
    """
    target: Dict[str, str]
    source_text: str = ""
    pass_threshold: float = field(default_factory=lambda: config.DRILL_PASS_THRESHOLD)
    max_attempts: int = 10
    # How many consecutive passes are needed to declare "mastered".
    mastered_streak_required: int = 3

    # Populated by the controller.
    phrases: List[str] = field(default_factory=list)
    takes: List[DrillTake] = field(default_factory=list)
    _phrase_index: int = field(default=0, repr=False)
    _phrase_history: List[str] = field(default_factory=list, repr=False)
    # Why the candidate list is not built from the target's own sentences.
    # Empty when the drill found real matches. Set by _find_phrases so the
    # UI can explain a synthetic fallback instead of silently showing
    # unrelated sentences (the exact "first line matches, rest random"
    # confusion). Values: "", "no_target", "empty_source",
    # "word_not_in_source", "phoneme_not_in_source".
    fallback_reason: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.pass_threshold <= 0 or self.pass_threshold > 100:
            self.pass_threshold = 80.0
        if self.max_attempts < 1:
            self.max_attempts = 10
        if self.mastered_streak_required < 1:
            self.mastered_streak_required = 3
        self._candidate_phrases = self._find_phrases()

    # ------------------------------------------------------------------
    # Phrase selection
    # ------------------------------------------------------------------

    def fallback_message(self, tr=None) -> str:
        """Human-readable explanation when the drill fell back to synthetic.

        The engine silently substitutes the synthetic pool when the source
        text has no sentence containing the target. Without this message the
        UI shows unrelated sentences that look random - the exact confusion
        the user reported ("first line matches, rest random"). The message
        names the reason so the UI can tell the user how to fix it.

        ``tr`` is an optional translator callable (e.g. ``i18n.tr``); when
        omitted the English templates are returned verbatim. The engine stays
        GUI- and i18n-free; the caller localizes at display time.
        """
        tr = tr or (lambda key, **kw: key)
        reason = self.fallback_reason
        if not reason:
            return ""
        value = self.target.get("value") or ""
        if reason == "no_target":
            return tr("No target value was given, so the drill uses sample phrases.")
        if reason == "empty_source":
            return tr("Your practice text is empty, so the drill uses sample "
                      "phrases. Add the target word to your practice text, or "
                      "load a text that contains it.")
        if reason == "word_not_in_source":
            return tr("The word \"{value}\" is not in your practice text, so "
                      "the drill uses sample phrases. Add it to the practice "
                      "text, or pick a word that appears there.", value=value)
        if reason == "phoneme_not_in_source":
            return tr("No word in your practice text contains the sound /{value}/, "
                      "so the drill uses sample phrases. Add a word with this "
                      "sound to your practice text.", value=value)
        return tr("The drill uses sample phrases because the target was not found in your practice text.")

    def _find_phrases(self) -> List[str]:
        """Return the phrases this drill will cycle through."""
        target_type = (self.target.get("type") or "").lower()
        value = self.target.get("value") or ""
        if not value:
            self.fallback_reason = "no_target"
            return list(_SYNTHETIC_PHRASES)

        # free_form short-circuits: the value IS the phrase. single_word is the
        # same shape for the word drilled in isolation (double-click a phrase
        # word): the value IS the whole practice phrase.
        if target_type in ("free_form", "single_word"):
            return [value]

        source = (self.source_text or "").strip()
        phrases: List[str] = []
        if source:
            if target_type == "word":
                pattern = re.compile(rf"\b{re.escape(value)}\b",
                                     re.IGNORECASE)
                for sentence in re.split(r"[.!?]+\s+", source):
                    sentence = sentence.strip()
                    if not sentence or sentence.lower() == value.lower():
                        continue
                    if pattern.search(sentence):
                        phrases.append(sentence)
                if not phrases:
                    self.fallback_reason = "word_not_in_source"
            elif target_type == "phoneme":
                # Find words whose IPA contains the target symbol. Batch the
                # phonemization: one espeak call per sentence instead of one
                # per word (ipa_from_words phonemizes the whole phrase at once).
                from mimora.analysis.ipa_map import ipa_from_words
                for sentence in re.split(r"[.!?]+\s+", source):
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    if value.lower() in ipa_from_words(sentence).lower():
                        phrases.append(sentence)
                if not phrases:
                    self.fallback_reason = "phoneme_not_in_source"
        else:
            self.fallback_reason = "empty_source"

        # Fall back to the synthetic pool when the source had no hits.
        if not phrases:
            phrases = list(_SYNTHETIC_PHRASES)
        # Deduplicate while preserving order.
        seen = set()
        unique: List[str] = []
        for p in phrases:
            key = p.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        return unique or list(_SYNTHETIC_PHRASES)

    @property
    def candidate_phrases(self) -> List[str]:
        return list(self._candidate_phrases)

    # ------------------------------------------------------------------
    # Phrase cycling
    # ------------------------------------------------------------------

    def next_phrase(self) -> str:
        """Return the phrase for the next take.

        Cycles through the candidate list, reshuffling when we reach
        the end, so a 10-attempt drill on 3 phrases doesn't drill the
        same one 4 times in a row.
        """
        if not self._candidate_phrases:
            phrase = "The thought is a thought."
        else:
            phrase = self._candidate_phrases[self._phrase_index % len(
                self._candidate_phrases)]
            # Advance and reshuffle when we've cycled through.
            if self._phrase_index >= len(self._candidate_phrases):
                random.shuffle(self._candidate_phrases)
                self._phrase_index = 0
            else:
                self._phrase_index += 1
        self._phrase_history.append(phrase)
        return phrase

    def previous_phrase(self) -> str:
        """Return the previous phrase, popping from history.

        Rewinds the phrase index so next_phrase() continues from where
        we left off. Returns the current phrase if history is empty.
        """
        if len(self._phrase_history) > 1:
            self._phrase_history.pop()
            phrase = self._phrase_history[-1]
            self._phrase_index = max(0, self._phrase_index - 1)
            return phrase
        return self._phrase_history[-1] if self._phrase_history else "The thought is a thought."

    # ------------------------------------------------------------------
    # Take recording
    # ------------------------------------------------------------------

    def record_take(self, phrase: str, score: float) -> DrillTake:
        """Record one scored take and return the record with pass/fail."""
        if self.takes:
            attempt = self.takes[-1].attempt + 1
        else:
            attempt = 1
        take = DrillTake(
            phrase=phrase,
            score=float(score),
            attempt=attempt,
            passed=float(score) >= self.pass_threshold,
        )
        self.takes.append(take)
        return take

    def mastered_streak(self) -> int:
        """Length of the current consecutive-pass streak."""
        streak = 0
        for take in reversed(self.takes):
            if take.passed:
                streak += 1
            else:
                break
        return streak

    @property
    def mastered(self) -> bool:
        return self.mastered_streak() >= self.mastered_streak_required

    @property
    def done(self) -> bool:
        return self.mastered or len(self.takes) >= self.max_attempts

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def progress(self) -> Dict[str, Any]:
        """A snapshot for the UI to render progress.

        Includes the mini-chart data (last N scores) so the UI can
        draw without re-reading the takes list.
        """
        return {
            "total": self.max_attempts,
            "current": len(self.takes),
            "passed": sum(1 for t in self.takes if t.passed),
            "streak": self.mastered_streak(),
            "mastered": self.mastered,
            "done": self.done,
            "scores": [t.score for t in self.takes[-10:]],
            "threshold": self.pass_threshold,
        }

    def finish(self) -> DrillOutcome:
        """Finalize the drill and return a summary."""
        if not self.takes:
            return DrillOutcome(
                target_type=self.target.get("type", ""),
                target_value=self.target.get("value", ""),
                total_attempts=0, total_passed=0,
                first_score=0.0, last_score=0.0, avg_score=0.0,
                mastered=False, takes=[],
            )
        scores = [t.score for t in self.takes]
        return DrillOutcome(
            target_type=self.target.get("type", ""),
            target_value=self.target.get("value", ""),
            total_attempts=len(self.takes),
            total_passed=sum(1 for t in self.takes if t.passed),
            first_score=self.takes[0].score,
            last_score=self.takes[-1].score,
            avg_score=round(sum(scores) / len(scores), 2),
            mastered=self.mastered,
            takes=list(self.takes),
        )

    def to_storage_dict(self) -> Dict[str, Any]:
        """Serialize the drill session for ``StatsStore.record_drill_session``.

        The shape matches what ``StatsStore.record_drill_session``
        expects (see the method docstring there).
        """
        outcome = self.finish()
        return {
            "target": dict(self.target),
            "attempts": outcome.total_attempts,
            "passed": outcome.total_passed,
            "first_score": outcome.first_score,
            "last_score": outcome.last_score,
            "avg_score": outcome.avg_score,
            "outcome": "mastered" if outcome.mastered else "unfinished",
            "takes": [
                {
                    "phrase": t.phrase,
                    "score": t.score,
                    "attempt": t.attempt,
                    "passed": t.passed,
                    "audio_session_id": t.audio_session_id,
                    "audio_take_id": t.audio_take_id,
                    "audio_files": dict(t.audio_files) if t.audio_files else None,
                }
                for t in self.takes
            ],
        }
