# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Persistent stats storage: sessions, takes and roll-up totals.

Wraps ``config/stats.json`` with atomic writes, a rolling ``.bak`` snapshot
(kept so a torn write cannot lose history), per-day archive snapshots, and a
bump-on-write schema version that lets ``migrations.migrate_to_latest`` upgrade
older files on first read.

Design notes that matter:

* ``stats.json`` is the source of truth. ``sessions[].totals`` is a cached
  roll-up so the stats panel can render without re-scanning every take; it is
  recomputed lazily from ``takes`` whenever a take is deleted.
* Every ``save()`` is a single ``os.replace``, so a crash mid-write leaves
  either the previous or the new file, never a partial one. The old file is
  copied to ``stats.json.bak`` before the swap.
* Session lifecycle is owned here: ``open_session()`` either resumes a
  previously unfinished session (crash recovery) or, when the previous one was
  silent for longer than ``IDLE_TIMEOUT``, splits it off and starts a fresh
  one. Idle detection reads the wall clock, so two consecutive takes 40
  minutes apart land in different sessions without any explicit signal.

The store is deliberately Tk-free. All calls happen on the Tk main thread
(the take-scoring callback is ``root.after``-ed onto it), so no lock is
needed today; the ``threading.RLock`` is there to make it safe if a worker
ever calls it, and cheap enough to hold unconditionally.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mimora import config
from mimora import paths


# Schema version written to stats.json. Bumped only when a change would break
# an older reader; ``migrations.migrate_to_latest`` handles the upgrades.
STATS_SCHEMA_VERSION = "1.0"

# Session id format: ``s-<UTC ISO seconds>-<4-hex>``. Two reasons for the
# random tail: a crash-recover that restarts in the same second must not
# collide, and the id is what a user pastes into a bug report so two lines
# that read ``s-2026-10-26T182500`` are ambiguous.
_SESSION_ID_PREFIX = "s-"


def _iso(dt: datetime) -> str:
    """Serialize a datetime as an ISO-8601 string with a real offset.

    Uses ``astimezone()`` on the local timezone, so the offset reflects
    whatever the user's OS reports - the whole file reads consistently in
    the user's wall clock, not UTC.
    """
    return dt.astimezone().isoformat(timespec="seconds")


def _now_iso() -> str:
    return _iso(datetime.now().astimezone())


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse the ISO-8601 timestamps this module writes.

    Returns None on any shape we cannot parse rather than raising - a user
    hand-edits stats.json and a single typo should not take the store down.
    The callers treat a missing/invalid timestamp as "unknown" and fall
    back to the file mtime or a conservative default.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _new_session_id() -> str:
    """A fresh session id, unique within a single clock tick."""
    dt = datetime.now().astimezone()
    stamp = dt.strftime("%Y-%m-%dT%H%M%S")
    return f"{_SESSION_ID_PREFIX}{stamp}-{uuid.uuid4().hex[:4]}"


def _new_take_id() -> str:
    return f"t-{datetime.now().astimezone().strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"


# ---------------------------------------------------------------------------
# Error extraction from an engine PronunciationResult.
# ---------------------------------------------------------------------------
# The phoneme and acoustic engines both fill ``word_diff`` as
# ``[{"expected": ..., "heard": ...}]`` and (phoneme only) ``weak_phonemes``
# as ``[{"phoneme": ..., "severity": ..., "count": ...}]``. We re-shape those
# into the ``error_words`` / ``phoneme_scores`` fields the aggregate
# functions (analysis/error_words.py, analysis/phonemes.py) expect, so the
# rest of the code only sees one shape regardless of which engine ran.

# Below this score a word counts as "mispronounced" for the error-word
# leader board. Chosen to match config.PRONUNCIATION_SCORE_THRESHOLD's
# neighbourhood; a tighter threshold would make every borderline word
# "wrong" and the board unreadable.
WORD_ERROR_SCORE_CUTOFF = 0.75


