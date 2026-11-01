# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Cross-file "advance" aggregation for the stats dashboard (pure, Tk-free).

This module joins the three activity files the dashboard already loads -
practice sessions (``stats.json`` ``sessions``), drill sessions
(``drill_sessions``), dictation sessions (``dictation_sessions``) and the
spaced-repetition pool (``review.json`` ``phrases``) - into the sections the
advance tab shows. It is deliberately **pure** (no Tk, no engine imports),
so it is unit-testable without a GUI and shared by any future frontend.

The eight advance sections map to these functions:

0. :func:`kpi_overview` - the cross-file headline numbers.
1. :func:`word_flow` - practice error words -> drilled -> mastered (sankey).
2. :func:`phoneme_weakness` - weak phonemes vs drill-target coverage.
3. *dropped* - the drill/practice score-consistency scatter was analysed and
   found structurally trivial (every drill take IS the practice take it
   references; see the module note on ``word_flow``), so section 3 is folded
   into the funnel instead.
4. :func:`activity_timeline` - per-day practice/drill/dictation counts.
5. :func:`audio_coverage_matrix` - practice vs drill with/without audio.
6. :func:`phrase_funnel` - practice -> errors -> review -> drill -> pass.
7. :func:`activity_correlation` - day/week activity co-occurrence.
8. :func:`normalize_phrase` / :func:`normalize_word` / :func:`normalize_ipa`
   - the joins in this module rely on these, documented in section 8.

Two data limits that shape the joins are worth stating up front:

* ``review.json`` is keyed by the **exact phrase text**, while the practice
  takes store the phrase as generated. The join therefore normalises both
  sides (:func:`normalize_phrase`) - lower-case, punctuation stripped (kept
  for hyphens/apostrophes), whitespace collapsed - so a phrase that differs
  only in punctuation still matches. This is why the review-pool counts in
  :func:`phrase_funnel` can be smaller than a naive key lookup suggests.

* A drill take's ``audio_session_id``/``audio_take_id`` reference the
  **practice take** that produced the phrase, and its ``phrase``/``score``
  are byte-identical to that take (the drill records through the main loop's
  ``on_take_scored``, which persists a fresh practice take). So a drill does
  not re-score an independent recording, and there is no "drill vs practice
  score consistency" to measure - every pair is exactly on the diagonal.
  Sections 1 and 6 therefore use the drill *target* and *outcome*, never a
  score delta.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

#: Display order of the three activity types (used by the timeline charts).
ACTIVITY_ORDER = ("practice", "drill", "dictation")

#: Default weak-phoneme threshold. ``phoneme_scores[].score`` is a 0-1
#: per-phoneme similarity (NOT a 0-100 grade), so "weak" is a fraction of
#: the way to a perfect phoneme; 0.5 is the mid-scale cut.
DEFAULT_WEAK_PHONEME_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Section 8 - normalisation (the join seam)
# ---------------------------------------------------------------------------

