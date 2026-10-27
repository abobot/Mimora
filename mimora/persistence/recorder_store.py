# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Per-take audio storage: dump a recording under a session's directory.

Replaces the previous fixed-name ``logs/records/*.wav`` layout: each take
now lives in ``logs/records/<session_id>/<HHMMSS>-<hex>/`` with ``raw.wav``
and ``normalized.wav`` alongside a small ``meta.json``. That makes history
browser's "play this take" trivial (the directory is already known from the
take record) and lets the auto-cleanup policy delete a whole session with
one ``rmtree``.

Only writes to disk when ``config.HISTORY_MODE == "with_audio"``. The
metadata path is always available - it is what the aggregate stats are
built from - but this module only touches the wav files.
"""

from __future__ import annotations

import json
import logging
import os
import time
import wave
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from mimora import config
from mimora import paths


def _records_root() -> Path:
    """Directory that holds one subdirectory per session."""
    return Path(config.RECORDS_DIR)


def _session_dir(session_id: str) -> Path:
    return _records_root() / session_id


def _take_dir(session_id: str, take_id: str) -> Path:
    """One subdirectory per take. ``take_id`` is a safe path segment here
    (uuid hex + ISO timestamp), but we still guard against a caller passing
    something with path separators."""
    safe = "".join(c for c in str(take_id) if c not in "\\/:*?\"<>|")
    if not safe:
        safe = f"take-{int(time.time())}"
    return _session_dir(session_id) / safe


def resolve_take_audio(session_id: str, take_id: str) -> Optional[Path]:
    """Return the normalized wav for one take, or None if not present.

    Prefers ``normalized.wav`` because it is the exact signal that was
    scored. Falls back to ``raw.wav`` when normalization was skipped
    (e.g. silent take). Returns None when HISTORY_MODE is not
    ``with_audio`` so callers don't have to remember the check.
    """
    if config.HISTORY_MODE != "with_audio":
        return None
    take_path = _take_dir(session_id, take_id)
    for name in ("normalized.wav", "raw.wav"):
        candidate = take_path / name
        if candidate.is_file():
            return candidate
    return None


def dump_take_audio(session_id: str, take_id: str,
                    audio_raw: Optional[np.ndarray],
                    audio_normalized: Optional[np.ndarray],
                    sample_rate: int,
                    ) -> Optional[Dict[str, str]]:
    """Write the raw and normalized wav for one take.

    Returns ``{"raw": "raw.wav", "normalized": "normalized.wav"}`` (relative
    to the take directory) on success, or ``None`` when ``HISTORY_MODE`` is
    not ``with_audio``. Failures are logged and also return None - a
    persistence problem must not interrupt the practice loop.
    """
    if config.HISTORY_MODE != "with_audio":
        return None

    take_path = _take_dir(session_id, take_id)
    try:
        take_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.error("Cannot create take directory %s: %s", take_path, exc)
        return None

    written: Dict[str, str] = {}
    if audio_raw is not None and len(audio_raw) > 0:
        if _write_wav(take_path / "raw.wav", audio_raw, sample_rate):
            written["raw"] = "raw.wav"
    if audio_normalized is not None and len(audio_normalized) > 0:
        if _write_wav(take_path / "normalized.wav", audio_normalized, sample_rate):
            written["normalized"] = "normalized.wav"
    return written or None


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> bool:
    """Write one mono float32 array as 16-bit PCM. Returns True on success."""
    try:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return True
    except OSError as exc:
        logging.error("Could not write %s: %s", path, exc)
        return False


def dump_take_meta(take: Dict[str, Any], session_id: str) -> Optional[Path]:
    """Write a take's ``meta.json`` alongside its audio.

    Called even when ``HISTORY_MODE == "metadata_only"`` because the file
    is the source of truth for the history browser. Skipped only for
    ``HISTORY_MODE == "none"``.

    Returns the meta file path, or None if the write failed.
    """
    if config.HISTORY_MODE == "none":
        return None
    # In metadata_only mode there is no take directory; still write the
    # meta file, but nest it under ``metadata/`` so a later switch to
    # with_audio does not collide.
    if config.HISTORY_MODE == "metadata_only":
        session_dir = _session_dir(session_id)
        subdir = session_dir / "metadata"
    else:
        subdir = _take_dir(session_id, take.get("id", "unknown"))
    try:
        subdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.error("Cannot create meta directory %s: %s", subdir, exc)
        return None
    target = subdir / "meta.json"
    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(take, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return target
    except (OSError, TypeError, ValueError) as exc:
        logging.error("Could not write meta.json to %s: %s", target, exc)
        return None


def delete_take_files(take: Dict[str, Any]) -> int:
    """Remove a take's wav files. Returns the number of files removed.

    Reads the take's ``audio_files`` mapping (relative names) and deletes
    each one that exists. ``metadata_only`` and ``none`` modes have no
    wav to delete, so this is a no-op for most of the user base.
    """
    if config.HISTORY_MODE != "with_audio":
        return 0
    session_id = take.get("session_id")
    take_id = take.get("id")
    if not session_id or not take_id:
        return 0
    take_path = _take_dir(session_id, take_id)
    removed = 0
    for name in ("raw.wav", "normalized.wav", "meta.json"):
        target = take_path / name
        try:
            if target.exists():
                target.unlink()
                removed += 1
        except OSError as exc:
            logging.warning("Could not remove %s: %s", target, exc)
    # Remove the take directory if it is now empty (best-effort).
    try:
        if take_path.exists() and not any(take_path.iterdir()):
            take_path.rmdir()
    except OSError:
        pass
    return removed


def session_storage_bytes(session_id: str) -> int:
    """Total bytes on disk for one session's records. Used by the storage
    panel; returns 0 for sessions that were not saved with audio."""
    root = _session_dir(session_id)
    if not root.exists():
        return 0
    total = 0
    try:
        for child in root.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
    except OSError:
        pass
    return total
