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

import datetime
import logging
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional, Tuple

from mimora import config, i18n, window_icon
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
from mimora.phoneme_examples import example_for
from mimora.ui_stats import _CanvasChart
from mimora.ui_tokens import status_color
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
    # Speak a phoneme's example word (P2#8 interactive IPA chart): synthesizes
    # the word and plays it at normal speed, like the WORK ON badge click. The
    # word is looked up per-language by the view; the controller only plays it.
    play_example: Callable[[str], None] = None


def _status_color(status: str) -> str:
    """Verdict colour for an aggregation status, via the shared token map."""
    return status_color(status)


def _status_label(status: str) -> str:
    return {
        "need_practice": "🔴 " + i18n.tr("Need practice"),
        "watch": "🟡 " + i18n.tr("Watch"),
        "improving": "🟢 " + i18n.tr("Improving"),
    }.get(status, "  ")


# Statistics range choices shown in the weakness panel. Each is either a
# time window in days (``days``) or a fixed count of recent takes (``count``),
# and the label is localized at render time. The order here is the dropdown
# order: time windows first (newest data most relevant), then take counts.
RANGE_OPTIONS = (
    ("last_7d", "Last 7 days", {"days": 7}),
    ("last_14d", "Last 14 days", {"days": 14}),
    ("last_30d", "Last 30 days", {"days": 30}),
    ("last_60d", "Last 60 days", {"days": 60}),
    ("last_30", "Last 30 takes", {"count": 30}),
    ("last_100", "Last 100 takes", {"count": 100}),
    ("last_300", "Last 300 takes", {"count": 300}),
)
DEFAULT_RANGE = "last_30d"


def _filter_takes_by_range(raw_takes: List[Any], range_key: str
                           ) -> List[Any]:
    """Slice the raw ``[(session, take), ...]`` list to the chosen range.

    ``days`` windows are applied by the take's ISO timestamp (newer than
    ``now - days``); ``count`` windows take the last N takes. An unknown or
    empty range key returns the list unchanged.
    """
    spec = next((s for k, _l, s in RANGE_OPTIONS if k == range_key), None)
    if not spec:
        return raw_takes
    days = spec.get("days")
    if days is not None:
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=days)
        out = []
        for item in raw_takes:
            take = item[1] if isinstance(item, tuple) and len(item) == 2 else item
            ts = take.get("timestamp")
            try:
                if ts:
                    parsed = datetime.datetime.fromisoformat(ts)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(
                            tzinfo=datetime.datetime.now().astimezone().tzinfo)
                    if parsed >= cutoff:
                        out.append(item)
            except (TypeError, ValueError):
                continue
        return out
    count = spec.get("count")
    if count is not None:
        return raw_takes[-count:]
    return raw_takes


