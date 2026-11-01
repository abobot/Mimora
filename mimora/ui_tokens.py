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

import re

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
    "score.near_miss":  THEME["warn"],          # calibrated-bucket near-miss (amber)
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


# WCAG 2.x relative luminance of a "#rrggbb" colour (0.0 = black, 1.0 = white).
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


def _relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a ``#rrggbb`` colour string."""
    match = _HEX_RE.match(color)
    if not match:
        raise ValueError(f"not a #rrggbb colour: {color!r}")
    value = int(match.group(1), 16)
    channel = []
    for shift in (16, 8, 0):
        c = (value >> shift) & 0xFF
        c = c / 255.0
        # Linearize the sRGB channel (WCAG 2.x §1.4.3).
        channel.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channel[0] + 0.7152 * channel[1] + 0.0722 * channel[2]


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio (1.0..21.0) between two ``#rrggbb`` colours."""
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# Text colours that must pass WCAG AA (4.5:1) against the surfaces they can sit
# on. The design doc called out text_muted on bg_main specifically; text_dim and
# text_accent are the other two low-contrast-risk body colours. Each is checked
# against every surface it is actually used on, so the list is deliberately
# small and auditable rather than "all fg x all bg".
_CONTRAST_CHECKS = (
    ("text_muted", ("bg_main", "bg_panel", "bg_card")),
    ("text_dim", ("bg_main", "bg_panel", "bg_card")),
    ("text_accent", ("bg_accent", "bg_accent_active", "bg_button")),
)


def assert_contrast(min_ratio: float = 4.5) -> list[str]:
    """Check the contrast-sensitive text/surface pairs, return the failures.

    Called at startup (and by the theme tests) so a theme whose muted text
    drops below WCAG AA is caught in the run, not discovered by a user. A
    theme is allowed to fail - the light/cream themes trade contrast for warmth
    on purpose - so this returns the list of ``"fg on bg: ratio"`` failures
    instead of raising; the caller decides how loud to be (a warning log at
    startup, an assertion in the tests).
    """
    failures = []
    for fg_key, surfaces in _CONTRAST_CHECKS:
        fg = THEME[fg_key]
        for surface_key in surfaces:
            bg = THEME[surface_key]
            ratio = contrast_ratio(fg, bg)
            if ratio < min_ratio:
                failures.append(f"{fg_key} on {surface_key}: {ratio:.2f}")
    return failures
