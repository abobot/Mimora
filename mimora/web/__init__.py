# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Web companion for Mimora.

Serves the same pronunciation practice loop over HTTP so the trainer can be
used from a browser. The heavy lifting stays in the existing GUI-agnostic
modules (``mimora/engine.py``, ``mimora/tts.py``, ``mimora/llm.py``,
``mimora/persistence/stats.py``); this package only adds the HTTP surface and
a thin runtime that wires them together the way ``app.py`` does.

The frontend records audio in the browser (``getUserMedia``) and uploads the
WAV to the backend for scoring - the browser owns the microphone, the backend
owns the models. Run with ``mimora-web`` or ``python -m mimora.web.server``.
"""
