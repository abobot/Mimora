"""Shared, pure date/time helpers for the analysis and stats modules.

The stats dashboard and the ``*_analysis`` modules each used to parse ISO
timestamps with their own copy of the same logic (``_iso_date`` + ``_week_start``
in :mod:`mimora.drill_analysis` and :mod:`mimora.dictation_analysis`, a slicing
``ts[:10]`` in :mod:`mimora.advance_analysis`, ``strftime`` in
:mod:`mimora.ui_stats`, and ``fromisoformat`` + timezone juggling in
:mod:`mimora.ui_weakness``). Five copies of one rule is how cross-tab date
keys silently disagree (a non-zero-padded ``"2026-1-5"`` from a raw slice does
not match the ``"2026-01-05"`` a parser produces), so the logic lives here and
everyone calls the same functions.

Pure standard library only - no Tk, no engine, no ``config``. Tested by
``tests/test_time_utils.py``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional


def iso_date(ts: str) -> Optional[str]:
    """The ``YYYY-MM-DD`` date of an ISO timestamp (None if unparseable).

    Accepts both a full timestamp (``2026-11-01T21:00:00+08:00``) and a bare
    date (``2026-11-01``), so callers never have to know which they hold. A
    parsed timestamp is normalised to its zero-padded date - a raw ``ts[:10]``
    slice is NOT equivalent and is the bug this module exists to prevent.
    """
    text = (ts or "").strip()
    if not text:
        return None
    try:
        # ISO with a +08:00 offset parses directly; tolerate a bare date.
        if "T" in text or "+" in text:
            parsed = _dt.datetime.fromisoformat(text)
        else:
            parsed = _dt.date.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, _dt.datetime):
        return parsed.date().isoformat()
    return parsed.isoformat()


def week_start(iso_date: str) -> str:
    """The Monday of the week containing ``iso_date`` (ISO date string)."""
    d = _dt.date.fromisoformat(iso_date)
    monday = d - _dt.timedelta(days=d.weekday())
    return monday.isoformat()


def parse_ts(ts: str) -> Optional[_dt.datetime]:
    """A timezone-aware ``datetime`` from an ISO timestamp.

    Returns ``None`` for an empty/unparseable value. A naive timestamp (no
    offset) is assumed to be local time and gets the local tz, so callers can
    compare it directly against ``datetime.now(tz=local)`` without a second
    ``tzinfo`` juggle - the rule :mod:`mimora.ui_weakness` used to duplicate.
    """
    if not (ts or "").strip():
        return None
    try:
        parsed = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return parsed
