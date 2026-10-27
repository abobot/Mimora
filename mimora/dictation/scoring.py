# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Word-level comparison and scoring for dictation.

The user hears a spoken sentence (the reference) and types what they heard.
This module compares the typed text against the reference at the WORD level
and produces a 0-100 score plus per-word diagnostics so the UI can show
exactly which words were correct, missed, extra, or misspelled.

Pure Python - no Tkinter, no engine imports - so it is trivially unit-testable.
The comparison is an edit-distance alignment (LCS via ``difflib``) rather than
a naive index-by-index loop, so an inserted or dropped word does not shift
every later word into a "wrong" bucket.

Design notes:

* Normalisation: casefold + strip punctuation + collapse whitespace, applied
  to both texts before comparison, so "Hello, world." and "hello world" are
  the same dictation. Punctuation inside a word (apostrophes, hyphens) is
  kept - "don't" and "do not" are genuinely different dictations.
* ``compare_sentences`` returns a dict carrying the word lists, an
  ``accuracy`` (0-100), and a ``status`` list aligned to the REFERENCE words.
* ``score_dictation`` wraps the comparison and adds a ``score`` (0-100)
  plus the accuracy itself; the UI displays ``score`` and the controller
  persists it.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple


# Word tokenizer: keeps Unicode letters and internal apostrophes/hyphens so
# contractions ("don't") and hyphenated words ("well-known") stay one token.
_WORD_RE = re.compile(r"[\w'’-]+(?:-[\w'’-]+)*")


def _tokenize(text: str) -> List[str]:
    """Split text into normalised word tokens.

    Empty text -> empty list (a blank submission is a valid, score-0 answer).
    """
    if not text:
        return []
    return [_normalize_word(w) for w in _WORD_RE.findall(text)]


def _normalize_word(word: str) -> str:
    """Lower-case and strip edge punctuation from one word token."""
    return word.strip(".,;:!?\"'“”‘’()[]{}").lower()


def tokenize_words(text: str) -> List[str]:
    """Return the normalised word tokens of ``text`` (public).

    The UI uses this to size the word-slot input to the reference sentence's
    word count; it is the same tokenization the comparison uses, so the slot
    count always matches the scoring's ``reference_words``.
    """
    return _tokenize(text)


def _strip_punctuation(text: str) -> str:
    """Lower-case the whole text but keep internal apostrophes/hyphens."""
    return text.lower()


def compare_sentences(user_text: str, reference_text: str) -> Dict:
    """Compare the user's dictation against the reference at word level.

    Returns a dict with:

    ``user_words``      - the normalised user word list.
    ``reference_words`` - the normalised reference word list.
    ``word_count``      - len(reference_words) (0 when empty).
    ``exact_matches``   - number of reference words matched exactly, in order.
    ``accuracy``        - exact_matches / max(word_count, 1) * 100, 0-100.
    ``status``          - one entry per REFERENCE word, each a dict:
                            {"word": str, "state": "correct"|"missed"|"wrong"}
                          where "correct" matches in order, "missed" means the
                          user omitted it, and "wrong" means a word occupies
                          the slot but differs (spelling error or inserted).
    ``extra_words``     - user words with no reference counterpart (insertions).
    """
    user_words = _tokenize(user_text)
    ref_words = _tokenize(reference_text)
    word_count = len(ref_words)
    if word_count == 0:
        # No reference: treat any user input as entirely extra.
        return {
            "user_words": user_words,
            "reference_words": ref_words,
            "word_count": 0,
            "exact_matches": 0,
            "accuracy": 0.0,
            "status": [],
            "extra_words": list(user_words),
        }

    # Build the per-reference-word status using a SequenceMatcher over the
    # word lists. ``get_opcodes`` yields the edit script; we consume it to
    # assign states:
    #   "equal"    -> all these reference words are "correct".
    #   "delete"   -> reference words with no user counterpart -> "missed".
    #   "insert"   -> user words with no reference counterpart -> "extra".
    #   "replace"  -> the overlapping span is treated as wrong-for-slot:
    #                 any reference word that is also present in the user's
    #                 span is "wrong" (spelling error); if the user typed
    #                 more words than the reference span, the surplus are
    #                 "extra".
    sm = SequenceMatcher(None, ref_words, user_words, autojunk=False)
    status: List[Dict[str, str]] = [None] * word_count  # type: ignore[list-item]
    extra_words: List[str] = []

    def mark_missing(lo: int, hi: int) -> None:
        for i in range(lo, hi):
            status[i] = {"word": ref_words[i], "state": "missed"}

    def mark_correct(lo: int, hi: int) -> None:
        for i in range(lo, hi):
            status[i] = {"word": ref_words[i], "state": "correct"}

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            mark_correct(i1, i2)
        elif tag == "delete":
            mark_missing(i1, i2)
        elif tag == "insert":
            extra_words.extend(user_words[j1:j2])
        elif tag == "replace":
            # Reference span [i1:i2), user span [j1:j2). The user typed
            # something for the first min(n_ref, n_user) slots - a spelling
            # attempt ("wrong"), even if the word does not match exactly.
            # Reference slots with no user word at all are "missed".
            ref_span = ref_words[i1:i2]
            user_span = user_words[j1:j2]
            n_ref = len(ref_span)
            n_user = len(user_span)
            slots_written = min(n_ref, n_user)
            for i in range(i1, i2):
                if i - i1 < slots_written:
                    status[i] = {"word": ref_words[i], "state": "wrong"}
                else:
                    status[i] = {"word": ref_words[i], "state": "missed"}
            # Surplus user words beyond the reference span are extra.
            if n_user > n_ref:
                extra_words.extend(user_span[n_ref:])

    # Any status slots left None (should not happen) default to "missed".
    for i in range(word_count):
        if status[i] is None:
            status[i] = {"word": ref_words[i], "state": "missed"}

    exact_matches = sum(1 for s in status if s["state"] == "correct")
    accuracy = (exact_matches / word_count * 100.0) if word_count else 0.0

    return {
        "user_words": user_words,
        "reference_words": ref_words,
        "word_count": word_count,
        "exact_matches": exact_matches,
        "accuracy": round(accuracy, 2),
        "status": status,
        "extra_words": extra_words,
    }


def score_dictation(user_text: str, reference_text: str) -> Dict:
    """Compare and score a dictation attempt.

    Returns the :func:`compare_sentences` dict plus:

    ``score``      - 0-100; equals ``accuracy`` (rounded to 1 decimal).
    ``passed``     - True when ``score`` >= ``pass_threshold`` (if given).
    ``total_words``- alias of ``word_count`` for the UI.
    """
    result = compare_sentences(user_text, reference_text)
    score = round(result["accuracy"], 1)
    result["score"] = score
    result["passed"] = False  # filled by the caller with the threshold
    result["total_words"] = result["word_count"]
    return result


def alignment_summary(result: Dict) -> str:
    """Human-readable one-liner for logs / reports.

    E.g. "6/8 words · 2 missed · 1 extra · 1 misspelled".
    """
    if not result:
        return ""
    status = result.get("status") or []
    correct = sum(1 for s in status if s["state"] == "correct")
    missed = sum(1 for s in status if s["state"] == "missed")
    wrong = sum(1 for s in status if s["state"] == "wrong")
    extra = len(result.get("extra_words") or [])
    total = result.get("word_count") or 0
    return (f"{correct}/{total} words · {missed} missed · "
            f"{wrong} misspelled · {extra} extra")
