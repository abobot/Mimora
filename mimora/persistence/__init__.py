# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Persistent history and stats storage.

Everything that survives across app restarts lives here: the aggregate stats
file (config/stats.json), per-take audio recordings (logs/records/<session>/),
and the versioned migration machinery that turns an older stats.json into the
current schema on first read.

Only this package talks to disk for user history. ``mimora/session.py`` stays
pure in-memory tally (see its docstring); the controller (mimora/app.py) owns
one ``StatsStore`` and mirrors every scored take into it.
"""

from mimora.persistence.stats import StatsStore
from mimora.persistence.recorder_store import (
    dump_take_audio,
    dump_take_meta,
    delete_take_files,
    resolve_take_audio,
)

__all__ = [
    "StatsStore",
    "dump_take_audio",
    "dump_take_meta",
    "delete_take_files",
    "resolve_take_audio",
]
