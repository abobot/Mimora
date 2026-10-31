# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Semantic design tokens for the Mimora UI.

Every panel reads colours through this module - never through a per-panel dict
of raw ``THEME`` keys - so that one colour *meaning* maps to one colour
everywhere. Before this module existed the same meaning was re-interpreted five
times (``_status_color`` in history/weakness, ``_LEVEL_COLORS`` in
hero/drill/dictation, ``_bucket_quality``/``_quality_label`` in ui.py), and the
interpretations disagreed: a 72 score read green in one panel and amber in
another. A light-theme change could never reconcile them.

Importing this module must stay free of view-module imports (like ui_theme.py
it is panel-agnostic); it only reads the resolved palette and never touches Tk.
"""

from mimora.ui_theme import THEME

# Semantic colours, keyed by *meaning* not by colour name. Adding a meaning
# here (and using it) is the only way a new colour semantic enters the UI.
SEMANTIC = {
    "action.primary":   THEME["accent"],        # primary action (record, next)
    "action.secondary": THEME["bg_accent"],     # secondary action (replay, drill)
    "action.destructive": THEME["bad"],         # delete, clear
    "state.recording":  THEME["bad"],           # recording in progress
    "state.processing": THEME["warn"],          # analysing
    "state.ready":      THEME["ready"],         # ready for input
    "score.excellent":  THEME["good"],          # top band of a score
    "score.ok":         THEME["text_emph"],     # mid-high band (passable)
    "score.mid":        THEME["text_dim"],      # mid-low band (needs work)
    "score.weak":       THEME["bad"],           # low band (weak)
    "surface.page":     THEME["bg_main"],       # window background
    "surface.panel":    THEME["bg_panel"],      # panel background
    "surface.card":     THEME["bg_card"],       # card background
}


def score_color(value: float, scale: float = 100.0) -> str:
    """Single entry point: a 0..``scale`` score -> its semantic colour.

    Every panel that colours a *numeric* score must call this, so the bands
    can never drift apart. The bucket-based phoneme path is deliberately NOT
    routed here (see ``ui.py._bucket_quality``): a 0-5 bucket is keyed to the
    engine's pass bucket (>= 4 must read green to match ``result.passed``),
    which a percentage band cannot express.
    """
    pct = value / scale
    if pct >= 0.85:
        return SEMANTIC["score.excellent"]
    if pct >= 0.70:
        return SEMANTIC["score.ok"]
    if pct >= 0.50:
        return SEMANTIC["score.mid"]
    return SEMANTIC["score.weak"]


# Per-word quality level -> colour, the traffic light behind a word cell in the
# hero / drill / dictation feedback strips. Replaces the three per-panel
# ``_LEVEL_COLORS`` dicts (and dictation's ``_STATE_COLORS``, whose states
# normalise to these levels).
_WORD_LEVEL_COLORS = {
    "good": THEME["good"],
    "ok": THEME["text_dim"],
    "bad": THEME["bad"],
}


def level_color(level: str) -> str:
    """Colour for a per-word quality level (``good`` / ``ok`` / ``bad``)."""
    return _WORD_LEVEL_COLORS.get(level, THEME["text_dim"])


# Weakness-panel aggregation status -> colour (the one status mapping that is
# not score-based).
_WEAKNESS_STATUS_COLORS = {
    "need_practice": THEME["bad"],
    "watch": THEME["warn"] if "warn" in THEME else THEME["text_dim"],
    "improving": THEME["good"],
}


def status_color(status: str) -> str:
    """Colour for a weakness-panel aggregation status."""
    return _WEAKNESS_STATUS_COLORS.get(status, THEME["text_dim"])
