# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Dictation-mode logic: phrase selection, attempt tracking, session summary.

Pure Python, no Tkinter, no engine imports. The controller (app.py) owns one
``DictationEngine`` instance per active dictation session, feeds it each scored
attempt (the typed text vs the spoken reference), and reads progress from it.
Everything the UI needs - current phrase, progress text, mini-chart data,
outcome on finish - comes from this class.

Design notes:

* Phrases are drawn from the user's source text (the same corpus the practice
  loop reads), cycled with reshuffling so a session does not repeat the same
  sentence over and over. If the source is empty the engine falls back to a
  small pool of common sentences so dictation is always doable.
* ``record_attempt`` stores the user's typed text, the reference text, the
  score and a pass/fail flag (score >= pass_threshold).
* The session ends when ``done`` is true: ``max_attempts`` reached OR the
  ``mastered_streak_required`` consecutive passes are achieved.

The engine is deliberately independent of the scoring module's internals: it
only needs a numeric score (0-100) and the two texts it was scored on. That
keeps it testable without a Tk or engine load.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from mimora import config


# Fallback phrase pool used when the user's source text is empty. Short,
# common sentences so dictation is always doable on a fresh install.
_FALLBACK_PHRASES = [
    "The quick brown fox jumps over the lazy dog.",
    "She sells seashells by the seashore.",
    "The rain in Spain falls mainly on the plain.",
    "Peter Piper picked a peck of pickled peppers.",
    "How much wood would a woodchuck chuck.",
    "A smooth sea never made a skilled sailor.",
    "Practice makes perfect.",
    "Every journey begins with a single step.",
]


@dataclass
class DictationAttempt:
    """One scored dictation attempt."""
    user_text: str
    reference_text: str
    score: float
    attempt: int  # 1-indexed position in the session
    passed: bool  # score >= pass_threshold
    # Reference words the user got wrong (missed or misspelled) plus any
    # extra words typed that are not in the reference. Filled from the
    # comparison ``details`` when the controller passes them in.
    wrong_words: List[str] = field(default_factory=list)
    extra_words: List[str] = field(default_factory=list)


@dataclass
class DictationOutcome:
    """Summary of a finished dictation session."""
    total_attempts: int
    total_passed: int
    first_score: float
    last_score: float
    avg_score: float
    mastered: bool
    attempts: List[DictationAttempt]
    # Distinct reference words that were missed or misspelled across the
    # session, for the "review these later" view and statistics.
    wrong_words: List[str] = field(default_factory=list)
    extra_words: List[str] = field(default_factory=list)


