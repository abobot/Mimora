# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""History browser: browse all persisted sessions and delete takes.

A Toplevel window opened from a future "History" button on the main
view. Shows every session (from :class:`StatsStore`) in newest-first
order, with each take inside. Each take has:

* a score chip + grade text
* the phrase it was scored on
* a trend arrow (up / down / flat) vs the previous take of the same
  phrase
* a play button (only enabled when ``HISTORY_MODE == "with_audio"``)
* a delete button

Sessions are grouped by a header row with a summary line (N takes,
average score, duration). A "Clear all" button at the bottom wipes
the entire history (with a two-step confirm).

The view is passive: it does not talk to ``StatsStore`` directly. It
calls ``load_sessions()`` on the controller to fetch the data and
``delete_*`` handlers to request mutations, so all persistence
stays in one place.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional

from mimora import config, i18n, window_icon
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_FAMILY_MONO,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
    WHEEL_EVENTS,
    wheel_scroll_units,
)
# Imported after ui_theme, which disables the ttkbootstrap autostyle hook.
import ttkbootstrap as ttk


@dataclass(frozen=True)
class HistoryCallbacks:
    """Typed view→controller contract for the history browser."""
    load_sessions: Callable[[], List[Dict[str, Any]]]
    load_drill_sessions: Callable[[], List[Dict[str, Any]]]
    load_dictation_sessions: Callable[[], List[Dict[str, Any]]]
    delete_take: Callable[[str, str], None]
    delete_session: Callable[[str], None]
    clear_all: Callable[[], None]
    play_recording: Callable[[str, str], None]
    export_json: Callable[[], Optional[Path]]
    export_csv: Callable[[], Optional[Path]]


def _fmt_timestamp(iso: str) -> str:
    """Format an ISO-8601 timestamp for a UI row.

    Falls back to the raw string if it cannot be parsed - a hand-edited
    stats.json should not take the whole browser down.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _status_color(entry: Dict[str, Any]) -> str:
    """Verdict colour for a take's score. Falls back to dim."""
    score = float(entry.get("score") or 0)
    if score >= 90:
        return THEME["good"]
    if score >= 70:
        return THEME["text_emph"]
    if score >= 50:
        return THEME["warn"] if "warn" in THEME else THEME["text_dim"]
    return THEME["bad"]