class WeaknessView:
    """Toplevel with tabs for overview / words / phonemes."""

    def __init__(self, root, callbacks: WeaknessCallbacks,
                 native_language: str = "zh"):
        self.root = root
        self._cb = callbacks
        self._native = native_language or "zh"
        self._range_key = DEFAULT_RANGE
        self._window = tk.Toplevel(root)
        self._window.title(i18n.tr("Pronunciation weakness"))
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
        tk.Label(header, text=i18n.tr("Pronunciation weakness"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        ttk.Button(header, text=i18n.tr("Close"),
                   command=self._window.destroy).pack(side=tk.RIGHT)

        # Statistics range selector (near 7d/14d/30d/60d, or last 30/100/300
        # takes). Changing it re-runs refresh() so every tab re-aggregates
        # over the chosen window. Seven options in a dropdown are not explicit
        # (the design doc: "7 个选项用下拉不明示"), so a segmented button row
        # replaces the Combobox: every window is visible at once and one click
        # selects it. The active segment is highlighted.
        tk.Label(header, text=i18n.tr("Range:"),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(side=tk.LEFT, padx=(8, 2))
        self._range_seg = tk.Frame(header, bg=THEME["bg_main"])
        self._range_seg.pack(side=tk.LEFT)
        self._range_buttons: Dict[str, tk.Label] = {}
        for key, label, _spec in RANGE_OPTIONS:
            btn = tk.Label(self._range_seg, text=i18n.tr(label),
                           font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                           padx=6, pady=1, cursor="hand2")
            btn.pack(side=tk.LEFT, padx=1)
            btn.bind("<Button-1>", lambda e, k=key: self._on_segmented(k))
            self._range_buttons[key] = btn
        self._paint_segmented()

        self._nb = ttk.Notebook(self._window)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))
        self._tab_overview = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_words = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_phonemes = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._nb.add(self._tab_overview, text=i18n.tr("Overview"))
        self._nb.add(self._tab_words, text=i18n.tr("Words"))
        self._nb.add(self._tab_phonemes, text=i18n.tr("Phonemes"))

        # The tabs are populated once on open (__init__ calls self.refresh()).
        # There is deliberately NO <FocusIn> binding here: a ttk.Notebook tab
        # frame receives FocusIn when a CHILD BUTTON inside it is clicked, and
        # refresh() destroys all children — including the very button being
        # clicked — between ButtonPress and ButtonRelease. That leaves the
        # release targeting a destroyed widget, so the button command never
        # fires and the click appears to do nothing. Refresh is a manual
        # action (the Refresh button) because the data only changes while
        # practicing, which happens in the main window, not here.
        ttk.Button(header, text=i18n.tr("Refresh"),
                   command=self.refresh).pack(side=tk.LEFT, padx=8)

    # ------------------------------------------------------------------
    # Data + rendering
    # ------------------------------------------------------------------

    def _on_segmented(self, key: str) -> None:
        """Apply the chosen statistics range and re-render every tab."""
        if key == self._range_key:
            return
        self._range_key = key
        self._paint_segmented()
        self.refresh()

    def _paint_segmented(self) -> None:
        """Highlight the active range segment, dim the others.

        The selected segment gets the accent color (bg_accent/text_accent);
        the rest stay text_dim on bg_main. Repainting just configure()s the
        existing labels - no destruction, so the row never flickers.
        """
        for key, btn in self._range_buttons.items():
            if key == self._range_key:
                btn.configure(bg=THEME["bg_accent"], fg=THEME["text_accent"])
            else:
                btn.configure(bg=THEME["bg_main"], fg=THEME["text_dim"])

    def refresh(self) -> None:
        (sessions, raw_takes) = self._cb.load_takes()
        # Slice to the chosen statistics window before aggregating.
        raw_takes = _filter_takes_by_range(raw_takes, self._range_key)
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
        # The title names the active statistics window so the user can see
        # the range dropdown did change the data they are looking at.
        range_label = next(
            (i18n.tr(l) for k, l, _s in RANGE_OPTIONS if k == self._range_key),
            i18n.tr("your last 30 takes"))
        tk.Label(self._tab_overview,
                 text=i18n.tr("Suggested next steps (based on {range})",
                              range=range_label),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(anchor="w", pady=(4, 8))
        if not suggestions:
            tk.Label(self._tab_overview,
                     text=i18n.tr("No obvious weaknesses. Keep practicing!"),
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        # The design doc: Overview shows at most 3 cards, not 8. The rule
        # engine returns suggestions already ranked worst-first; three is the
        # attention budget the user can act on before the panel scrolls, and
        # the full list stays reachable in the Words/Phonemes tabs.
        for s in suggestions[:3]:
            card = tk.Frame(self._tab_overview, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1)
            card.pack(fill=tk.X, pady=4)
            tk.Label(card, text=i18n.tr(s["title"],
                                        **s.get("title_params", {})),
                     font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(anchor="w", padx=10, pady=(8, 0))
            tk.Label(card, text=i18n.tr(s["body"],
                                        **s.get("body_params", {})),
                     justify=tk.LEFT,
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text"]
                     ).pack(anchor="w", padx=10, pady=(0, 8))
            extra = s.get("body_extra")
            if extra:
                tk.Label(card, text=i18n.tr(extra["template"],
                                            **extra.get("params", {})),
                         justify=tk.LEFT,
                         font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                         bg=THEME["bg_panel"], fg=THEME["text"]
                         ).pack(anchor="w", padx=10, pady=(0, 8))
            tgt = s["target"]
            # Left-click drills the sentence context (word → word drill pulls
            # sentences; phoneme → phoneme drill). Right-click drills the word
            # alone when the suggestion names a word, else falls back to left.
            word_t = (self._word_only_target(tgt["value"])
                      if tgt.get("type") == "word" else None)
            self._make_drill_button(
                card, i18n.tr("Start drill →"), tgt, word_t
            ).pack(anchor="e", padx=10, pady=(0, 6))

    def _render_words(self, word_agg: Dict[str, Dict[str, Any]]) -> None:
        for child in self._tab_words.winfo_children():
            child.destroy()
        if not word_agg:
            tk.Label(self._tab_words,
                     text=i18n.tr("No word-level data yet. Take some practice first."),
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        # A Treeview replaces the destroy-and-rebuild row stack: it supports
        # incremental updates, column sorting and keyboard selection, and it
        # does not re-create widgets on every refresh (the old code rebuilt a
        # Frame per word, which is what made the "start drill" buttons lose
        # their click on refresh).
        self._words_data = list(sorted_by_error_rate(word_agg))
        self._words_sort_key = "error_rate"
        self._words_sort_desc = True
        tree = ttk.Treeview(
            self._tab_words,
            columns=("status", "word", "rate", "count", "action"),
            show="headings", selectmode="browse",
        )
        tree.heading("status", text=i18n.tr("Status"),
                     command=lambda: self._sort_words("status"))
        tree.heading("word", text=i18n.tr("Word"),
                     command=lambda: self._sort_words("word"))
        tree.heading("rate", text=i18n.tr("Error rate"),
                     command=lambda: self._sort_words("rate"))
        tree.heading("count", text=i18n.tr("Times"),
                     command=lambda: self._sort_words("count"))
        tree.heading("action", text=i18n.tr("Action"))
        tree.column("status", width=130, anchor="w", stretch=False)
        tree.column("word", width=150, anchor="w")
        tree.column("rate", width=80, anchor="e", stretch=False)
        tree.column("count", width=60, anchor="e", stretch=False)
        tree.column("action", width=70, anchor="center", stretch=False)
        tree.pack(fill=tk.BOTH, expand=True)
        # Rows are re-inserted in full on each refresh (the aggregation is the
        # expensive part, not the widget update); the win over the old code is
        # that the widget itself is not destroyed, so selection and the sort
        # order survive a refresh, and a sorted column can be toggled.
        self._insert_word_rows(tree)
        # Left-click on the Action cell drills the sentence context (a word
        # drill pulls phrases containing it); right-click anywhere on a row
        # drills the word in isolation; double-click is the same as a click on
        # Action. A single click elsewhere only selects (browse mode), so the
        # row can be examined before acting.
        tree.bind("<Button-1>", self._on_word_tree_click)
        tree.bind("<Double-1>", self._on_word_tree_double)
        tree.bind("<Button-3>", self._on_word_tree_right)
        self._word_tree = tree
        # A small caption below the table keeps the two drill behaviours
        # discoverable (the original buttons said only "Drill").
        tk.Label(self._tab_words,
                 text=i18n.tr("Click the action to drill sentences, right-click "
                              "a row to drill the word alone."),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_muted"]
                 ).pack(anchor="w", pady=(4, 6))

    def _insert_word_rows(self, tree: ttk.Treeview) -> None:
        """(Re)fill the Treeview with one row per aggregated word."""
        tree.delete(*tree.get_children())
        for word, entry in self._words_data:
            status = entry.get("status", "")
            etype = ", ".join(sorted(entry.get("error_types", {}).keys()))
            tree.insert(
                "", "end", iid=word,
                values=(
                    _status_label(status),
                    word,
                    f"{entry.get('error_rate', 0):.0%}",
                    f"{entry.get('count', 0)}×",
                    i18n.tr("Drill →"),
                ),
                tags=(status,),
            )

    def _sort_words(self, key: str) -> None:
        """Sort the word rows by the clicked heading (toggle direction)."""
        if key == self._words_sort_key:
            self._words_sort_desc = not self._words_sort_desc
        else:
            self._words_sort_key = key
            # Error rate reads best worst-first; the text columns read
            # alphabetically. Pick a sensible initial direction per column.
            self._words_sort_desc = key in ("rate", "count")
        rank = {"need_practice": 0, "watch": 1, "improving": 2, "other": 3}
        if key == "status":
            self._words_data.sort(
                key=lambda w: rank.get(w[1].get("status", "other"), 3))
        elif key == "word":
            self._words_data.sort(key=lambda w: w[0].lower())
        elif key == "rate":
            self._words_data.sort(
                key=lambda w: w[1].get("error_rate", 0), reverse=True)
        elif key == "count":
            self._words_data.sort(
                key=lambda w: w[1].get("count", 0), reverse=True)
        if self._words_sort_desc and key != "status":
            self._words_data.reverse()
        self._insert_word_rows(self._word_tree)

    def _on_word_tree_click(self, event) -> None:
        """Drill the sentence for a click on the Action cell, else select."""
        tree = self._word_tree
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        if tree.identify_column(event.x) != "#5":
            return
        word = self._row_word_at(event)
        if word:
            self._start_drill({"type": "word", "value": word})

    def _on_word_tree_double(self, event) -> None:
        word = self._row_word_at(event)
        if word:
            self._start_drill({"type": "word", "value": word})

    def _on_word_tree_right(self, event) -> None:
        """Right-click drills the word alone (same fallback as the buttons)."""
        word = self._row_word_at(event)
        if word:
            self._start_drill(self._word_only_target(word))

    def _row_word_at(self, event) -> Optional[str]:
        row = self._word_tree.identify_row(event.y)
        if not row:
            return None
        return row

    def _render_phonemes(self, phoneme_agg: Dict[str, Dict[str, Any]]) -> None:
        for child in self._tab_phonemes.winfo_children():
            child.destroy()
        if not phoneme_agg:
            tk.Label(self._tab_phonemes,
                     text=i18n.tr("No phoneme-level data yet. The phoneme engine "
                                  "provides this automatically."),
                     font=(FONT_FAMILY, FONT_SIZE_BODY),
                     bg=THEME["bg_main"], fg=THEME["text_emph"]
                     ).pack(pady=20)
            return
        # Header.
        tk.Label(self._tab_phonemes,
                 text=i18n.tr("Issued phonemes (native: {native})",
                              native=self._native),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(anchor="w", pady=(4, 6))
        # Grid: a responsive column count instead of a fixed 5. The window is
        # 900px wide; at ~200px per card that is 4 columns, and a narrow window
        # (or a wide one, after the user resizes) recomputes on <Configure>.
        # Rebuilding the grid on every <Configure> would thrash the cards, so
        # only the column layout is reapplied - the cards themselves live in a
        # fixed (single-column-per-card) grid and are repositioned by the
        # column count. The <Configure> binding is on the grid's parent.
        self._phoneme_agg = dict(sorted_by_weakness(phoneme_agg))
        grid = tk.Frame(self._tab_phonemes, bg=THEME["bg_main"])
        grid.pack(fill=tk.X)
        self._phoneme_grid = grid
        self._phoneme_cards: List[Tuple[int, tk.Frame]] = []
        for i, (ipa, entry) in enumerate(self._phoneme_agg.items()):
            card = self._make_phoneme_card(grid, ipa, entry)
            card.grid(row=0, column=i, sticky="nsew", padx=4, pady=4)
            self._phoneme_cards.append((i, card))
        self._phoneme_columns = 5
        self._layout_phoneme_grid()
        grid.bind("<Configure>", lambda e: self._layout_phoneme_grid(),
                  add="+")

    def _layout_phoneme_grid(self) -> None:
        """Re-position the phoneme cards for the current width.

        cols = max(3, width // 200): a 900px window gives 4 columns, a
        narrower one fewer, a wider one more. Only the row/column of each
        card changes; the cards are never destroyed, so a resize does not
        lose the user's hover or focus.
        """
        grid = self._phoneme_grid
        if grid is None:
            return
        width = grid.winfo_width()
        if width < 40:
            # Not mapped yet (first layout): fall back to the initial width.
            width = 900
        cols = max(3, width // 200)
        if cols == self._phoneme_columns:
            return
        self._phoneme_columns = cols
        for i, card in self._phoneme_cards:
            card.grid_configure(row=i // cols, column=i % cols)
        for c in range(cols):
            grid.columnconfigure(c, weight=1)
        # Unused columns keep weight 0 so they collapse instead of leaving a
        # blank slot on the right.
        for c in range(cols, 20):
            grid.columnconfigure(c, weight=0)

    def _make_phoneme_card(self, grid, ipa: str, entry: Dict[str, Any]
                           ) -> tk.Frame:
        card = tk.Frame(grid, bg=THEME["bg_panel"], bd=1,
                        highlightbackground=THEME["border"],
                        highlightthickness=1, width=170)
        severity = entry["severity_avg"]
        # Color: red for bad, dim for neutral, green for good.
        color = THEME["bad"] if severity > 0.5 else \
                (THEME["warn"] if severity > 0.25 else THEME["good"])
        tk.Label(card, text=f"/{ipa}/",
                 font=(FONT_FAMILY_MONO, 20, "bold"),
                 bg=THEME["bg_panel"], fg=color,
                 ).pack(pady=(8, 0))
        tk.Label(card, text=f"{entry.get('count', 0)}×  "
                            f"{i18n.tr('score')} "
                            f"{entry.get('avg_score', 0):.0%}",
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_panel"], fg=THEME["text_dim"]
                 ).pack(pady=(0, 4))
        hint = hint_for(ipa)
        example = example_word_for(ipa)
        tip_text = (i18n.tr("Example word: {example}", example=example)
                    if example else "")
        if hint:
            # The hint is a Chinese source string (ZH_INTERFERENCE.hint_zh);
            # pass it through i18n so the English UI renders an English hint
            # and the Chinese UI keeps the original.
            hint = i18n.tr(hint)
            tip_text = (f"{tip_text}  {hint}" if tip_text else hint)
        if tip_text:
            tk.Label(card, text=tip_text, wraplength=150, justify=tk.LEFT,
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(pady=(0, 4))
        # P2#8 interactive IPA chart: clicking a phoneme speaks its example
        # word. The example is looked up per-language (example_for) so the
        # word matches what the user is practicing, with the ZH-interference
        # table's English example as a fallback. The card's /ipa/ label and
        # the whole card area are clickable, and a small Play button makes
        # the affordance explicit.
        play_word = example_for(ipa) or example_word_for(ipa)
        button_row = tk.Frame(card, bg=THEME["bg_panel"])
        button_row.pack(pady=(0, 6))
        if play_word and self._cb.play_example is not None:
            ttk.Button(
                button_row, text=i18n.tr("Play"),
                command=lambda w=play_word: self._cb.play_example(w),
            ).pack(side=tk.LEFT, padx=(0, 4))
        # Left-click drills words with this phoneme (sentence context);
        # right-click drills the example word alone when one exists.
        self._make_drill_button(
            button_row, i18n.tr("Drill"),
            {"type": "phoneme", "value": ipa},
            self._word_only_target(play_word) if play_word else None,
        ).pack(side=tk.LEFT)
        return card

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _make_drill_button(self, parent, text: str,
                           sentence_target: Dict[str, str],
                           word_target: Optional[Dict[str, str]] = None
                           ) -> ttk.Button:
        """Create a drill button with left-click = sentence, right-click = word.

        Left-click drills the sentence (the target in context, e.g. a word
        drill pulls sentences containing it); right-click drills the word
        alone (``single_word``). When ``word_target`` is None the right-click
        falls back to the same target as left-click.
        """
        btn = ttk.Button(parent, text=text,
                         command=lambda t=sentence_target: self._start_drill(t))
        if word_target is not None:
            btn.bind("<Button-3>",
                     lambda e, t=word_target: self._start_drill(t))
        return btn

    @staticmethod
    def _word_only_target(word: str) -> Dict[str, str]:
        """Drill target for practicing one word in isolation."""
        return {"type": "single_word", "value": word}

    def _start_drill(self, target: Dict[str, str]) -> None:
        logging.info("Weakness: _start_drill target=%r", target)
        self._cb.start_drill(target)
        # Keep this window open: the drill opens as a separate Toplevel on top
        # (DrillView lifts and focuses itself), so the user can start another
        # drill from the same weakness view without reopening it. Destroying
        # this window here made the weakness panel vanish on every drill start.
        # self._window.destroy()