def normalize_phrase(text: str) -> str:
    """Normalise a phrase for cross-file matching.

    Lower-cases, strips punctuation (keeping hyphens and apostrophes, which
    are significant inside English words), and collapses whitespace. A curly
    apostrophe (U+2019, common in smart-quoted text) is folded to the ASCII
    one so "don't" matches however it was typed. This is the seam that lets a
    practice take's phrase match a review.json key even when the two files
    differ in punctuation or case.
    """
    text = (text or "").lower()
    text = text.replace("\u2019", "'")
    text = re.sub(r"[^a-z0-9'\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_word(word: str) -> str:
    """Normalise a single word for matching (see :func:`normalize_phrase`).

    Also strips a trailing possessive/apostrophe form (``'s``/``’s``) so
    "children's" and "children" join; a leading apostrophe (e.g. ``"'em"``)
    is kept because it is part of the word in the source text.
    """
    text = normalize_phrase(word)
    if not text:
        return ""
    if text.endswith(("'s", "\u2019s")):
        text = text[:-2]
    # Strip only the trailing side; a leading apostrophe is part of the word.
    return text.rstrip("'\u2019")


def normalize_ipa(ipa: str) -> str:
    """Normalise an IPA symbol for matching.

    Drill phoneme targets are stored as ``"/r/"`` (with the slashes, from the
    picker) while ``phoneme_scores[].ipa`` is the bare symbol (``"r"``); this
    strips surrounding slashes and whitespace so the two join.
    """
    return str(ipa or "").strip().strip("/").strip()


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------

def _takes_from_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten ``sessions[].takes`` into one take list."""
    return [t for s in sessions for t in (s.get("takes") or [])]


def _activity_date(session: Dict[str, Any], kind: str) -> Optional[str]:
    """The ``YYYY-MM-DD`` a session belongs to, per activity kind.

    Practice sessions carry a real ``started_at``. Drill and dictation
    sessions write ``started_at`` as the *save* time (the engine only knows
    the session ended), so for those ``ended_at`` is the reliable axis - the
    same limit :mod:`mimora.drill_analysis` documents.
    """
    key = "started_at" if kind == "practice" else "ended_at"
    ts = (session.get(key) or session.get("ended_at") or "").strip()
    if not ts:
        return None
    return ts[:10]


def _distinct_error_words(takes: List[Dict[str, Any]]) -> set:
    """The distinct normalised words in every take's ``error_words``."""
    words = set()
    for t in takes:
        for ew in (t.get("error_words") or []):
            w = normalize_word(ew.get("word"))
            if w:
                words.add(w)
    return words


def _drill_targets(drill_sessions: List[Dict[str, Any]],
                   types: Tuple[str, ...]) -> Dict[str, set]:
    """Normalised drill target values per type, restricted to ``types``."""
    out: Dict[str, set] = {}
    for ds in drill_sessions:
        target = ds.get("target") or {}
        ttype = str(target.get("type") or "").lower()
        if ttype not in types:
            continue
        val = normalize_phrase(target.get("value"))
        if val:
            out.setdefault(ttype, set()).add(val)
    return out


def _drill_target_values(drill_sessions: List[Dict[str, Any]],
                         types: Tuple[str, ...]) -> set:
    """Union of normalised drill target values across ``types``."""
    union: set = set()
    for values in _drill_targets(drill_sessions, types).values():
        union |= values
    return union


def _review_phrase_set(review_phrases: Dict[str, Any]) -> set:
    """The normalised set of review-pool phrase keys."""
    return {normalize_phrase(k) for k in (review_phrases or {})}


def _review_passed_set(review_phrases: Dict[str, Any]) -> set:
    """The normalised set of review-pool phrases whose state is ``passed``."""
    passed = set()
    for key, state in (review_phrases or {}).items():
        if state and state.get("passed"):
            passed.add(normalize_phrase(key))
    return passed


# ---------------------------------------------------------------------------
# Section 0 - key overview
# ---------------------------------------------------------------------------

def kpi_overview(takes: List[Dict[str, Any]],
                 drill_sessions: List[Dict[str, Any]],
                 dictation_sessions: List[Dict[str, Any]],
                 review_phrases: Dict[str, Any]) -> Dict[str, Any]:
    """The cross-file headline numbers (all section 0 cards).

    Returns a dict with ``practice_takes``, ``error_words`` (distinct),
    ``review_pool``, ``drill_sessions``, ``dictation_sessions``,
    ``audio_coverage`` (0..1, None when no takes) and ``mastered_drills``.
    """
    error_words = len(_distinct_error_words(takes))
    with_audio = sum(1 for t in takes if t.get("audio_files"))
    coverage = (with_audio / len(takes)) if takes else None
    mastered = sum(1 for ds in drill_sessions
                   if str(ds.get("outcome") or "") == "mastered")
    return {
        "practice_takes": len(takes),
        "error_words": error_words,
        "review_pool": len(review_phrases or {}),
        "drill_sessions": len(drill_sessions),
        "dictation_sessions": len(dictation_sessions),
        "audio_coverage": coverage,
        "mastered_drills": mastered,
    }


# ---------------------------------------------------------------------------
# Section 1 - practice error words -> drilled -> mastered (sankey)
# ---------------------------------------------------------------------------

def word_flow(takes: List[Dict[str, Any]],
              drill_sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The error-word -> drilled -> mastered flow (section 1 sankey).

    *Error words* are the distinct normalised words in every take's
    ``error_words``. A word is *drilled* when some drill session targeted it
    as a ``word`` or ``single_word`` target; *mastered* when such a session
    ended with ``outcome == "mastered"``.

    Returns the counts the sankey needs::

        {"error_words": n, "drilled": n, "mastered": n,
         "not_drilled": n, "not_mastered": n}

    Note that a drill target can name a word that never appeared in an error
    list; only the words that did are counted here, because the sankey shows
    the practice side of the join. ``word`` and ``single_word`` are the same
    semantic target (one word) in two shapes, so both are matched.
    """
    error_words = _distinct_error_words(takes)
    drilled = _drill_target_values(drill_sessions, ("word", "single_word"))
    mastered = set()
    for ds in drill_sessions:
        target = ds.get("target") or {}
        ttype = str(target.get("type") or "").lower()
        if ttype not in ("word", "single_word"):
            continue
        val = normalize_phrase(target.get("value"))
        if val and val in error_words and \
                str(ds.get("outcome") or "") == "mastered":
            mastered.add(val)

    drilled_in_errors = drilled & error_words
    mastered_in_drilled = mastered & drilled_in_errors
    return {
        "error_words": len(error_words),
        "drilled": len(drilled_in_errors),
        "mastered": len(mastered_in_drilled),
        "not_drilled": len(error_words - drilled_in_errors),
        "not_mastered": len(drilled_in_errors - mastered_in_drilled),
    }


def word_flow_nodes(counts: Dict[str, Any]) -> Tuple[List[Tuple[str, Any]],
                                                    List[Tuple[str, Any]]]:
    """Build the sankey ``(nodes, links)`` for :func:`word_flow`.

    ``nodes`` is ``[(node_id, label, value, column)]``; ``links`` is
    ``[(src_id, dst_id, value)]``. This is the exact shape
    ``_CanvasChart.draw_sankey`` consumes, and it is kept here (pure) so the
    tests can assert the flow without a Tk canvas.
    """
    n_err = int(counts.get("error_words", 0))
    n_drill = int(counts.get("drilled", 0))
    n_master = int(counts.get("mastered", 0))
    n_not_drill = int(counts.get("not_drilled", 0))
    n_not_master = int(counts.get("not_mastered", 0))
    nodes = [
        ("err", "Error words", n_err, 0),
        ("drilled", "Drilled", n_drill, 1),
        ("not_drilled", "Not drilled", n_not_drill, 1),
        ("mastered", "Mastered", n_master, 2),
        ("not_mastered", "Not mastered", n_not_master, 2),
    ]
    links = [
        ("err", "drilled", n_drill),
        ("err", "not_drilled", n_not_drill),
        ("drilled", "mastered", n_master),
        ("drilled", "not_mastered", n_not_master),
    ]
    return nodes, links


def drill_take_origin(drill_sessions: List[Dict[str, Any]],
                      takes: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many drill takes reference a practice take (section 3 replacement).

    Every drill take that was scored through the main loop points its
    ``audio_session_id``/``audio_take_id`` at the practice take that produced
    the phrase; a take that never got scored (or was deleted) has no
    reference. Returns::

        {"referenced": n, "unreferenced": n}

    This replaces the originally-planned drill-vs-practice score-consistency
    scatter: because a drill take IS the practice take it references (same
    phrase, same score), there is no independent grade to compare, and the
    meaningful question is simply how much of the drill history links back
    to a concrete practice take.
    """
    practice = {(t.get("session_id"), t.get("id")) for t in takes}
    referenced = 0
    unreferenced = 0
    for ds in drill_sessions:
        for dt in (ds.get("takes") or []):
            key = (dt.get("audio_session_id"), dt.get("audio_take_id"))
            if key and key in practice:
                referenced += 1
            else:
                unreferenced += 1
    return {"referenced": referenced, "unreferenced": unreferenced}


# ---------------------------------------------------------------------------
# Section 2 - phoneme weakness vs drill-target coverage
# ---------------------------------------------------------------------------

def phoneme_weakness(takes: List[Dict[str, Any]],
                     drill_sessions: List[Dict[str, Any]],
                     weak_threshold: float = DEFAULT_WEAK_PHONEME_THRESHOLD,
                     ) -> Dict[str, Any]:
    """Weak phonemes and whether a drill targets them (section 2).

    A phoneme's score is the count-weighted average of its
    ``phoneme_scores[].score`` values (0-1 similarity, NOT a 0-100 grade).
    A phoneme is *weak* when that average is below ``weak_threshold``. A
    weak phoneme is *drilled* when some drill session has a ``phoneme``
    target of the same symbol; *mastered* when such a session ended
    ``mastered``.

    Returns::

        {"threshold": t, "weak": [(ipa, avg, drilled, mastered), ...],
         "weak_count": n, "drilled_count": n, "mastered_count": n,
         "drill_ipa_no_practice": [...]}

    ``weak`` is sorted weakest-first (ascending average) and is the full
    list - the caller truncates for display. ``drill_ipa_no_practice`` lists
    phonemes that were drilled but never appeared in a take (a sanity check:
    a drill target the practice data never shows).
    """
    totals: Dict[str, float] = defaultdict(float)
    weights: Dict[str, int] = defaultdict(int)
    for t in takes:
        for ps in (t.get("phoneme_scores") or []):
            ipa = normalize_ipa(ps.get("ipa"))
            if not ipa:
                continue
            score = float(ps.get("score") or 0.0)
            count = int(ps.get("count") or 1)
            totals[ipa] += score * count
            weights[ipa] += count

    avgs = {ipa: totals[ipa] / weights[ipa] for ipa in totals}

    drilled = set()
    mastered = set()
    for ds in drill_sessions:
        target = ds.get("target") or {}
        if str(target.get("type") or "").lower() != "phoneme":
            continue
        ipa = normalize_ipa(target.get("value"))
        if not ipa:
            continue
        drilled.add(ipa)
        if str(ds.get("outcome") or "") == "mastered":
            mastered.add(ipa)

    weak = sorted(
        ((ipa, avgs[ipa], ipa in drilled, ipa in mastered)
         for ipa in avgs if avgs[ipa] < weak_threshold),
        key=lambda row: (row[1], row[0]),
    )
    return {
        "threshold": weak_threshold,
        "weak": weak,
        "weak_count": len(weak),
        "drilled_count": sum(1 for _, _, d, _ in weak if d),
        "mastered_count": sum(1 for _, _, _, m in weak if m),
        "drill_ipa_no_practice": sorted(drilled - set(avgs)),
    }


# ---------------------------------------------------------------------------
# Section 4 - 3-type activity timeline
# ---------------------------------------------------------------------------

def activity_timeline(sessions: List[Dict[str, Any]],
                      drill_sessions: List[Dict[str, Any]],
                      dictation_sessions: List[Dict[str, Any]],
                      ) -> Dict[str, Any]:
    """Per-day session counts for practice / drill / dictation (section 4).

    Returns ``{"days": [...], "practice": [...], "drill": [...],
    "dictation": [...]}`` where each count list is aligned with ``days``
    (``YYYY-MM-DD``, oldest first). Only days with any activity appear, so a
    long idle stretch does not draw a zero-filled run. Practice counts
    sessions; drill/dictation count sessions too (not takes), because a
    drill session's ``attempts`` is the better volume measure and the
    timeline compares *sessions* across the three modes.
    """
    by_day: Dict[str, Dict[str, int]] = {}
    buckets = [
        ("practice", sessions),
        ("drill", drill_sessions),
        ("dictation", dictation_sessions),
    ]
    for kind, items in buckets:
        for s in items:
            day = _activity_date(s, kind)
            if not day:
                continue
            by_day.setdefault(day, {"practice": 0, "drill": 0, "dictation": 0})
            by_day[day][kind] += 1

    days = sorted(by_day)
    out = {
        "days": days,
        "practice": [by_day[d]["practice"] for d in days],
        "drill": [by_day[d]["drill"] for d in days],
        "dictation": [by_day[d]["dictation"] for d in days],
    }
    return out


# ---------------------------------------------------------------------------
# Section 5 - audio coverage matrix
# ---------------------------------------------------------------------------

def audio_coverage_matrix(takes: List[Dict[str, Any]],
                          drill_sessions: List[Dict[str, Any]],
                          ) -> Dict[str, Any]:
    """Practice vs drill takes with/without audio (section 5).

    A take *has audio* when its ``audio_files`` is a non-empty dict (the
    metadata path is always written, but audio is only stored under
    ``HISTORY_MODE == "with_audio"``). Returns::

        {"practice": {"with": n, "without": n},
         "drill": {"with": n, "without": n}}
    """
    def split(items: List[Dict[str, Any]]) -> Dict[str, int]:
        with_ = sum(1 for it in items if it.get("audio_files"))
        return {"with": with_, "without": len(items) - with_}

    drill_takes = [dt for ds in drill_sessions for dt in (ds.get("takes") or [])]
    return {"practice": split(takes), "drill": split(drill_takes)}


# ---------------------------------------------------------------------------
# Section 6 - phrase funnel
# ---------------------------------------------------------------------------

def phrase_funnel(takes: List[Dict[str, Any]],
                  drill_sessions: List[Dict[str, Any]],
                  review_phrases: Dict[str, Any],
                  ) -> Dict[str, Any]:
    """The practice -> errors -> review -> drill -> pass funnel (section 6).

    Every stage is at the **phrase** level (normalised via
    :func:`normalize_phrase`), because review.json is keyed by phrase text:

    * *practice* - distinct phrases in the practice takes;
    * *errors* - phrases that appeared in a take with ``error_words``;
    * *review* - phrases present in the review pool;
    * *drill* - phrases that appear in a drill take (the drill records the
      practice phrase it trained);
    * *pass* - review-pool phrases whose state is ``passed``.

    The stages are not strictly nested (a phrase can be drilled without ever
    reaching the review pool, and vice versa), so the funnel reports the
    count of each stage plus the consecutive overlaps, which is what the
    conversion path really is::

        {"stages": [(label, count), ...],
         "links": [practice->errors, errors->review, review->drill,
                   drill->pass]}
    """
    practice = {normalize_phrase(t.get("phrase")) for t in takes}
    practice.discard("")
    errors = {normalize_phrase(t.get("phrase")) for t in takes
              if t.get("error_words")}
    errors.discard("")
    review = _review_phrase_set(review_phrases)
    passed = _review_passed_set(review_phrases)
    drilled = {normalize_phrase(dt.get("phrase"))
               for ds in drill_sessions
               for dt in (ds.get("takes") or [])}
    drilled.discard("")

    stages = [
        ("Practice phrases", len(practice)),
        ("With errors", len(errors)),
        ("In review", len(review)),
        ("Drilled", len(drilled)),
        ("Passed", len(passed)),
    ]
    links = [
        len(practice & errors),
        len(errors & review),
        len(review & drilled),
        len(drilled & passed),
    ]
    return {"stages": stages, "links": links}


# ---------------------------------------------------------------------------
# Section 7 - day / week activity co-occurrence
# ---------------------------------------------------------------------------

def activity_correlation(sessions: List[Dict[str, Any]],
                         drill_sessions: List[Dict[str, Any]],
                         dictation_sessions: List[Dict[str, Any]],
                         ) -> Dict[str, Any]:
    """Day-level activity co-occurrence between the three modes (section 7).

    The practice/drill/dictation session counts are bucketed per calendar day
    (the same dates :func:`activity_timeline` uses), and the Pearson
    correlation of each pair is computed over those days. Returns::

        {"days": n, "practice_drill": r|None, "practice_dictation": r|None,
         "drill_dictation": r|None}

    A pair is ``None`` when either series is constant (zero variance - e.g.
    dictation on a single day, or a mode with no sessions at all), because
    the correlation coefficient is undefined there. Week-level bucketing is
    not reported: with less than a fortnight of data there is at most one
    distinct week, so a week correlation is always undefined; the
    day-level result is the useful one.
    """
    days = {_activity_date(s, "practice") for s in sessions}
    days |= {_activity_date(s, "drill") for s in drill_sessions}
    days |= {_activity_date(s, "dictation") for s in dictation_sessions}
    days.discard(None)
    ordered = sorted(days)

    def counts(kind: str, items: List[Dict[str, Any]]) -> List[int]:
        per_day: Dict[str, int] = defaultdict(int)
        for s in items:
            day = _activity_date(s, kind)
            if day:
                per_day[day] += 1
        return [per_day.get(d, 0) for d in ordered]

    practice = counts("practice", sessions)
    drill = counts("drill", drill_sessions)
    dictation = counts("dictation", dictation_sessions)

    def pearson(a: List[int], b: List[int]) -> Optional[float]:
        if len(a) < 2:
            return None
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a)
        db = sum((y - mb) ** 2 for y in b)
        if da <= 0 or db <= 0:
            return None
        return num / (da ** 0.5 * db ** 0.5)

    return {
        "days": len(ordered),
        "practice_drill": pearson(practice, drill),
        "practice_dictation": pearson(practice, dictation),
        "drill_dictation": pearson(drill, dictation),
    }


# ---------------------------------------------------------------------------
# Section 8 - join gotchas (rendered as a note; the logic is the helpers above)
# ---------------------------------------------------------------------------

JOIN_GOTCHAS: List[str] = [
    "review.json is keyed by exact phrase text; the joins here normalise both "
    "sides (lower-case, punctuation stripped, whitespace collapsed).",
    "Drill take audio refs point to the practice take that produced the "
    "phrase; the drill score is that take's score, not an independent grade.",
    "phoneme_scores[].score is a 0-1 similarity, not a 0-100 grade; the weak "
    "threshold is on that scale.",
    "Drill and dictation sessions record started_at as the save time, so the "
    "timeline and correlation use ended_at for those modes.",
]