def extract_error_words(result: Any) -> List[Dict[str, Any]]:
    """Flatten a PronunciationResult into the persisted ``error_words`` shape.

    Returns ``[]`` when nothing is available - engines that do not bucket
    word-level data (or the "none" engine) still produce a valid record.

    Every entry has:
        word       - the target word (lower-cased, stripped of punctuation)
        score      - per-word confidence, 0..1 (0.0 for words we know are
                     wrong but the engine did not score individually)
        error_type - one of: pronunciation, missing, extra, substitution,
                     fluency
    """
    if result is None:
        return []
    out: List[Dict[str, Any]] = []

    # Primary source: word_diff. Its shape differs slightly between engines:
    # acoustic gives one pair per diverging segment, phoneme lists the diverging
    # words themselves. Both are usable here.
    word_diff = getattr(result, "word_diff", None) or []
    for pair in word_diff:
        expected = (pair.get("expected") or "").strip().lower()
        if not expected:
            continue
        heard = (pair.get("heard") or "").strip().lower()
        # No confidence per pair, so classify by presence/absence instead.
        if not heard:
            error_type = "missing"
        elif heard == expected:
            # Engine said this diverged but the strings match - probably a
            # fluency/timing issue in the alignment.
            error_type = "fluency"
        else:
            error_type = "substitution"
        out.append({"word": expected, "score": 0.0, "error_type": error_type})

    # Secondary source: reference_words carry a per-word ``correct`` flag from
    # the phoneme engine. Anything marked wrong that we have not yet recorded
    # joins the list as "pronunciation" with a zero score (we don't get a
    # per-word confidence from the engine).
    ref_words = getattr(result, "reference_words", None) or []
    seen = {entry["word"] for entry in out}
    for entry in ref_words:
        word = (entry.get("word") or "").strip().lower()
        if not word or word in seen:
            continue
        if entry.get("correct") is False:
            seen.add(word)
            out.append({"word": word, "score": 0.0,
                        "error_type": "pronunciation"})

    return out


def extract_phoneme_scores(result: Any) -> List[Dict[str, Any]]:
    """Return ``[{"ipa": ..., "score": ..., "in_word": ...}]`` from a result.

    The phoneme engine exposes ``weak_phonemes`` as (severity, count) pairs,
    not per-phoneme scores. We invert severity into a rough score (1 - severity)
    so downstream aggregation has a comparable float, and attach the word the
    phoneme appears in when we can find one from ``reference_words``.
    """
    if result is None:
        return []
    weak = getattr(result, "weak_phonemes", None) or []
    out: List[Dict[str, Any]] = []
    # Weakness in the engine is measured as a distance; severity is normalized
    # to 0..1 with 1 = worst. Inverting gives a 0..1 "goodness" we can
    # aggregate. Cap at 1 to guard against a non-normalized engine.
    for entry in weak:
        ipa = (entry.get("phoneme") or "").strip()
        if not ipa:
            continue
        severity = entry.get("severity")
        try:
            severity = float(severity) if severity is not None else 0.5
        except (TypeError, ValueError):
            severity = 0.5
        score = max(0.0, min(1.0, 1.0 - severity))
        out.append({"ipa": ipa, "score": score, "count": entry.get("count", 1)})
    return out


def compute_words_per_minute(transcript: str, audio_duration_sec: float) -> float:
    """Words-per-minute from the ASR transcript and the take's audio length.

    Both inputs are optional: with an empty transcript or zero/negative
    duration the function returns 0.0 so the field is always numeric and
    never ``None``.
    """
    if not transcript or not audio_duration_sec or audio_duration_sec <= 0:
        return 0.0
    words = len([w for w in transcript.split() if w.strip()])
    if words == 0:
        return 0.0
    return words / (audio_duration_sec / 60.0)


# ---------------------------------------------------------------------------
# The store itself.
# ---------------------------------------------------------------------------