@dataclass
class DictationEngine:
    """State machine for one dictation session.

    The controller calls ``next_phrase`` for each new sentence,
    ``record_attempt`` after scoring the typed text, and ``finish`` when
    the user ends the session.

    ``source_text`` is the plain-text corpus the sentences are drawn from;
    if it is empty the engine falls back to ``_FALLBACK_PHRASES``.
    """
    source_text: str = ""
    pass_threshold: float = field(
        default_factory=lambda: config.DICTATION_PASS_THRESHOLD)
    max_attempts: int = field(
        default_factory=lambda: config.DICTATION_MAX_ATTEMPTS)
    # Consecutive passes needed to declare the session "mastered".
    mastered_streak_required: int = 3

    # Populated by the controller.
    phrases: List[str] = field(default_factory=list)
    attempts: List[DictationAttempt] = field(default_factory=list)
    _phrase_index: int = field(default=0, repr=False)
    _phrase_history: List[str] = field(default_factory=list, repr=False)

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

    def _find_phrases(self) -> List[str]:
        """Return the sentences this session will cycle through."""
        source = (self.source_text or "").strip()
        phrases: List[str] = []
        if source:
            for sentence in re.split(r"[.!?]+\s+", source):
                sentence = sentence.strip()
                if not sentence:
                    continue
                phrases.append(sentence)
        # Fall back to the pool when the source had no usable sentences.
        if not phrases:
            phrases = list(_FALLBACK_PHRASES)
        # Deduplicate while preserving order.
        seen = set()
        unique: List[str] = []
        for p in phrases:
            key = p.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        return unique or list(_FALLBACK_PHRASES)

    def set_source_text(self, source_text: str) -> None:
        """Replace the phrase source and rebuild the candidate list.

        The phrase index is reset so the new source starts from its first
        sentence. Only affects subsequent ``next_phrase`` calls; the current
        sentence stays as-is.
        """
        self.source_text = source_text or ""
        self._phrase_index = 0
        self._phrase_history.clear()
        self._candidate_phrases = self._find_phrases()

    @property
    def candidate_phrases(self) -> List[str]:
        return list(self._candidate_phrases)

    # ------------------------------------------------------------------
    # Phrase cycling
    # ------------------------------------------------------------------

    def next_phrase(self) -> str:
        """Return the sentence for the next attempt.

        Cycles through the candidate list, reshuffling when we reach the
        end, so a long session does not drill the same sentence repeatedly.
        """
        if not self._candidate_phrases:
            phrase = "The quick brown fox jumps over the lazy dog."
        else:
            phrase = self._candidate_phrases[self._phrase_index % len(
                self._candidate_phrases)]
            if self._phrase_index >= len(self._candidate_phrases):
                random.shuffle(self._candidate_phrases)
                self._phrase_index = 0
            else:
                self._phrase_index += 1
        self._phrase_history.append(phrase)
        return phrase

    def previous_phrase(self) -> str:
        """Return the previous sentence, popping from history.

        Rewinds the phrase index so ``next_phrase`` continues from where
        we left off. Returns the current sentence if history is empty.
        """
        if len(self._phrase_history) > 1:
            self._phrase_history.pop()
            phrase = self._phrase_history[-1]
            self._phrase_index = max(0, self._phrase_index - 1)
            return phrase
        return self._phrase_history[-1] if self._phrase_history \
            else "The quick brown fox jumps over the lazy dog."

    # ------------------------------------------------------------------
    # Attempt recording
    # ------------------------------------------------------------------

    def record_attempt(self, user_text: str, reference_text: str,
                       score: float,
                       wrong_words: List[str] | None = None,
                       extra_words: List[str] | None = None) -> DictationAttempt:
        """Record one scored attempt and return the record with pass/fail.

        ``wrong_words`` / ``extra_words`` are optional diagnostic lists from
        the comparison (missed or misspelled reference words, and surplus user
        words). They are stored per attempt so the report can surface which
        words the user should review.
        """
        if self.attempts:
            attempt = self.attempts[-1].attempt + 1
        else:
            attempt = 1
        rec = DictationAttempt(
            user_text=user_text,
            reference_text=reference_text,
            score=float(score),
            attempt=attempt,
            passed=float(score) >= self.pass_threshold,
            wrong_words=list(wrong_words or []),
            extra_words=list(extra_words or []),
        )
        self.attempts.append(rec)
        return rec

    def mastered_streak(self) -> int:
        """Length of the current consecutive-pass streak."""
        streak = 0
        for attempt in reversed(self.attempts):
            if attempt.passed:
                streak += 1
            else:
                break
        return streak

    @property
    def mastered(self) -> bool:
        return self.mastered_streak() >= self.mastered_streak_required

    @property
    def done(self) -> bool:
        return self.mastered or len(self.attempts) >= self.max_attempts

    @property
    def position(self) -> int:
        """1-indexed position of the current sentence in the session.

        Tracks navigation (``next_phrase`` / ``previous_phrase``), not
        scoring, so the counter moves when the user browses with Next or
        Previous. Clamped to ``[1, max_attempts]`` for the progress read-out.
        """
        return max(1, min(len(self._phrase_history), self.max_attempts))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def progress(self) -> Dict[str, Any]:
        """A snapshot for the UI to render progress."""
        return {
            "total": self.max_attempts,
            "current": self.position,
            "passed": sum(1 for a in self.attempts if a.passed),
            "streak": self.mastered_streak(),
            "mastered": self.mastered,
            "done": self.done,
            "scores": [a.score for a in self.attempts[-10:]],
            "threshold": self.pass_threshold,
        }

    def finish(self) -> DictationOutcome:
        """Finalize the session and return a summary."""
        if not self.attempts:
            return DictationOutcome(
                total_attempts=0, total_passed=0,
                first_score=0.0, last_score=0.0, avg_score=0.0,
                mastered=False, attempts=[],
            )
        scores = [a.score for a in self.attempts]
        return DictationOutcome(
            total_attempts=len(self.attempts),
            total_passed=sum(1 for a in self.attempts if a.passed),
            first_score=self.attempts[0].score,
            last_score=self.attempts[-1].score,
            avg_score=round(sum(scores) / len(scores), 2),
            mastered=self.mastered,
            attempts=list(self.attempts),
            wrong_words=self._aggregate_words("wrong_words"),
            extra_words=self._aggregate_words("extra_words"),
        )

    def _aggregate_words(self, attr: str) -> List[str]:
        """Distinct words (first-seen order) across all attempts for ``attr``."""
        seen: List[str] = []
        for attempt in self.attempts:
            for word in getattr(attempt, attr):
                if word not in seen:
                    seen.append(word)
        return seen

    def to_storage_dict(self) -> Dict[str, Any]:
        """Serialize the session for ``StatsStore.record_dictation_session``."""
        outcome = self.finish()
        return {
            "attempts": outcome.total_attempts,
            "passed": outcome.total_passed,
            "first_score": outcome.first_score,
            "last_score": outcome.last_score,
            "avg_score": outcome.avg_score,
            "outcome": "mastered" if outcome.mastered else "unfinished",
            "wrong_words": outcome.wrong_words,
            "extra_words": outcome.extra_words,
            "takes": [
                {
                    "user_text": a.user_text,
                    "reference_text": a.reference_text,
                    "score": a.score,
                    "attempt": a.attempt,
                    "passed": a.passed,
                    "wrong_words": a.wrong_words,
                    "extra_words": a.extra_words,
                }
                for a in self.attempts
            ],
        }