class HistoryBrowseView:
    """Toplevel that lists every session and its takes."""

    def __init__(self, root, callbacks: HistoryCallbacks):
        self.root = root
        self._cb = callbacks
        self._window = tk.Toplevel(root)
        self._window.title(i18n.tr("Practice history"))
        self._window.geometry("820x600")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)

        self._sessions: List[Dict[str, Any]] = []
        self._drill_sessions: List[Dict[str, Any]] = []
        self._dictation_sessions: List[Dict[str, Any]] = []
        self._expanded: set = set()  # session ids with takes shown

        self._build_ui()
        self.refresh()

    def exists(self) -> bool:
        try:
            return self._window.winfo_exists()
        except tk.TclError:
            return False

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        top = tk.Frame(self._window, bg=THEME["bg_main"])
        top.pack(fill=tk.X, padx=12, pady=10)
        tk.Label(top, text=i18n.tr("Practice history"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        self._summary_label = tk.Label(top, text="",
                                       font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                                       bg=THEME["bg_main"], fg=THEME["text_dim"])
        self._summary_label.pack(side=tk.LEFT, padx=12)

        buttons = tk.Frame(top, bg=THEME["bg_main"])
        buttons.pack(side=tk.RIGHT)
        ttk.Button(buttons, text=i18n.tr("Export JSON"),
                   command=self._on_export_json).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text=i18n.tr("Export CSV"),
                   command=self._on_export_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text=i18n.tr("Clear all"),
                   command=self._on_clear_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text=i18n.tr("Close"),
                   command=self._window.destroy).pack(side=tk.LEFT, padx=2)

        # Scrollable body of session rows.
        outer = tk.Frame(self._window, bg=THEME["bg_main"])
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self._canvas = tk.Canvas(outer, bg=THEME["bg_main"],
                                 highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL,
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._body = tk.Frame(self._canvas, bg=THEME["bg_main"])
        self._canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        for seq in WHEEL_EVENTS:
            self._canvas.bind(seq, self._on_wheel)
            self._body.bind(seq, self._on_wheel)

    def _on_wheel(self, event) -> None:
        units = wheel_scroll_units(event)
        if units:
            self._canvas.yview_scroll(units, "units")

    # ------------------------------------------------------------------
    # Data loading + rendering
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-fetch sessions from the store and re-render."""
        self._sessions = self._cb.load_sessions()
        self._drill_sessions = self._cb.load_drill_sessions()
        self._dictation_sessions = self._cb.load_dictation_sessions()
        self._render()

    def _render(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        if not self._sessions:
            tk.Label(self._body, text=i18n.tr("No practice sessions yet."),
                     font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                     fg=THEME["text_dim"], bg=THEME["bg_main"]
                     ).pack(fill=tk.X, padx=12, pady=12)
            self._summary_label.configure(text="")
            return

        # Summary.
        total_takes = sum(len(s.get("takes") or []) for s in self._sessions)
        total_sessions = len(self._sessions)
        self._summary_label.configure(
            text=i18n.tr("{total} sessions · {takes} takes",
                         total=total_sessions, takes=total_takes))

        # Newest session first.
        for session in reversed(self._sessions):
            self._build_session_block(session)

        if self._drill_sessions:
            divider = tk.Frame(self._body, bg=THEME["border"])
            divider.pack(fill=tk.X, padx=12, pady=(12, 4))
            tk.Label(self._body, text=i18n.tr("Drill sessions"),
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                     fg=THEME["text_emph"], bg=THEME["bg_main"]
                     ).pack(anchor="w", padx=12)
            for drill in reversed(self._drill_sessions):
                self._build_drill_row(drill)

        if self._dictation_sessions:
            divider = tk.Frame(self._body, bg=THEME["border"])
            divider.pack(fill=tk.X, padx=12, pady=(12, 4))
            tk.Label(self._body, text=i18n.tr("Dictation sessions"),
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                     fg=THEME["text_emph"], bg=THEME["bg_main"]
                     ).pack(anchor="w", padx=12)
            for dt in reversed(self._dictation_sessions):
                self._build_dictation_row(dt)

        self._canvas.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _build_session_block(self, session: Dict[str, Any]) -> None:
        sid = session.get("id", "")
        takes = session.get("takes") or []
        totals = session.get("totals") or {}
        header = tk.Frame(self._body, bg=THEME["bg_panel"], bd=1,
                          highlightbackground=THEME["border"],
                          highlightthickness=1)
        header.pack(fill=tk.X, padx=12, pady=(8, 4))

        def toggle(event=None):
            if sid in self._expanded:
                self._expanded.discard(sid)
            else:
                self._expanded.add(sid)
            self._render()

        header.bind("<Button-1>", toggle)
        arrow = "▾" if sid in self._expanded else "▸"
        label = f"{arrow}  {self._session_summary(session)}"
        tk.Label(header, text=label, anchor="w",
                 font=(FONT_FAMILY, FONT_SIZE_BODY),
                 fg=THEME["text_emph"], bg=THEME["bg_panel"],
                 padx=8, pady=4).pack(fill=tk.X)

        if sid not in self._expanded:
            return

        # Per-take rows.
        container = tk.Frame(self._body, bg=THEME["bg_panel"], bd=0)
        container.pack(fill=tk.X, padx=(24, 12), pady=(0, 4))
        # Track scores per phrase for the trend arrow.
        prev_score_by_phrase: Dict[str, float] = {}
        for take in takes:
            self._build_take_row(container, session, take, prev_score_by_phrase)

        # Delete whole session.
        delete_btn = tk.Label(container, text=i18n.tr("  ✕ delete session"),
                              fg=THEME["bad"], bg=THEME["bg_panel"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        delete_btn.pack(anchor="w", pady=(4, 4))
        delete_btn.bind("<Button-1>", lambda e, sid=sid:
                        self._on_delete_session(sid))

    def _session_summary(self, session: Dict[str, Any]) -> str:
        started = _fmt_timestamp(session.get("started_at") or "")
        ended = _fmt_timestamp(session.get("ended_at") or "")
        totals = session.get("totals") or {}
        parts = [f"{started}"]
        if ended:
            # A session spans a time range; always show the "started – ended"
            # pair with a separator. (The previous `endswith("00")` check
            # suppressed the dash when the end minute was ":00", which was an
            # arbitrary leftover, not a rule about anything.)
            parts.append("–")
            parts.append(ended)
        if totals:
            parts.append(i18n.tr("· {n} takes", n=totals.get('takes', 0)))
            avg = totals.get("avg_best_score", 0.0)
            if avg:
                parts.append(i18n.tr("· avg {avg:.0f}", avg=avg))
        return "  ".join(p for p in parts if p)

    def _build_take_row(self, container, session: Dict[str, Any],
                        take: Dict[str, Any],
                        prev_by_phrase: Dict[str, float]) -> None:
        row = tk.Frame(container, bg=THEME["bg_panel"])
        row.pack(fill=tk.X, pady=2)

        # Score chip.
        score = float(take.get("score") or 0)
        color = _status_color(take)
        chip_text = take.get("grade") or f"{score:.0f}"
        chip = tk.Label(row, text=f" {chip_text} ", fg=color,
                        bg=THEME["bg_panel"], padx=8,
                        highlightthickness=1, highlightbackground=color,
                        font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"))
        chip.pack(side=tk.LEFT, padx=(4, 4))

        # Trend arrow.
        phrase = (take.get("phrase") or "").strip()
        prev = prev_by_phrase.get(phrase)
        if prev is None:
            arrow_text, arrow_color = "·", THEME["text_dim"]
        elif score > prev:
            arrow_text, arrow_color = "▲", THEME["good"]
        elif score < prev:
            arrow_text, arrow_color = "▼", THEME["bad"]
        else:
            arrow_text, arrow_color = "–", THEME["text_dim"]
        tk.Label(row, text=arrow_text, fg=arrow_color,
                 bg=THEME["bg_panel"],
                 font=(FONT_FAMILY, FONT_SIZE_BODY)).pack(
            side=tk.LEFT, padx=(0, 8))
        prev_by_phrase[phrase] = score

        # Timestamp + phrase.
        ts = _fmt_timestamp(take.get("timestamp") or "")
        phrase_label = tk.Label(row, text=f"{ts}  {phrase}",
                                fg=THEME["text_emph"], bg=THEME["bg_panel"],
                                font=(FONT_FAMILY, FONT_SIZE_BODY),
                                anchor="w", justify=tk.LEFT, wraplength=520)
        phrase_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        # Error words summary (if any).
        error_words = take.get("error_words") or []
        if error_words:
            ew_text = ", ".join(w["word"] for w in error_words[:3])
            more = " …" if len(error_words) > 3 else ""
            tk.Label(row, text=i18n.tr("work on: {words}{more}",
                                       words=ew_text, more=more),
                     fg=THEME["bad"], bg=THEME["bg_panel"],
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(
                anchor="s", padx=(4, 4))

        # Play button (only when audio is stored).
        if take.get("audio_files") and config.HISTORY_MODE == "with_audio":
            play_btn = tk.Label(row, text="▶", fg=THEME["text_emph"],
                                bg=THEME["bg_panel"], padx=6,
                                cursor="hand2",
                                font=(FONT_FAMILY, FONT_SIZE_BODY))
            play_btn.pack(side=tk.LEFT, padx=(0, 2))
            play_btn.bind("<Button-1>", lambda e, sid=session["id"],
                          tid=take["id"]:
                          self._cb.play_recording(sid, tid))

        # Delete button.
        del_btn = tk.Label(row, text="✕", fg=THEME["bad"],
                           bg=THEME["bg_panel"], padx=8,
                           cursor="hand2",
                           font=(FONT_FAMILY, FONT_SIZE_BODY))
        del_btn.pack(side=tk.RIGHT, padx=(4, 4))
        del_btn.bind("<Button-1>", lambda e, sid=session["id"],
                     tid=take["id"]: self._on_delete_take(sid, tid))

    def _build_drill_row(self, drill: Dict[str, Any]) -> None:
        row = tk.Frame(self._body, bg=THEME["bg_panel"], bd=0)
        row.pack(fill=tk.X, padx=12, pady=2)
        target = drill.get("target") or {}
        t_type = target.get("type", "")
        t_value = target.get("value", "")
        outcome = drill.get("outcome", "")
        outcome_color = THEME["good"] if outcome == "mastered" else THEME["text_dim"]
        text = (f"[{t_type}:{t_value}]  "
                + i18n.tr("{n} attempts", n=drill.get('attempts', 0)) + "  "
                + i18n.tr("avg {avg:.0f}", avg=drill.get('avg_score', 0))
                + f"  {outcome}")
        tk.Label(row, text=text, anchor="w",
                 fg=THEME["text_emph"], bg=THEME["bg_panel"],
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(fill=tk.X)
        tk.Label(row, text=i18n.tr("   result: {outcome}", outcome=outcome),
                 fg=outcome_color, bg=THEME["bg_panel"],
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(anchor="w")

    def _build_dictation_row(self, dt: Dict[str, Any]) -> None:
        row = tk.Frame(self._body, bg=THEME["bg_panel"], bd=0)
        row.pack(fill=tk.X, padx=12, pady=2)
        outcome = dt.get("outcome", "")
        outcome_color = THEME["good"] if outcome == "mastered" else THEME["text_dim"]
        text = (i18n.tr("{n} sentences", n=dt.get('attempts', 0)) + "  "
                + i18n.tr("avg {avg:.0f}", avg=dt.get('avg_score', 0))
                + f"  {outcome}")
        tk.Label(row, text=text, anchor="w",
                 fg=THEME["text_emph"], bg=THEME["bg_panel"],
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(fill=tk.X)
        tk.Label(row, text=i18n.tr("   result: {outcome}", outcome=outcome),
                 fg=outcome_color, bg=THEME["bg_panel"],
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(anchor="w")
        wrong = dt.get("wrong_words") or []
        if wrong:
            tk.Label(row, text=i18n.tr("   words to review: {words}",
                                       words=", ".join(wrong[:12]))
                     + (" …" if len(wrong) > 12 else ""),
                     fg=THEME["bad"], bg=THEME["bg_panel"],
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION)).pack(anchor="w")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_delete_take(self, session_id: str, take_id: str) -> None:
        if messagebox.askyesno(
                i18n.tr("Delete take"),
                i18n.tr("Delete this practice record?\n\n(If audio is stored, its wav files are also removed.)"),
                parent=self._window, icon="warning"):
            self._cb.delete_take(session_id, take_id)
            self.refresh()

    def _on_delete_session(self, session_id: str) -> None:
        if messagebox.askyesno(
                i18n.tr("Delete session"),
                i18n.tr("Delete this whole session and all its takes?"),
                parent=self._window, icon="warning"):
            self._cb.delete_session(session_id)
            self._expanded.discard(session_id)
            self.refresh()

    def _on_clear_all(self) -> None:
        # Two-step confirm: the user must say "yes" twice.
        if not messagebox.askyesno(
                i18n.tr("Clear all history"),
                i18n.tr("This deletes every session, take and drill record.\n"
                        "Audio files are also removed.\n\n"
                        "Are you sure?"),
                parent=self._window, icon="warning"):
            return
        if not messagebox.askyesno(
                i18n.tr("Confirm"), i18n.tr("This action cannot be undone. Continue?"),
                parent=self._window, icon="warning"):
            return
        self._cb.clear_all()
        self._expanded.clear()
        self.refresh()

    def _on_export_json(self) -> None:
        path = self._cb.export_json()
        if path:
            messagebox.showinfo(i18n.tr("Export complete"),
                                i18n.tr("JSON saved to:\n{path}", path=path),
                                parent=self._window)

    def _on_export_csv(self) -> None:
        path = self._cb.export_csv()
        if path:
            messagebox.showinfo(i18n.tr("Export complete"),
                                i18n.tr("CSV saved to:\n{path}", path=path),
                                parent=self._window)