@dataclass
class StatsStore:
    """Load/save the persistent history file and manage sessions.

    Owns the in-memory ``data`` dict. All mutating methods persist
    immediately (atomic replace + `.bak` snapshot); all reads refresh from
    disk when ``reload_if_changed`` is True.

    ``path`` defaults to ``config.stats_path()`` and can be overridden by a
    test that points it at a tmp directory.
    """

    path: Path = field(default_factory=lambda: Path(config.stats_path()))
    idle_timeout_sec: float = field(default_factory=lambda: config.IDLE_TIMEOUT)
    daily_backup_dir: Path = field(default_factory=lambda: Path(config.DAILY_BACKUP_DIR))

    data: Dict[str, Any] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _loaded: bool = field(default=False, repr=False)

    # -- lifecycle -----------------------------------------------------------

    def __post_init__(self) -> None:
        self._path_obj = Path(self.path)
        self._daily_obj = Path(self.daily_backup_dir)
        # Idle timeout is intentionally read at call time from config rather
        # than frozen at construction: the settings window can flip it live
        # (see settings_ctl.LIVE_CONFIG_ATTRS) and the next open_session_if_needed
        # must see the new value without a restart.
        if self.idle_timeout_sec <= 0:
            self.idle_timeout_sec = config.IDLE_TIMEOUT
        # Load lazily so constructing the store does not touch disk until
        # something actually wants data - important because the store is
        # built during PronunciationTrainerGUI.__init__, which runs before
        # logging is fully configured.
        self.data = {}

    # -- loading -------------------------------------------------------------

    def _empty_data(self) -> Dict[str, Any]:
        return {
            "version": STATS_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "settings_snapshot": {},
            "sessions": [],
            "drill_sessions": [],
            "dictation_sessions": [],
            "tracked_words": [],
        }

    def load(self, force: bool = False) -> None:
        """Read stats.json into memory, migrating if necessary.

        Missing file: an empty data skeleton is created (no error - a fresh
        install has nothing to load). Corrupt file: the ``.bak`` snapshot is
        restored and the corrupt file is renamed to ``.corrupt-<stamp>`` so
        the user can look at it without it blocking further writes.
        """
        with self._lock:
            if self._loaded and not force:
                return
            parent = self._path_obj.parent
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logging.error("Cannot create stats dir %s: %s", parent, exc)
                self.data = self._empty_data()
                self._loaded = True
                return

            if not self._path_obj.exists():
                self.data = self._empty_data()
                self._loaded = True
                return

            try:
                with open(self._path_obj, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                self._recover_corrupt(exc)
                self._loaded = True
                return

            if not isinstance(data, dict):
                logging.warning("stats.json is not an object; starting fresh.")
                self.data = self._empty_data()
                self._loaded = True
                return

            # Fill missing top-level keys so older files work with new code
            # without requiring the migration to touch them (the migration
            # is for schema changes inside a session/take).
            data.setdefault("version", STATS_SCHEMA_VERSION)
            data.setdefault("created_at", _now_iso())
            data.setdefault("settings_snapshot", {})
            data.setdefault("sessions", [])
            data.setdefault("drill_sessions", [])
            data.setdefault("dictation_sessions", [])
            data.setdefault("tracked_words", [])
            self.data = data
            self._loaded = True

    def _recover_corrupt(self, exc: BaseException) -> None:
        """Swap in ``stats.json.bak`` when the primary file cannot be parsed."""
        logging.error("stats.json unreadable (%s); trying .bak", exc)
        bak = self._path_obj.with_suffix(self._path_obj.suffix + ".bak")
        if bak.exists():
            try:
                with open(bak, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    # Restore the backup as the new primary.
                    self._write_atomic(bak)
                    self.data = self._empty_data()
                    for key, value in data.items():
                        self.data.setdefault(key, value)
                    self._write_atomic(self._path_obj)
                    logging.warning("Recovered stats from %s.", bak.name)
                    return
            except (OSError, ValueError, UnicodeDecodeError) as bak_exc:
                logging.error("stats.json.bak also unreadable (%s).", bak_exc)
        # Rename the bad file so it cannot block future writes.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bad = self._path_obj.with_suffix(
            self._path_obj.suffix + f".corrupt-{stamp}")
        try:
            self._path_obj.rename(bad)
            logging.warning("Renamed unreadable stats.json to %s.", bad.name)
        except OSError:
            pass
        self.data = self._empty_data()

    # -- saving --------------------------------------------------------------

    def _write_atomic(self, target: Path) -> None:
        """Serialize ``self.data`` to ``target`` via same-dir tmp + os.replace."""
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync is best-effort; some filesystems report EOPNOTSUPP.
                pass
        os.replace(tmp, target)

    def save(self, backup: bool = True) -> None:
        """Persist the in-memory data.

        With ``backup=True`` (the default) the previous primary is copied to
        ``.bak`` before the swap, so a torn write never loses both. A user
        clearing all history with ``clear_all()`` passes ``backup=False`` so
        the empty state does not resurrect on the next crash recovery.
        """
        self.load()  # make sure we are operating on a loaded view
        with self._lock:
            parent = self._path_obj.parent
            parent.mkdir(parents=True, exist_ok=True)
            if backup and self._path_obj.exists():
                try:
                    shutil.copy2(self._path_obj,
                                 self._path_obj.with_suffix(
                                     self._path_obj.suffix + ".bak"))
                except OSError as exc:
                    # Losing the .bak does not lose the primary; the write
                    # below still succeeds.
                    logging.warning("Could not refresh stats.json.bak: %s", exc)
            self._write_atomic(self._path_obj)
            # Daily archive: snapshot once per calendar day, so a long-lived
            # install has at most one backup file per day, not one per take.
            self._maybe_daily_backup()

    def _maybe_daily_backup(self) -> None:
        try:
            self._daily_obj.mkdir(parents=True, exist_ok=True)
            stamp = _now_dt().strftime("%Y-%m-%d")
            target = self._daily_obj / f"{stamp}.json"
            if not target.exists():
                self._write_atomic(target)
        except OSError as exc:
            logging.warning("Daily stats backup failed: %s", exc)

    # -- session lifecycle ---------------------------------------------------

    def current_session(self) -> Optional[Dict[str, Any]]:
        """Return the most recent session, or None if there is none.

        A session with no ``ended_at`` is considered open; an idle check
        decides whether it should be closed before a new one starts.
        """
        self.load()
        with self._lock:
            sessions = self.data.get("sessions") or []
            if not sessions:
                return None
            return sessions[-1]

    def open_session_if_needed(self) -> Optional[Dict[str, Any]]:
        """Return the session the next take belongs to.

        Three cases, in the order they fire:
          * no sessions yet - create one and return it;
          * an open session that is still inside ``idle_timeout_sec`` of
            its last take - resume it;
          * otherwise - close the previous session (write its ended_at)
            and open a fresh one. This is what makes idle-splitting work:
            a user who idles 30 minutes sees two separate sessions, and
            a crash recovery picks up where the last take left off.
        """
        self.load()
        with self._lock:
            now = _now_dt()
            existing = self.current_session()
            if existing is None:
                return self._new_session()

            ended_at = existing.get("ended_at")
            if ended_at:
                # Previously closed; always start a new session.
                return self._new_session()

            # Idle check: how long since the last take?
            last_take_ts = None
            takes = existing.get("takes") or []
            if takes:
                last_take_ts = _parse_iso(takes[-1].get("timestamp", ""))
            if last_take_ts is not None:
                gap_sec = (now - last_take_ts).total_seconds()
                # Read the timeout from config so a live change in the
                # settings window takes effect on the next take.
                timeout = config.IDLE_TIMEOUT or self.idle_timeout_sec
                if gap_sec > timeout:
                    existing["ended_at"] = _iso(last_take_ts)
                    existing["duration_sec"] = int(
                        (last_take_ts - self._session_start_dt(existing)).total_seconds())
                    existing["idle_splits"] = int(existing.get("idle_splits", 0)) + 1
                    self.save()
                    return self._new_session()
            return existing

    def _new_session(self) -> Dict[str, Any]:
        session = {
            "id": _new_session_id(),
            "started_at": _now_iso(),
            "ended_at": None,
            "duration_sec": 0,
            "idle_splits": 0,
            "tags": ["daily"],
            "totals": {
                "takes": 0,
                "unique_phrases": 0,
                "avg_best_score": 0.0,
                "max_score": 0.0,
                "avg_wpm": 0.0,
            },
            "takes": [],
        }
        self.data.setdefault("sessions", []).append(session)
        return session

    @staticmethod
    def _session_start_dt(session: Dict[str, Any]) -> datetime:
        started = _parse_iso(session.get("started_at", ""))
        if started is not None:
            return started
        # Fallback: use the epoch - duration will read as garbage but the
        # session is still valid; the caller's next save will fix it.
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    def end_current_session(self) -> None:
        """Close the open session with the current wall clock as its end time."""
        self.load()
        with self._lock:
            current = self.current_session()
            if current is None or current.get("ended_at"):
                return
            now = _now_dt()
            started = self._session_start_dt(current)
            current["ended_at"] = _iso(now)
            current["duration_sec"] = int((now - started).total_seconds())
            self.save()

    # -- take recording ------------------------------------------------------

    def record_take(self, phrase: str, score: float, *,
                    audio_duration_sec: float = 0.0,
                    transcript: str = "",
                    error_words: Optional[List[Dict[str, Any]]] = None,
                    phoneme_scores: Optional[List[Dict[str, Any]]] = None,
                    grade: str = "",
                    grade_value: float = -1.0,
                    attempts: int = 1,
                    audio_files: Optional[Dict[str, str]] = None,
                    tags: Optional[List[str]] = None,
                    take_id: Optional[str] = None,
                    ) -> Optional[Dict[str, Any]]:
        """Append one take to the current session and persist.

        This is the primary entry point: ``app.py`` calls it from
        ``on_take_scored`` with the fields it pulls off the engine's
        PronunciationResult. Anything optional defaults to a safe empty
        value so callers that only have a phrase+score still work.

        ``take_id`` is normally auto-generated, but callers that already
        know the id (the audio dump path) can pass it so the audio
        directory and the take record share one name.

        Returns the persisted take dict (with an id and session_id filled
        in) so the caller can read back the take id it is about to
        reference in the UI.
        """
        self.load()
        session = self.open_session_if_needed()
        if session is None:
            logging.warning("record_take called but no session could be opened")
            return None
        with self._lock:
            take = {
                "id": take_id or _new_take_id(),
                "timestamp": _now_iso(),
                "phrase": phrase or "",
                "score": float(score),
                "grade": grade or "",
                "grade_value": float(grade_value) if grade_value else 0.0,
                "attempts": int(attempts),
                "audio_duration_sec": float(audio_duration_sec)
                    if audio_duration_sec else 0.0,
                "transcript": transcript or "",
                "words_per_minute": compute_words_per_minute(
                    transcript or "", audio_duration_sec or 0.0),
                "error_words": list(error_words or []),
                "phoneme_scores": list(phoneme_scores or []),
                "session_id": session["id"],
            }
            if audio_files:
                take["audio_files"] = audio_files
            if tags:
                take["tags"] = list(tags)
            session.setdefault("takes", []).append(take)
            self._recompute_session_totals(session)
            self.save()
            return take

    @staticmethod
    def _recompute_session_totals(session: Dict[str, Any]) -> None:
        """Rebuild ``session['totals']`` from ``session['takes']``."""
        takes = session.get("takes") or []
        if not takes:
            session["totals"] = {
                "takes": 0, "unique_phrases": 0, "avg_best_score": 0.0,
                "max_score": 0.0, "avg_wpm": 0.0,
            }
            return
        phrase_best: Dict[str, float] = {}
        scores: List[float] = []
        wpms: List[float] = []
        for take in takes:
            phrase = (take.get("phrase") or "").strip()
            if not phrase:
                continue
            s = float(take.get("score") or 0.0)
            phrase_best[phrase] = max(phrase_best.get(phrase, 0.0), s)
            scores.append(s)
            w = take.get("words_per_minute")
            if w:
                wpms.append(float(w))
        avg_best = sum(phrase_best.values()) / len(phrase_best)
        session["totals"] = {
            "takes": len(takes),
            "unique_phrases": len(phrase_best),
            "avg_best_score": round(avg_best, 4),
            "max_score": round(max(scores) if scores else 0.0, 4),
            "avg_wpm": round(sum(wpms) / len(wpms) if wpms else 0.0, 2),
        }

    # -- drill sessions ------------------------------------------------------

    def record_drill_session(self, session: Dict[str, Any],
                              ) -> Optional[Dict[str, Any]]:
        """Append one drill session and persist.

        ``session`` is the dict returned by
        :meth:`mimora.drill.engine.DrillEngine.to_storage_dict` - it
        carries ``target``, ``takes``, ``attempts``, ``passed``,
        ``first_score``, ``last_score``, ``avg_score`` and ``outcome``.
        """
        self.load()
        now = _now_dt()
        takes = list(session.get("takes") or [])
        first_score = float(takes[0].get("score") or 0) if takes else 0.0
        last_score = float(takes[-1].get("score") or 0) if takes else 0.0
        avg_score = (round(sum(float(t.get("score") or 0) for t in takes)
                          / len(takes), 2) if takes else 0.0)
        record = {
            "id": f"d-{now.strftime('%Y-%m-%dT%H%M%S')}-{uuid.uuid4().hex[:4]}",
            "target": dict(session.get("target") or {}),
            "started_at": (_iso(takes[0]["timestamp"]) if takes
                           and takes[0].get("timestamp")
                           else _now_iso()),
            "ended_at": _now_iso(),
            "attempts": int(session.get("attempts") or len(takes)),
            "passed": int(session.get("passed") or 0),
            "first_score": first_score,
            "last_score": last_score,
            "avg_score": avg_score,
            "outcome": session.get("outcome") or "unfinished",
            "takes": takes,
        }
        self.data.setdefault("drill_sessions", []).append(record)
        self.save()
        return record

    def record_dictation_session(self, session: Dict[str, Any],
                                 ) -> Optional[Dict[str, Any]]:
        """Append one dictation session and persist.

        ``session`` is the dict returned by
        :meth:`mimora.dictation.engine.DictationEngine.to_storage_dict` - it
        carries ``takes``, ``attempts``, ``passed``, ``first_score``,
        ``last_score``, ``avg_score`` and ``outcome``.
        """
        self.load()
        now = _now_dt()
        takes = list(session.get("takes") or [])
        first_score = float(takes[0].get("score") or 0) if takes else 0.0
        last_score = float(takes[-1].get("score") or 0) if takes else 0.0
        avg_score = (round(sum(float(t.get("score") or 0) for t in takes)
                           / len(takes), 2) if takes else 0.0)
        record = {
            "id": f"dt-{now.strftime('%Y-%m-%dT%H%M%S')}-{uuid.uuid4().hex[:4]}",
            "started_at": (_iso(takes[0]["timestamp"]) if takes
                           and takes[0].get("timestamp")
                           else _now_iso()),
            "ended_at": _now_iso(),
            "attempts": int(session.get("attempts") or len(takes)),
            "passed": int(session.get("passed") or 0),
            "first_score": first_score,
            "last_score": last_score,
            "avg_score": avg_score,
            "outcome": session.get("outcome") or "unfinished",
            "wrong_words": list(session.get("wrong_words") or []),
            "extra_words": list(session.get("extra_words") or []),
            "takes": takes,
        }
        self.data.setdefault("dictation_sessions", []).append(record)
        self.save()
        return record

    def delete_take(self, session_id: str, take_id: str,
                    delete_audio: bool = True) -> bool:
        """Remove one take from a session; recompute that session's totals.

        Returns False (no change) when the ids do not match, so a
        double-delete or a stale UI reference is a no-op rather than an
        error. ``delete_audio`` is currently only a hint to the caller -
        actual wav removal lives in ``recorder_store.delete_take_files``
        and is done in app.py so this module stays free of numpy imports.
        """
        self.load()
        with self._lock:
            for session in self.data.get("sessions") or []:
                if session.get("id") != session_id:
                    continue
                takes = session.get("takes") or []
                for i, take in enumerate(takes):
                    if take.get("id") == take_id:
                        removed = takes.pop(i)
                        self._recompute_session_totals(session)
                        self.save()
                        if delete_audio:
                            self._delete_take_audio(removed)
                        return True
            return False

    def delete_session(self, session_id: str,
                       delete_audio: bool = True) -> bool:
        """Remove an entire session and its audio. See ``delete_take``."""
        self.load()
        with self._lock:
            sessions = self.data.get("sessions") or []
            for i, session in enumerate(sessions):
                if session.get("id") != session_id:
                    continue
                removed = sessions.pop(i)
                if delete_audio:
                    self._delete_session_audio(removed)
                self.save()
                return True
        return False

    def clear_all(self, delete_audio: bool = True) -> None:
        """Wipe every session and drill session.

        A separate method because it is destructive; the UI must confirm
        twice before calling it. ``delete_audio`` still only removes the
        wav/manifest files - the ``config/stats.json`` is replaced with a
        fresh skeleton rather than left in a half-emptied state.
        """
        self.load()
        with self._lock:
            if delete_audio:
                for session in self.data.get("sessions") or []:
                    self._delete_session_audio(session)
                for session in self.data.get("drill_sessions") or []:
                    self._delete_session_audio(session)
                for session in self.data.get("dictation_sessions") or []:
                    self._delete_session_audio(session)
            self.data = self._empty_data()
            self.save(backup=False)

    def _delete_take_audio(self, take: Dict[str, Any]) -> None:
        """Remove the wav files a take references, if any.

        Deliberately swallows errors: a missing audio file is normal
        (metadata-only mode) and a permission error should not surface
        as an exception from a delete button.
        """
        try:
            from mimora.persistence.recorder_store import delete_take_files
            delete_take_files(take)
        except Exception:  # noqa: BLE001 - never block a delete on I/O
            logging.exception("Failed to delete take audio files:")

    def _delete_session_audio(self, session: Dict[str, Any]) -> None:
        session_dir = session.get("session_dir")
        if not session_dir:
            return
        try:
            session_path = Path(session_dir)
            if session_path.exists():
                shutil.rmtree(session_path, ignore_errors=True)
                session.pop("session_dir", None)
        except Exception:  # noqa: BLE001
            logging.exception("Failed to delete session directory:")

    # -- queries -------------------------------------------------------------

    def all_sessions(self) -> List[Dict[str, Any]]:
        """Return every session (including closed ones), newest last."""
        self.load()
        with self._lock:
            return list(self.data.get("sessions") or [])

    def all_takes(self, lookback: Optional[int] = None
                  ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Iterate ``(session, take)`` pairs, oldest first.

        ``lookback`` limits to the last N takes overall (across all
        sessions), which is what the weakness aggregator wants - a fixed
        window of recent history, not a random subset of one session.
        """
        self.load()
        with self._lock:
            result: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            for session in self.data.get("sessions") or []:
                for take in session.get("takes") or []:
                    result.append((session, take))
            if lookback is not None and len(result) > lookback:
                result = result[-lookback:]
            return result

    def all_drill_sessions(self) -> List[Dict[str, Any]]:
        self.load()
        with self._lock:
            return list(self.data.get("drill_sessions") or [])

    def all_dictation_sessions(self) -> List[Dict[str, Any]]:
        self.load()
        with self._lock:
            return list(self.data.get("dictation_sessions") or [])

    # -- export / import -----------------------------------------------------

    def export_to(self, target: Path) -> None:
        """Copy the current stats.json to ``target`` (manual backup)."""
        self.load()
        with self._lock:
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_atomic(target)

    def import_from(self, source: Path) -> None:
        """Replace the current data with the contents of ``source``.

        Used by the settings window's "Restore from backup". The source
        is validated before it is swapped in, so a corrupt backup does
        not take down the current data.
        """
        source = Path(source)
        with open(source, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{source.name} is not a JSON object")
        self.load()
        with self._lock:
            self.data = data
            self._write_atomic(self._path_obj)
