# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Weakness panel: word / phoneme / speed summary.

A Toplevel opened from a future "Weakness" button. Reads every take
from :class:`StatsStore` and shows three tabs:

* Overview - the top suggestions the :func:`generate_suggestions`
  rule engine produced (phonemes first, then weak words, then
  speed advice).
* Words - the full error-word leader board, sorted by status and
  error rate. Each row has a "Start drill" button.
* Phonemes - a heatmap of every recorded IPA. Each cell has the
  ZH-interference priority, an example word and a Chinese hint
  (only for phonemes the user's native language tends to trip on).

The view is passive: it calls ``load_takes()`` on the controller for
the raw take list and ``start_drill(target)`` to hand off a
suggestion to the Drill engine.
"""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, Dict, List

from mimora import config, window_icon
from mimora.analysis import (
    ZH_INTERFERENCE,
    aggregate_error_words,
    aggregate_phoneme_scores,
    generate_suggestions,
    hint_for,
    example_word_for,
    sorted_by_error_rate,
    sorted_by_weakness,
)
from mimora.ui_stats import _CanvasChart
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_FAMILY_MONO,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
)
import ttkbootstrap as ttk


@dataclass(frozen=True)
class WeaknessCallbacks:
    """Typed view→controller contract for the weakness panel."""
    load_takes: Callable[[], List[Any]]
    start_drill: Callable[[Dict[str, str]], None]
    open_practice: Callable[[], None]


def _status_color(status: str) -> str:
    return {
        "need_practice": THEME["bad"],
        "watch": THEME["warn"] if "warn" in THEME else THEME["text_dim"],
        "improving": THEME["good"],
    }.get(status, THEME["text_dim"])


def _status_label(status: str) -> str:
    return {
        "need_practice": "🔴 需练习",
        "watch": "🟡 关注",
        "improving": "🟢 改善中",
    }.get(status, "  ")


class WeaknessView:
    """Toplevel with tabs for overview / words / phonemes."""

    def __init__(self, root, callbacks: WeaknessCallbacks,
                 native_language: str = "zh"):
        self.root = root
        self._cb = callbacks
        self._native = native_language or "zh"
        self._window = tk.Toplevel(root)
        self._window.title("Pronunciation weakness")
        self._window.geometry("900x640")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)
        self._build_ui()
        # Populate the tabs immediately. The <FocusIn> bindings below are meant
        # to refresh when a tab is first shown, but a ttk.Notebook tab frame
        # does not receive FocusIn on tab switch (focus stays on the notebook
        # itself), so without this initial call the panel would stay empty.
        self.refresh()

    def exists(self) -> bool:
        try:
            return self._window.winfo_exists()
        except tk.TclError:
            return False

    def _build_ui(self) -> None:
        header = tk.Frame(self._window, bg=THEME["bg_main"])
        header.pack(fill=tk.X, padx=16, pady=10)
        tk.Label(header, text="Pronunciation weakness",
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        ttk.Button(header, text="Close",
                   command=self._window.destroy).pack(side=tk.RIGHT)

        self._nb = ttk.Notebook(self._window)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))
        self._tab_overview = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_words = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_phonemes = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._nb.add(self._tab_overview, text="Overview")
        self._nb.add(self._tab_words, text="Words")
        self._nb.add(self._tab_phonemes, text="Phonemes")

        # Refresh is triggered when the tab is first shown or when the
        # user hits Refresh.
        for tab in (self._tab_overview, self._tab_words, self._tab_phonemes):
            tab.bind("<FocusIn>", lambda _e: self.refresh())
        ttk.Button(header, text="Refresh",
                   command=self.refresh).pack(side=tk.LEFT, padx=8)

    # ------------------------------------------------------------------
    # Data + rendering
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        (sessions, raw_takes) = self._cb.load_takes()
        # raw_takes is [(session, take), ...] - pass tuples directly to
        # the aggregation functions, which expect that format.
        word_agg = aggregate_error_words(raw_takes)
        phoneme_agg = aggregate_phoneme_scores(raw_takes, native_language=self._native)
        # Recent WPM for speed advice - extract just the take dicts.
        takes = [t for _, t in raw_takes] if raw_takes and isinstance(raw_takes[0], tuple) else raw_takes
        recent = takes[-30:] if takes else []
        wpms = [float(t.get("words_per_minute") or 0) for t in recent]
        avg_wpm = sum(wpms) / len(wpms) if wpms else 0.0
        suggestions = generate_suggestions(word_agg, phoneme_agg,
                                           recent_wpm=avg_wpm,
                                           native_language=self._native)
        self._render_overview(suggestions)
        self._render_words(word_agg)
        self._render_phonemes(phoneme_agg)

    def _render_overview(self, suggestions: List[Dict[str, Any]]) -> None:
        for child in self._tab_overview.winfo_children():
            child.destroy()
        tk.Label(self._tab_overview,
                 text="Suggested next steps (based on your last 30 takes)",
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(anchor="w", pady=(4, 8))
        if not suggestions:
            tk.Label(self._tab_overview,
                     text="No obvious weaknesses. Keep practicing!",
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        for s in suggestions[:8]:
            card = tk.Frame(self._tab_overview, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1)
            card.pack(fill=tk.X, pady=4)
            tk.Label(card, text=s["title"],
                     font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(anchor="w", padx=10, pady=(8, 0))
            tk.Label(card, text=s["body"], justify=tk.LEFT,
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text"]
                     ).pack(anchor="w", padx=10, pady=(0, 8))
            ttk.Button(card, text="Start drill →",
                       command=lambda t=s["target"]: self._start_drill(t)
                       ).pack(anchor="e", padx=10, pady=(0, 6))

    def _render_words(self, word_agg: Dict[str, Dict[str, Any]]) -> None:
        for child in self._tab_words.winfo_children():
            child.destroy()
        if not word_agg:
            tk.Label(self._tab_words,
                     text="No word-level data yet. Take some practice first.",
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        for word, entry in sorted_by_error_rate(word_agg):
            row = tk.Frame(self._tab_words, bg=THEME["bg_panel"], bd=0)
            row.pack(fill=tk.X, pady=2)
            status = entry.get("status", "")
            tk.Label(row, text=_status_label(status),
                     fg=_status_color(status), bg=THEME["bg_panel"],
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     width=10, anchor="w").pack(side=tk.LEFT, padx=6)
            tk.Label(row, text=word, font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(side=tk.LEFT, padx=(0, 12))
            # Error types.
            etype = ", ".join(sorted(entry.get("error_types", {}).keys()))
            tk.Label(row, text=f"{entry.get('count', 0)}×  err "
                               f"{entry.get('error_rate', 0):.0%}  {etype}",
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_dim"]
                     ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="Drill",
                       command=lambda w=word:
                       self._start_drill({"type": "word", "value": w})
                       ).pack(side=tk.RIGHT, padx=6)

    def _render_phonemes(self, phoneme_agg: Dict[str, Dict[str, Any]]) -> None:
        for child in self._tab_phonemes.winfo_children():
            child.destroy()
        if not phoneme_agg:
            tk.Label(self._tab_phonemes,
                     text="No phoneme-level data yet. The phoneme engine "
                          "provides this automatically.",
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        # Header.
        tk.Label(self._tab_phonemes,
                 text=f"Issued phonemes (native: {self._native})",
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(anchor="w", pady=(4, 6))
        # Grid: 5 columns.
        grid = tk.Frame(self._tab_phonemes, bg=THEME["bg_main"])
        grid.pack(fill=tk.X)
        for i, (ipa, entry) in enumerate(sorted_by_weakness(phoneme_agg)):
            card = tk.Frame(grid, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1, width=170)
            card.grid(row=i // 5, column=i % 5, sticky="nsew", padx=4, pady=4)
            severity = entry["severity_avg"]
            # Color: red for bad, dim for neutral, green for good.
            color = THEME["bad"] if severity > 0.5 else \
                    (THEME["warn"] if severity > 0.25 else THEME["good"])
            tk.Label(card, text=f"/{ipa}/",
                     font=(FONT_FAMILY_MONO, 20, "bold"),
                     bg=THEME["bg_panel"], fg=color,
                     ).pack(pady=(8, 0))
            tk.Label(card, text=f"{entry.get('count', 0)}×  score "
                                f"{entry.get('avg_score', 0):.0%}",
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_dim"]
                     ).pack(pady=(0, 4))
            hint = hint_for(ipa)
            example = example_word_for(ipa)
            tip_text = f"例词: {example}" if example else ""
            if hint:
                tip_text = (f"{tip_text}  {hint}"
                            if tip_text else hint)
            if tip_text:
                tk.Label(card, text=tip_text, wraplength=150, justify=tk.LEFT,
                         font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                         bg=THEME["bg_panel"], fg=THEME["text_emph"]
                         ).pack(pady=(0, 4))
            ttk.Button(card, text="Drill",
                       command=lambda i=ipa:
                       self._start_drill({"type": "phoneme", "value": i})
                       ).pack(pady=(0, 6))
            for c in range(5):
                grid.columnconfigure(c, weight=1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _start_drill(self, target: Dict[str, str]) -> None:
        logging.info("Weakness: _start_drill target=%r", target)
        self._cb.start_drill(target)
        self._window.destroy()
