# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Stats dashboard: summary cards and simple canvas charts.

A Toplevel opened from a future "Stats" button. Aggregates everything
in :class:`StatsStore` and renders:

* summary cards: total practice time, session count, take count,
  unique phrases, current streak;
* a score trend chart - the daily average score for the last N days;
* a score distribution bar chart - how many takes landed in each
  bucket (0-49 / 50-69 / 70-84 / 85-100);
* a recent-sessions list.

Charts are drawn on ``tk.Canvas`` because Mimora is a Tkinter app -
no matplotlib, no third-party plotting. The chart helpers are small
and self-contained.

The view is passive: it calls ``load_data()`` on the controller for
one big snapshot dict, then renders. That keeps the store access in
one place.
"""

from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional, Tuple


from mimora import config, i18n, window_icon
from mimora import (  # noqa: F401  (analysis modules used below)
    advance_analysis,
    dictation_analysis,
    drill_analysis,
    review_analysis,
)
from mimora.review import EBBINGHAUS_INTERVALS_DAYS
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
    WHEEL_EVENTS,
    wheel_scroll_units,
)
import ttkbootstrap as ttk


@dataclass(frozen=True)
class StatsCallbacks:
    """Typed view→controller contract for the stats dashboard."""
    load_data: Callable[[], Dict[str, Any]]
    export_json: Callable[[], Optional[str]]
    export_csv: Callable[[], Optional[str]]
    # P2#7: export a progress report image (PNG) or document (PDF) of the
    # same statistics, rendered offline by mimora.report.
    export_report_png: Callable[[], Optional[str]] = None
    export_report_pdf: Callable[[], Optional[str]] = None


def _fmt_seconds(seconds: int) -> str:
    """1440 → '2h 24m'; 60 → '1m'; 0 → '0m'."""
    if seconds < 60:
        return i18n.tr("{seconds}s", seconds=seconds)
    minutes = seconds // 60
    if minutes < 60:
        return i18n.tr("{minutes}m", minutes=minutes)
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return i18n.tr("{hours}h {rem}m", hours=hours, rem=rem)
    days = hours // 24
    rem_h = hours % 24
    return i18n.tr("{days}d {rem_h}h", days=days, rem_h=rem_h)


def _avg_score_for_session(session: Dict[str, Any]) -> float:
    totals = session.get("totals") or {}
    if "avg_best_score" in totals:
        return float(totals["avg_best_score"] or 0.0)
    # Fall back to averaging individual take scores.
    scores = [float(t.get("score") or 0) for t in session.get("takes") or []]
    return sum(scores) / len(scores) if scores else 0.0


def _session_date_key(session: Dict[str, Any]) -> str:
    """The calendar date (YYYY-MM-DD) a session belongs to."""
    ts = session.get("started_at") or ""
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except ValueError:
        return ts[:10]

from mimora.ui_chart import (  # noqa: F401  (re-exported for tests/back-compat)
    GITHUB_HEATMAP_LEVELS,
    _CanvasChart,
    _MONTH_ABBR,
    _WEEKDAY_ABBR,
    heatmap_cells,
    heatmap_layout,
    heatmap_month_layout,
)


class StatsView:
    """Toplevel showing the practice stats dashboard."""

    def __init__(self, root, callbacks: StatsCallbacks):
        self.root = root
        self._cb = callbacks
        # Month shown in the activity heatmap; defaults to the current month
        # and is moved by the prev/next arrows. Kept on this view so a data
        # refresh (e.g. after an export) does not reset the selection.
        today = datetime.now().date()
        self._heatmap_month = today.replace(day=1)
        self._window = tk.Toplevel(root)
        self._window.title(i18n.tr("Practice stats"))
        self._window.geometry("900x640")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)
        self._build_ui()
        self.refresh()

    def exists(self) -> bool:
        try:
            return self._window.winfo_exists()
        except tk.TclError:
            return False

    def _build_ui(self) -> None:
        top = tk.Frame(self._window, bg=THEME["bg_main"])
        top.pack(fill=tk.X, padx=16, pady=12)
        tk.Label(top, text=i18n.tr("Practice stats"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        btns = tk.Frame(top, bg=THEME["bg_main"])
        btns.pack(side=tk.RIGHT)
        ttk.Button(btns, text=i18n.tr("Export JSON"),
                   command=self._on_export_json).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=i18n.tr("Export CSV"),
                   command=self._on_export_csv).pack(side=tk.LEFT, padx=2)
        if self._cb.export_report_png is not None:
            ttk.Button(btns, text=i18n.tr("Export PNG"),
                       command=self._on_export_report_png).pack(side=tk.LEFT, padx=2)
        if self._cb.export_report_pdf is not None:
            ttk.Button(btns, text=i18n.tr("Export PDF"),
                       command=self._on_export_report_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=i18n.tr("Close"),
                   command=self._window.destroy).pack(side=tk.LEFT, padx=2)

        # The dashboard splits into four tabs (Overview / Trends /
        # Review pool / Sessions) instead of one long scroll. The body still
        # scrolls vertically because the Review pool tab alone is taller than
        # the viewport once its 2-column chart grid is drawn; the tab bar
        # stays fixed at the top of the body.
        body = tk.Frame(self._window, bg=THEME["bg_main"])
        body.pack(fill=tk.BOTH, expand=True)
        self._body_canvas = tk.Canvas(body, bg=THEME["bg_main"],
                                      highlightthickness=0)
        vbar = ttk.Scrollbar(body, orient=tk.VERTICAL,
                             command=self._body_canvas.yview)
        self._body_canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._content = tk.Frame(self._body_canvas, bg=THEME["bg_main"])
        self._content_id = self._body_canvas.create_window(
            (0, 0), window=self._content, anchor="nw")
        self._content.bind("<Configure>", self._on_content_configure)
        self._body_canvas.bind("<Configure>", self._on_canvas_configure)

        # One binding on the Toplevel serves every child (a widget's
        # bindtags include its toplevel), so the wheel scrolls the dashboard
        # body anywhere in the window. All WHEEL_EVENTS are bound:
        # <MouseWheel> for Windows/macOS, Button-4/5 for X11 (see
        # ui_theme.wheel_scroll_units).
        for sequence in WHEEL_EVENTS:
            self._window.bind(sequence, self._on_wheel)

        self._nb = ttk.Notebook(self._content)
        self._nb.pack(fill=tk.X, padx=16, pady=(8, 4))
        self._tab_overview = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_trends = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_review = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_sessions = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_dictation = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_drill = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._tab_advance = tk.Frame(self._nb, bg=THEME["bg_main"])
        self._nb.add(self._tab_overview, text=i18n.tr("Overview"))
        self._nb.add(self._tab_trends, text=i18n.tr("Trends"))
        self._nb.add(self._tab_review, text=i18n.tr("Review pool"))
        self._nb.add(self._tab_sessions, text=i18n.tr("Sessions"))
        self._nb.add(self._tab_dictation, text=i18n.tr("Dictation"))
        self._nb.add(self._tab_drill, text=i18n.tr("Drill"))
        self._nb.add(self._tab_advance, text=i18n.tr("Advance"))

        # Overview tab: summary cards + the two headline charts.
        self._cards = tk.Frame(self._tab_overview, bg=THEME["bg_main"])
        self._cards.pack(fill=tk.X, padx=8, pady=4)
        self._charts = tk.Frame(self._tab_overview, bg=THEME["bg_main"])
        self._charts.pack(fill=tk.X, padx=8, pady=8)

        # Trends tab: activity heatmap (with month arrows) + duration table.
        activity_head = tk.Frame(self._tab_trends, bg=THEME["bg_main"])
        activity_head.pack(fill=tk.X, padx=8, pady=(4, 0))
        ttk.Button(activity_head, text=i18n.tr("‹ Prev"),
                   command=self._heatmap_prev_month).pack(side=tk.LEFT)
        self._heatmap_month_var = tk.StringVar()
        tk.Label(activity_head, textvariable=self._heatmap_month_var,
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT, padx=10)
        ttk.Button(activity_head, text=i18n.tr("Next ›"),
                   command=self._heatmap_next_month).pack(side=tk.LEFT)

        self._activity = tk.Frame(self._tab_trends, bg=THEME["bg_main"])
        self._activity.pack(fill=tk.X, padx=8, pady=4)
        self._duration = tk.Frame(self._tab_trends, bg=THEME["bg_main"])
        self._duration.pack(fill=tk.X, padx=8, pady=4)

        # Review pool tab: the 8-section analysis in a 2-column chart grid.
        self._review = tk.Frame(self._tab_review, bg=THEME["bg_main"])
        self._review.pack(fill=tk.X, padx=8, pady=4)

        # Sessions tab: recent sessions list.
        self._recent = tk.Frame(self._tab_sessions, bg=THEME["bg_main"])
        self._recent.pack(fill=tk.X, padx=8, pady=4)

        # Dictation tab: word-error balance, spelling errors, progress
        # slope and mastery donut. Charts sit in a 2-column grid like the
        # review tab, so a wide window keeps two columns and a narrow one
        # still lays them out without overflow.
        self._dictation = tk.Frame(self._tab_dictation, bg=THEME["bg_main"])
        self._dictation.pack(fill=tk.X, padx=8, pady=4)

        # Drill tab: mastery by target type, progress, phoneme averages,
        # attempt distributions, investment, trajectories and audio
        # coverage — the full drill analysis in the same 2-column grid.
        self._drill = tk.Frame(self._tab_drill, bg=THEME["bg_main"])
        self._drill.pack(fill=tk.X, padx=8, pady=4)

        # Advance tab: the cross-file sections (practice/drill/dictation/
        # review joins) in the same 2-column grid. Added last because it is
        # the newest analysis; its charts reuse the shared _CanvasChart.
        self._advance = tk.Frame(self._tab_advance, bg=THEME["bg_main"])
        self._advance.pack(fill=tk.X, padx=8, pady=4)

    def _on_content_configure(self, _event=None) -> None:
        """Keep the scrollable content width matched to the canvas."""
        self._body_canvas.configure(
            scrollregion=self._body_canvas.bbox("all"))

    def _on_wheel(self, event) -> None:
        """Scroll the dashboard body with the mouse wheel.

        Bound on the Toplevel (see the comment in ``_build_ui``); a widget's
        bindtags include its toplevel, so this fires no matter which child
        the cursor is over. Delegates to the shared cross-platform unit
        conversion in ``ui_theme.wheel_scroll_units``.
        """
        units = wheel_scroll_units(event)
        if units:
            self._body_canvas.yview_scroll(units, "units")

    def _on_canvas_configure(self, event) -> None:
        """Stretch the content window to the canvas width (no horizontal
        scroll), so every section spans the full available width."""
        self._body_canvas.itemconfigure(self._content_id,
                                        width=event.width)

    def refresh(self) -> None:
        data = self._cb.load_data()
        sessions = data.get("sessions") or []
        raw_takes = data.get("all_takes") or []
        # Takes come as (session, take) tuples; extract just the take dicts.
        takes = [t for _, t in raw_takes] if raw_takes and isinstance(raw_takes[0], tuple) else raw_takes
        self._render_cards(sessions, takes)
        self._render_charts(sessions, takes)
        self._render_activity(sessions)
        self._render_duration_table(sessions)
        self._render_review(data.get("review_phrases") or {},
                            data.get("review_history") or [])
        self._render_recent(sessions)
        self._render_dictation(data.get("dictation_sessions") or [])
        self._render_drill(data.get("drill_sessions") or [])
        self._render_advance(
            sessions, takes,
            data.get("drill_sessions") or [],
            data.get("dictation_sessions") or [],
            data.get("review_phrases") or {})

    def _show_tab(self, tab: str) -> None:
        """Switch the visible tab and re-scroll to its top.

        Tabs are pre-created in ``_build_ui``; selecting one just raises it.
        The scroll region is re-synced by the content's <Configure> bind, so
        the canvas adjusts to the newly visible tab's height automatically.
        """
        index = {"overview": 0, "trends": 1, "review": 2, "sessions": 3,
                 "dictation": 4, "drill": 5, "advance": 6}[tab]
        self._nb.select(index)
        self._body_canvas.yview_moveto(0.0)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _render_cards(self, sessions: List[Dict], takes: List[Dict]) -> None:
        for child in self._cards.winfo_children():
            child.destroy()
        total_time = sum(int(s.get("duration_sec") or 0) for s in sessions)
        total_takes = len(takes)
        unique_phrases = len({(t.get("phrase") or "").strip()
                              for t in takes if (t.get("phrase") or "").strip()})
        scores = [float(t.get("score") or 0) for t in takes if t.get("score")]
        avg = sum(scores) / len(scores) if scores else 0.0
        best = max(scores) if scores else 0.0
        streak = _compute_streak(sessions,
                                 target_minutes=config.DAILY_TARGET_MINUTES * 60)
        cards = [
            (i18n.tr("Practice time"), _fmt_seconds(total_time)),
            (i18n.tr("Sessions"), str(len(sessions))),
            (i18n.tr("Takes"), f"{total_takes:,}"),
            (i18n.tr("Unique phrases"), f"{unique_phrases}"),
            (i18n.tr("Avg score"), f"{avg:.1f}" if avg else "—"),
            (i18n.tr("Best score"), f"{best:.1f}" if best else "—"),
            (i18n.tr("Streak"), f"{streak}d"),
        ]
        for i, (title, value) in enumerate(cards):
            card = tk.Frame(self._cards, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1)
            # Summary cards wrap at 4 per row instead of an unbounded
            # side=LEFT row: 7 cards would otherwise compress each to a sliver
            # on a narrow window, and the values (durations, counts) need
            # readable width. Rows grow down; all columns share one weight.
            card.grid(row=i // 4, column=i % 4, sticky="nsew",
                      padx=4, pady=4)
            self._cards.columnconfigure(i % 4, weight=1, uniform="cards")
            tk.Label(card, text=title, font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_dim"]
                     ).pack(pady=(8, 0))
            tk.Label(card, text=value, font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(pady=(0, 8))

    def _render_charts(self, sessions: List[Dict], takes: List[Dict]) -> None:
        for child in self._charts.winfo_children():
            child.destroy()
        # Daily average scores for the last 30 days.
        daily = defaultdict(list)
        for t in takes:
            ts = t.get("timestamp") or ""
            try:
                day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
            except ValueError:
                continue
            daily[day].append(float(t.get("score") or 0))
        # Sort by date.
        days = sorted(daily.keys())[-30:]
        scores_per_day = [sum(daily[d]) / len(daily[d]) for d in days]
        trend = _CanvasChart(self._charts, width=430, height=180)
        trend.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        trend.draw_line(scores_per_day, max_value=100.0,
                        color=THEME["good"],
                        label=i18n.tr("Daily avg score (last 30 days)"))

        # Score distribution buckets.
        buckets = [0, 0, 0, 0]
        for t in takes:
            s = float(t.get("score") or 0)
            idx = min(int(s // 25), 3)
            buckets[idx] += 1
        labels = ["0-24", "25-49", "50-74", "75-100"]
        dist = _CanvasChart(self._charts, width=430, height=180)
        dist.canvas.pack(side=tk.RIGHT, padx=8, pady=8)
        dist.draw_bars(buckets, labels, title=i18n.tr("Score distribution"))

    def _render_activity(self, sessions: List[Dict]) -> None:
        """Render the practice-activity heatmap for the selected month.

        ``self._heatmap_month`` selects which calendar month to show; the
        heatmap is redrawn in place when the prev/next arrows move it. The
        month title is refreshed here too, so a data refresh keeps it in
        sync with the selected month.
        """
        for child in self._activity.winfo_children():
            child.destroy()
        # Daily total practice seconds, per calendar day (any month).
        day_sec: Dict[str, int] = defaultdict(int)
        for s in sessions:
            key = _session_date_key(s)
            if key:
                day_sec[key] += int(s.get("duration_sec") or 0)
        month = self._heatmap_month
        self._heatmap_month_var.set(
            i18n.tr("{year} {month}",
                    year=month.year,
                    month=i18n.tr(_MONTH_ABBR[month.month - 1])))
        # Horizontal layout: 7 weekday columns across, one row per week. A
        # 5-6 week month needs ~5-6 rows (each ~27px) plus the month and
        # weekday label rows and the caption, so 240px height is generous.
        heat = _CanvasChart(self._activity, width=430, height=240)
        heat.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        heat.draw_heatmap_month(
            day_sec, month,
            label=i18n.tr("Practice minutes by day"))

    def _shift_heatmap_month(self, delta: int) -> None:
        """Move the heatmap's selected month by ``delta`` and redraw it."""
        month = self._heatmap_month
        month_index = month.year * 12 + (month.month - 1) + delta
        self._heatmap_month = date(month_index // 12, month_index % 12 + 1, 1)
        self.refresh()

    def _heatmap_prev_month(self) -> None:
        self._shift_heatmap_month(-1)

    def _heatmap_next_month(self) -> None:
        self._shift_heatmap_month(+1)

    def _render_duration_table(self, sessions: List[Dict]) -> None:
        """Render the per-session and per-day practice-duration table."""
        for child in self._duration.winfo_children():
            child.destroy()
        tk.Label(self._duration,
                 text=i18n.tr("Practice duration"),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(6, 2))
        if not sessions:
            tk.Label(self._duration, text=i18n.tr("No sessions yet."),
                     font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                     fg=THEME["text_dim"], bg=THEME["bg_main"]
                     ).pack(anchor="w")
            return
        # Per-day totals (newest first), for the last 14 days with practice.
        day_sec: Dict[str, int] = defaultdict(int)
        day_takes: Dict[str, int] = defaultdict(int)
        for s in sessions:
            key = _session_date_key(s)
            if key:
                day_sec[key] += int(s.get("duration_sec") or 0)
                day_takes[key] += int((s.get("totals") or {}).get("takes") or 0)
        rows = sorted(day_sec.items(), key=lambda kv: kv[0], reverse=True)[:14]
        for day, secs in rows:
            row = tk.Frame(self._duration, bg=THEME["bg_panel"], bd=0)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=day, anchor="w",
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     fg=THEME["text_emph"], bg=THEME["bg_panel"]
                     ).pack(side=tk.LEFT, padx=6)
            tk.Label(row, text=f"{_fmt_seconds(int(secs))}",
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     fg=THEME["text_dim"], bg=THEME["bg_panel"]
                     ).pack(side=tk.RIGHT, padx=6)

    def _render_review(self, phrases: Dict[str, Any],
                       history: List[Dict[str, Any]]) -> None:
        """Render the review-pool analysis (the 8 sections the user asked
        for, driven by review.json + config/review_history/).

        All the aggregation happens in :mod:`mimoreview_analysis.review_analysis` (pure,
        Tk-free); this method only draws. ``phrases`` is the store's
        ``phrases`` mapping (empty when review is off or unreadable), and
        ``history`` is the daily snapshots (oldest first).
        """
        for child in self._review.winfo_children():
            child.destroy()
        if not phrases:
            tk.Label(self._review, text=i18n.tr(
                "No review data yet. Review is off, or nothing is tracked yet."),
                font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                fg=THEME["text_dim"], bg=THEME["bg_main"]
                ).pack(anchor="w")
            return
        tk.Label(self._review, text=i18n.tr("Review pool"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(8, 2))

        # (8) KPI dashboard: small cards wrapping at 4 per row (same rule as
        # the summary cards above - an unbounded side=LEFT row would squeeze
        # six KPI values into unreadable width on a narrow window).
        kpi = review_analysis.kpi_dashboard(phrases)
        kpi_row = tk.Frame(self._review, bg=THEME["bg_main"])
        kpi_row.pack(fill=tk.X)
        kpi_items = [
            (i18n.tr("Total"), f"{kpi['total']}"),
            (i18n.tr("Due today"), f"{kpi['due_today']}"),
            (i18n.tr("Overdue"), f"{kpi['overdue']}"),
            (i18n.tr("Avg index"), f"{kpi['avg_index']:.1f}"),
            (i18n.tr("Pass rate"),
             f"{kpi['pass_rate']*100:.0f}%" if kpi['pass_rate'] is not None
             else "—"),
            (i18n.tr("Stubborn"), f"{kpi['stubborn']}"),
        ]
        for i, (title, value) in enumerate(kpi_items):
            card = tk.Frame(kpi_row, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1)
            card.grid(row=i // 4, column=i % 4, sticky="nsew",
                      padx=3, pady=4)
            kpi_row.columnconfigure(i % 4, weight=1, uniform="kpi")
            tk.Label(card, text=title,
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_dim"]
                     ).pack(pady=(6, 0))
            tk.Label(card, text=value,
                     font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(pady=(0, 6))

        # The 8 sections: one KPI row, six charts in a 2-column grid, and the
        # overdue list. Charts are gridded instead of side=LEFT/RIGHT packed so
        # a wide window keeps two columns (each ~430px) and a narrow one still
        # lays them out without overflow - the grid reflows on <Configure>.
        self._review_grid = tk.Frame(self._review, bg=THEME["bg_main"])
        self._review_grid.pack(fill=tk.X)

        def _chart_cell(parent, row, col):
            """Create a cell frame for one chart and grid it."""
            cell = tk.Frame(parent, bg=THEME["bg_main"])
            cell.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            parent.columnconfigure(col, weight=1, uniform="review")
            return cell

        # (1) Index distribution bar chart + unscored column.
        dist = review_analysis.index_distribution(phrases)
        counts = dist["counts"]
        values = [float(counts.get(i, 0))
                  for i in range(len(EBBINGHAUS_INTERVALS_DAYS))]
        labels = [str(i) for i in range(len(EBBINGHAUS_INTERVALS_DAYS))]
        if dist["unscored"]:
            values.append(float(dist["unscored"]))
            labels.append(i18n.tr("unscored"))
        idx_chart = _CanvasChart(_chart_cell(self._review_grid, 0, 0),
                                 width=430, height=160)
        idx_chart.canvas.pack(padx=4, pady=4)
        idx_chart.draw_bars(values, labels,
                            title=i18n.tr("Review stage distribution"))

        # (2) Due schedule: 60-day cumulative line.
        schedule = review_analysis.due_schedule(phrases)
        due_days = sorted(schedule["cumulative"].items())[:60]
        cum_vals = [float(v) for _, v in due_days]
        cum = _CanvasChart(_chart_cell(self._review_grid, 0, 1),
                           width=430, height=160)
        cum.canvas.pack(padx=4, pady=4)
        cum.draw_line(cum_vals,
                      max_value=max(cum_vals or [1.0]),
                      color=THEME["bad"],
                      label=i18n.tr("Due load (next 60 days, cumulative)"))

        # (3) Seen vs passed box plot (min/median/max per group).
        seen = review_analysis.seen_by_passed(phrases)
        seen_chart = _CanvasChart(_chart_cell(self._review_grid, 1, 0),
                                  width=430, height=160)
        seen_chart.canvas.pack(padx=4, pady=4)
        self._draw_seen_box(seen_chart, seen)

        # (4) Pool composition: per-source totals + pass rate.
        comp = review_analysis.source_composition(phrases)
        comp_chart = _CanvasChart(_chart_cell(self._review_grid, 1, 1),
                                  width=430, height=160)
        comp_chart.canvas.pack(padx=4, pady=4)
        self._draw_composition(comp_chart, comp)

        # (5) Span vs index scatter.
        spans = review_analysis.span_vs_index(phrases)
        span_chart = _CanvasChart(_chart_cell(self._review_grid, 2, 0),
                                  width=430, height=160)
        span_chart.canvas.pack(padx=4, pady=4)
        self._draw_span_scatter(span_chart, spans)

        # (6) History trajectory line (avg index over time).
        hist_chart = _CanvasChart(_chart_cell(self._review_grid, 2, 1),
                                  width=430, height=160)
        hist_chart.canvas.pack(padx=4, pady=4)
        hist_vals = [float(h.get("avg_index") or 0) for h in history]
        hist_chart.draw_line(
            hist_vals,
            max_value=max(hist_vals or [1.0]),
            color=THEME["info"],
            label=i18n.tr("Avg interval index over time"))

        # (7) Overdue list: table of the most overdue phrases.
        overdue = review_analysis.overdue_list(phrases)
        tk.Label(self._review, text=i18n.tr("Overdue phrases"),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(8, 2))
        if not overdue:
            tk.Label(self._review,
                     text=i18n.tr("Nothing is overdue. Nice!"),
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     fg=THEME["text_dim"], bg=THEME["bg_main"]
                     ).pack(anchor="w")
        else:
            for item in overdue[:12]:
                row = tk.Frame(self._review, bg=THEME["bg_panel"], bd=0)
                row.pack(fill=tk.X, pady=1)
                text = (f"{item.get('phrase', '?')}  ·  "
                        f"{i18n.tr('{days}d overdue',
                                   days=item.get('overdue_days', 0))}")
                tk.Label(row, text=text, anchor="w",
                         font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                         fg=THEME["text_emph"], bg=THEME["bg_panel"]
                         ).pack(fill=tk.X, padx=6, pady=2)

    def _draw_seen_box(self, chart, seen: Dict[str, List[int]]) -> None:
        """Min/median/max 'seen' box for passed vs failed phrases."""
        c = chart.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 40, 8, 12, 28
        plot_w = chart._width - pad_l - pad_r
        plot_h = chart._height - pad_t - pad_b
        groups = [("passed", seen.get("passed", []), THEME["good"]),
                  ("failed", seen.get("failed", []), THEME["bad"])]
        max_seen = max([max(v or [0]) for _, v, _ in groups] or [0])
        max_seen = max(max_seen, 1)
        for gi, (name, values, color) in enumerate(groups):
            cx = pad_l + plot_w * (gi + 0.5) / len(groups)
            if values:
                lo, hi = min(values), max(values)
                med = sorted(values)[len(values) // 2]
                y_lo = pad_t + plot_h * (1 - lo / max_seen)
                y_hi = pad_t + plot_h * (1 - hi / max_seen)
                y_med = pad_t + plot_h * (1 - med / max_seen)
                c.create_rectangle(cx - 14, y_hi, cx + 14, y_lo,
                                   fill=color, outline=THEME["border"])
                c.create_line(cx - 14, y_med, cx + 14, y_med,
                              fill=THEME["text_emph"], width=2)
            c.create_text(cx, pad_t + plot_h + 4, text=name, anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        c.create_text(pad_l, 6, text=i18n.tr("Seen count by last result"),
                      anchor="nw", fill=THEME["text_emph"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def _draw_composition(self, chart, comp: Dict[str, Any]) -> None:
        """Per-source stacked bars (total + pass-rate labels)."""
        c = chart.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 40, 8, 12, 28
        plot_w = chart._width - pad_l - pad_r
        plot_h = chart._height - pad_t - pad_b
        sources = [s for s in ("practice", "llm") if s in comp["totals"]]
        if not sources:
            return
        totals = [comp["totals"][s] for s in sources]
        max_t = max(totals + [1])
        for i, s in enumerate(sources):
            cx = pad_l + plot_w * (i + 0.5) / len(sources)
            v = comp["totals"][s]
            y = pad_t + plot_h * (1 - v / max_t)
            c.create_rectangle(cx - 22, y, cx + 22, pad_t + plot_h,
                               fill=THEME["info"], outline="")
            rate = comp["pass_rate"].get(s)
            rate_text = (f"{rate*100:.0f}%" if rate is not None else "—")
            c.create_text(cx, y - 4, text=f"{v} ({rate_text})", anchor="s",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            c.create_text(cx, pad_t + plot_h + 4, text=s, anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        c.create_text(pad_l, 6, text=i18n.tr("Pool by source (total / pass %)"),
                      anchor="nw", fill=THEME["text_emph"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def _draw_span_scatter(self, chart,
                           spans: List[Tuple[int, int, bool, int]]) -> None:
        """Scatter of span_days (x) vs interval index (y)."""
        c = chart.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 40, 8, 12, 28
        plot_w = chart._width - pad_l - pad_r
        plot_h = chart._height - pad_t - pad_b
        if not spans:
            c.create_text(chart._width / 2, chart._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        max_x = max(s[0] for s in spans) or 1
        max_y = max(s[1] for s in spans) or 1
        for span, idx, passed, _seen in spans:
            x = pad_l + plot_w * span / max_x
            y = pad_t + plot_h * (1 - idx / max_y)
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=THEME["good"] if passed else THEME["bad"],
                          outline="")
        c.create_text(pad_l, 6, text=i18n.tr("Span (days) vs interval index"),
                      anchor="nw", fill=THEME["text_emph"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def _render_recent(self, sessions: List[Dict]) -> None:
        for child in self._recent.winfo_children():
            child.destroy()
        if not sessions:
            tk.Label(self._recent, text=i18n.tr("No sessions yet."),
                     font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                     fg=THEME["text_dim"], bg=THEME["bg_main"]
                     ).pack(anchor="w")
            return
        tk.Label(self._recent, text=i18n.tr("Recent sessions"),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(4, 2))
        for session in reversed(sessions[-10:]):
            totals = session.get("totals") or {}
            started = (session.get("started_at") or "")[:16].replace("T", " ")
            row = tk.Frame(self._recent, bg=THEME["bg_panel"], bd=0)
            row.pack(fill=tk.X, pady=1)
            avg_text = f"{float(totals.get('avg_best_score') or 0):.0f}"
            text = (f"{started}  ·  "
                    f"{i18n.tr('{takes} takes', takes=totals.get('takes', 0))}  ·  "
                    f"{i18n.tr('avg {avg}', avg=avg_text)}  ·  "
                    f"{_fmt_seconds(int(session.get('duration_sec') or 0))}")
            tk.Label(row, text=text, anchor="w",
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     fg=THEME["text_emph"], bg=THEME["bg_panel"]
                     ).pack(fill=tk.X, padx=6, pady=2)

    def _render_dictation(self, sessions: List[Dict]) -> None:
        """Render the dictation tab.

        The layout follows the dashboard design: a KPI row on top (mastery,
        average attempts, average first→last gain, session count), then a
        two-column grid of charts - the attempts histogram with the pass-rate
        curve, the three error kinds stacked by week, the top-N wrong words,
        the first→last dumbbell, the score distribution with the pass split,
        and the word-by-week heatmap. The mastery donut and per-session
        attempt series fill the second row.

        All the aggregation happens in :mod:`mimora.dictation_analysis`
        (pure, Tk-free); this method only draws. ``sessions`` is the store's
        ``dictation_sessions`` list (empty when none have been recorded).
        """
        for child in self._dictation.winfo_children():
            child.destroy()
        if not sessions:
            tk.Label(self._dictation, text=i18n.tr(
                "No dictation sessions yet. Open the Dictation mode to start one."),
                font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                fg=THEME["text_dim"], bg=THEME["bg_main"]
                ).pack(anchor="w")
            return
        tk.Label(self._dictation, text=i18n.tr("Dictation"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(8, 2))

        # KPI row: mastery rate, average attempts, average gain, sessions.
        kpi = _CanvasChart(self._dictation, width=430, height=60)
        kpi.canvas.pack(padx=4, pady=(4, 0), fill=tk.X)
        kpi.draw_kpi_cards([
            (i18n.tr("Mastery"), f"{dictation_analysis.mastery_rate(sessions):.0%}"),
            (i18n.tr("Avg attempts"), f"{dictation_analysis.average_attempts(sessions):.1f}"),
            (i18n.tr("Avg gain"), f"{dictation_analysis.average_gain(sessions):+.1f}"),
            (i18n.tr("Sessions"), f"{len(sessions)}"),
        ])

        # Two-column chart grid (same pattern as the review tab's grid).
        grid = tk.Frame(self._dictation, bg=THEME["bg_main"])
        grid.pack(fill=tk.X)

        def cell(row, col):
            c = tk.Frame(grid, bg=THEME["bg_main"])
            c.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(col, weight=1, uniform="dictation")
            return c

        # (1) Attempts histogram (session count) with the pass-rate curve
        # overlaid on a shared attempt-number x-axis.
        hist = dictation_analysis.attempts_histogram(sessions)
        attempt_nums = [str(n) for n, _ in hist]
        counts = [float(c) for _, c in hist]
        hist_chart = _CanvasChart(cell(0, 0), width=430, height=170)
        hist_chart.canvas.pack(padx=4, pady=4)
        hist_chart.draw_bars(
            counts, attempt_nums, color=THEME["accent"],
            title=i18n.tr("Attempts per session"))
        rate_chart = _CanvasChart(cell(0, 1), width=430, height=170)
        rate_chart.canvas.pack(padx=4, pady=4)
        rate = dictation_analysis.pass_rate_by_attempt(sessions)
        rate_chart.draw_line_series(
            [(i18n.tr("Pass rate"),
              [float(r) for _, r in rate], THEME["good"])],
            [str(n) for n, _ in rate],
            max_value=1.0,
            title=i18n.tr("Pass rate by attempt"))

        # (2) Error kinds stacked by week (missed / extra / spelling).
        trend = dictation_analysis.error_type_trend(sessions)
        weeks = [w for w, _ in trend]
        stacked = _CanvasChart(cell(1, 0), width=430, height=170)
        stacked.canvas.pack(padx=4, pady=4)
        stacked.draw_stacked_bars(
            [
                (i18n.tr("Missed"), [float(b["missed"]) for _, b in trend]),
                (i18n.tr("Extra"), [float(b["extra"]) for _, b in trend]),
                (i18n.tr("Spelling"), [float(b["spelling"]) for _, b in trend]),
            ],
            weeks,
            title=i18n.tr("Error kinds by week"))

        # (3) Top-N wrong words: horizontal bars, best first.
        wrong = dictation_analysis.top_wrong_words(sessions, limit=12)
        hbar = _CanvasChart(cell(1, 1), width=430, height=170)
        hbar.canvas.pack(padx=4, pady=4)
        hbar.draw_hbars([(w, float(c)) for w, c in wrong],
                        title=i18n.tr("Top wrong words"),
                        color=THEME["bad"])

        # (4) First→last dumbbell per session (labelled by a short session id).
        # progress_pairs and session_dates both cover every session in stored
        # order, so zipping them aligns id -> (first, last) correctly.
        pairs = dictation_analysis.progress_pairs(sessions)
        date_ids = [sid for sid, _ in dictation_analysis.session_dates(sessions)]
        label_map = {sid: sid.replace("dt-", "")[:10]
                     for sid, _ in dictation_analysis.session_dates(sessions)}
        dumbbell = _CanvasChart(cell(2, 0), width=430, height=170)
        dumbbell.canvas.pack(padx=4, pady=4)
        dumbbell_items = []
        for i, (sid, (first, last)) in enumerate(zip(date_ids, pairs)):
            dumbbell_items.append((label_map.get(sid, str(i + 1)),
                                   first, last))
        dumbbell.draw_dumbbell(dumbbell_items,
                               title=i18n.tr("First → last score per session"))

        # (5) Score distribution with the pass split (stacked green/red).
        dist = dictation_analysis.score_distribution(sessions)
        score_chart = _CanvasChart(cell(2, 1), width=430, height=170)
        score_chart.canvas.pack(padx=4, pady=4)
        score_chart.draw_stacked_histogram(dist,
                                           title=i18n.tr("Score distribution"))

        # (6) Word-by-week heatmap: rows = top wrong words, cols = weeks.
        words, hw_weeks, matrix = dictation_analysis.word_by_week_heatmap(sessions)
        heat = _CanvasChart(cell(3, 0), width=430, height=170)
        heat.canvas.pack(padx=4, pady=4)
        heat.draw_heatmap_matrix(words, hw_weeks, matrix,
                                 GITHUB_HEATMAP_LEVELS,
                                 title=i18n.tr("Wrong words by week"))

        # (7) Per-session attempt series (a line per session, coloured by
        # mastery), rendered without a legend to avoid overflow.
        series = dictation_analysis.attempt_series(sessions)
        series_chart = _CanvasChart(cell(3, 1), width=430, height=170)
        series_chart.canvas.pack(padx=4, pady=4)
        series_lines = []
        for sid, scores, mastered in series:
            series_lines.append((
                sid,
                [float(s) for s in scores],
                THEME["good"] if mastered else THEME["accent"],
            ))
        series_chart.draw_line_series(
            series_lines,
            [str(i + 1) for i in range(max((len(s[1]) for s in series), default=0))],
            max_value=100.0,
            title=i18n.tr("Score per attempt, per session"),
            legend=False)

        # (8) Outcome distribution donut: mastered vs unfinished.
        outcome = dictation_analysis.outcome_distribution(sessions)
        donut = _CanvasChart(cell(4, 0), width=430, height=170)
        donut.canvas.pack(padx=4, pady=4)
        self._draw_dictation_donut(donut, outcome)

    def _draw_dictation_donut(self, chart, outcome: Dict[str, int]) -> None:
        """Donut of mastery outcomes: mastered vs unfinished sessions."""
        c = chart.canvas
        c.delete("all")
        mastered = int(outcome.get("mastered", 0))
        unfinished = int(outcome.get("unfinished", 0))
        total = mastered + unfinished
        if total == 0:
            c.create_text(chart._width / 2, chart._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        cx = chart._width / 2
        cy = chart._height / 2 - 6
        r = min(chart._width, chart._height) / 2 - 18
        # Background ring = unfinished (muted); foreground arc = mastered.
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=THEME["bg_card"], outline=THEME["border"])
        if mastered:
            c.create_arc(cx - r, cy - r, cx + r, cy + r,
                         start=90, extent=-360.0 * mastered / total,
                         fill=THEME["good"], outline="")
        # Punch the donut hole.
        hr = r * 0.62
        c.create_oval(cx - hr, cy - hr, cx + hr, cy + hr,
                      fill=THEME["bg_main"], outline="")
        # Centre label: mastery percentage.
        pct = mastered / total * 100
        c.create_text(cx, cy - 6, text=f"{pct:.0f}%",
                      fill=THEME["text_emph"],
                      font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"))
        c.create_text(cx, cy + 12, text=i18n.tr("mastered"),
                      fill=THEME["text_dim"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        # Legend, centred under the ring.
        c.create_text(chart._width / 2, chart._height - 8,
                      text=f"{i18n.tr('mastered')}: {mastered}   "
                           f"{i18n.tr('unfinished')}: {unfinished}",
                      anchor="s", fill=THEME["text_dim"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION))

    # ------------------------------------------------------------------
    # Drill tab
    # ------------------------------------------------------------------

    def _render_drill(self, sessions: List[Dict]) -> None:
        """Render the drill tab.

        The layout follows the drill design: a KPI row on top (total
        sessions, mastery rate, phoneme average, stubborn targets), then a
        two-column grid of charts - mastery by type, progress per session,
        phoneme averages, attempt distributions, pass-rate curves,
        investment, same-target progress, trajectories, weekly volume, the
        investment-vs-outcome scatter, and the target-by-week heatmap.

        All the aggregation happens in :mod:`mimora.drill_analysis`
        (pure, Tk-free); this method only draws. ``sessions`` is the store's
        ``drill_sessions`` list (empty when none have been recorded).
        """
        for child in self._drill.winfo_children():
            child.destroy()
        if not sessions:
            tk.Label(self._drill, text=i18n.tr(
                "No drill sessions yet. Open the Drill mode to start one."),
                font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                fg=THEME["text_dim"], bg=THEME["bg_main"]
                ).pack(anchor="w")
            return
        tk.Label(self._drill, text=i18n.tr("Drill"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(8, 2))

        # KPI row: sessions, mastery rate, phoneme average, stubborn targets.
        kpi = _CanvasChart(self._drill, width=430, height=60)
        kpi.canvas.pack(padx=4, pady=(4, 0), fill=tk.X)
        overall_mastered = sum(d["mastered"] for _, d in drill_analysis.mastery_by_type(sessions))
        phoneme_avg = (
            sum(a for _, a, _ in drill_analysis.phoneme_averages(sessions))
            / len(drill_analysis.phoneme_averages(sessions))
            if drill_analysis.phoneme_averages(sessions) else 0.0)
        kpi.draw_kpi_cards([
            (i18n.tr("Sessions"), f"{len(sessions)}"),
            (i18n.tr("Mastery"),
             f"{overall_mastered / len(sessions):.0%}"),
            (i18n.tr("Phoneme avg"), f"{phoneme_avg:.0f}"),
            (i18n.tr("Stubborn targets"), f"{drill_analysis.stubborn_targets(sessions)}"),
        ])

        # Two-column chart grid (same pattern as the dictation tab).
        grid = tk.Frame(self._drill, bg=THEME["bg_main"])
        grid.pack(fill=tk.X)

        def cell(row, col):
            c = tk.Frame(grid, bg=THEME["bg_main"])
            c.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(col, weight=1, uniform="drill")
            return c

        def chart(cell_frame, height=170):
            ch = _CanvasChart(cell_frame, width=430, height=height)
            ch.canvas.pack(padx=4, pady=4)
            return ch

        # (1) Mastery by type: stacked bars (mastered green over unfinished
        # red) per target type.
        mastery = drill_analysis.mastery_by_type(sessions)
        type_labels = [i18n.tr(_type_name(t)) for t, _ in mastery]
        mastery_chart = chart(cell(0, 0))
        mastery_chart.draw_stacked_bars(
            [
                (i18n.tr("mastered"), [float(d["mastered"]) for _, d in mastery]),
                (i18n.tr("unfinished"), [float(d["unfinished"]) for _, d in mastery]),
            ],
            type_labels,
            title=i18n.tr("Mastery by type"))

        # (2) Weekly volume by type: stacked bars of attempts per week.
        weekly = drill_analysis.weekly_volume_by_type(sessions)
        weekly_weeks = [w for w, _ in weekly]
        weekly_chart = chart(cell(0, 1))
        weekly_chart.draw_stacked_bars(
            [
                (_type_label(t, "single_word"),
                 [float(b.get(t, 0)) for _, b in weekly])
                for t in ("phoneme", "word", "single_word")
            ],
            weekly_weeks,
            title=i18n.tr("Weekly volume by type"))

        # (3) Progress per session: dumbbell first→last, coloured by type.
        progress = drill_analysis.progress_by_type(sessions)
        dumbbell_items = []
        type_colors = {"phoneme": THEME["info"], "word": THEME["accent"],
                       "single_word": THEME["good"], "free_form": THEME["warn"]}
        for t, rows in progress:
            for sid, first, last, attempts in rows:
                dumbbell_items.append((sid.replace("d-", "")[:10],
                                       first, last))
        prog_chart = chart(cell(1, 0))
        prog_chart.draw_dumbbell(dumbbell_items,
                                 title=i18n.tr("Progress per session"))

        # (4) Phoneme averages: horizontal bars, weakest on top.
        pa = drill_analysis.phoneme_averages(sessions)
        pa_chart = chart(cell(1, 1))
        pa_chart.draw_hbars([(f"/{ipa}/", avg) for ipa, avg, _ in pa],
                            title=i18n.tr("Phoneme averages"),
                            color=THEME["info"])

        # (5) Attempts histogram by type (session counts).
        hist = drill_analysis.attempts_histogram_by_type(sessions)
        max_n = max((n for _, pts in hist for n, _ in pts), default=0)
        hist_chart = chart(cell(2, 0))
        hist_chart.draw_stacked_bars(
            [
                (_type_label(t, "single_word"),
                 [float(dict(pts).get(n, 0)) for n in range(max_n + 1)])
                for t, pts in hist
            ],
            [str(n) for n in range(max_n + 1)],
            title=i18n.tr("Attempts per session by type"))

        # (6) Pass-rate curve by type (per attempt number).
        rates = drill_analysis.pass_rate_by_attempt_by_type(sessions)
        all_n = sorted({n for _, pts in rates for n, _ in pts})
        rate_chart = chart(cell(2, 1))
        rate_series = []
        for t, pts in rates:
            rate_series.append((
                _type_label(t, "single_word"),
                [float(dict(pts).get(n, 0.0)) for n in all_n],
                type_colors.get(t, THEME["accent"]),
            ))
        rate_chart.draw_line_series(rate_series, [str(n) for n in all_n],
                                    max_value=1.0,
                                    title=i18n.tr("Pass rate by attempt by type"))

        # (7) Investment vs outcome scatter: attempts vs last score.
        sc = drill_analysis.investment_scatter(sessions)
        scatter = chart(cell(3, 0))
        scatter.draw_scatter(
            [(float(attempts), float(last)) for _, _, attempts, last in sc],
            title=i18n.tr("Investment vs outcome"),
            x_label=i18n.tr("Attempts"), y_label=i18n.tr("Last score"))

        # (8) Same target over time: one line series per target value.
        # Aligned to the union of all dates; a None value is a gap (the
        # target had no session that week), so the line breaks there.
        tps = drill_analysis.target_progress_series(sessions)
        all_dates = sorted({date for _, _, series in tps for date, _ in series})
        tp_series = []
        for t, value, series in tps:
            value_map = {date: avg for date, avg in series}
            tp_series.append((
                f"/{value}/" if t == "phoneme" else value,
                [value_map.get(d) for d in all_dates],
                type_colors.get(t, THEME["accent"]),
            ))
        tp_chart = chart(cell(3, 1))
        tp_chart.draw_line_series(tp_series, [str(d) for d in all_dates],
                                  max_value=100.0,
                                  title=i18n.tr("Same target over time"))

        # (9) Session trajectories: overlay lines per session.
        traj = drill_analysis.session_trajectories(sessions)
        traj_chart = chart(cell(4, 0))
        traj_series = []
        max_len = max((len(scores) for _, _, _, scores in traj), default=0)
        for sid, t, outcome, scores in traj:
            traj_series.append((
                sid.replace("d-", "")[:8],
                scores,
                THEME["good"] if outcome == "mastered" else THEME["accent"],
            ))
        traj_chart.draw_line_series(
            traj_series,
            [str(i + 1) for i in range(max_len)],
            max_value=100.0,
            title=i18n.tr("Session trajectories"),
            legend=False)

        # (10) Target-by-week heatmap.
        targets, weeks, matrix = drill_analysis.target_by_week_heatmap(sessions)
        heat = chart(cell(4, 1))
        heat.draw_heatmap_matrix(targets, weeks, matrix,
                                 GITHUB_HEATMAP_LEVELS,
                                 title=i18n.tr("Target × week"))

        # (11) Audio coverage: with-audio vs no-audio stacked bar.
        with_audio, total = drill_analysis.audio_coverage(sessions)
        audio_chart = chart(cell(5, 0))
        audio_chart.draw_stacked_bars(
            [
                (i18n.tr("With audio"), [float(with_audio)]),
                (i18n.tr("No audio"), [float(total - with_audio)]),
            ],
            [i18n.tr("Audio coverage")],
            title=i18n.tr("Audio coverage"))

        # (12) Outcome donut (mastered vs unfinished) reused from dictation.
        outcome = {"mastered": overall_mastered,
                   "unfinished": len(sessions) - overall_mastered}
        donut = chart(cell(5, 1))
        self._draw_dictation_donut(donut, outcome)

    def _render_advance(self, sessions: List[Dict], takes: List[Dict],
                        drill_sessions: List[Dict],
                        dictation_sessions: List[Dict],
                        review_phrases: Dict[str, Any]) -> None:
        """Render the advance tab (cross-file analysis).

        The advance tab joins the practice takes, drill sessions, dictation
        sessions and the review pool into the sections the user asked for:
        key overview (0), error-word flow (1), phoneme weakness (2), drill
        take origin (3, the score-consistency question answered structurally),
        activity timeline (4), audio coverage (5), phrase funnel (6), day
        correlation (7) and the join gotchas (8). All aggregation happens in
        :mod:`mimora.advance_analysis` (pure, Tk-free); this method only
        draws.
        """
        for child in self._advance.winfo_children():
            child.destroy()
        if not sessions and not drill_sessions and not dictation_sessions:
            tk.Label(self._advance, text=i18n.tr(
                "No activity yet. Practice, drill or dictate to populate the "
                "advance analysis."),
                font=(FONT_FAMILY, FONT_SIZE_BODY, "italic"),
                fg=THEME["text_dim"], bg=THEME["bg_main"]
                ).pack(anchor="w")
            return
        tk.Label(self._advance, text=i18n.tr("Advance"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(anchor="w", pady=(8, 2))

        # (0) Key overview KPI cards.
        kpi = advance_analysis.kpi_overview(takes, drill_sessions, dictation_sessions,
                              review_phrases)
        coverage = kpi["audio_coverage"]
        kpi_row = tk.Frame(self._advance, bg=THEME["bg_main"])
        kpi_row.pack(fill=tk.X)
        kpi_items = [
            (i18n.tr("Practice takes"), f"{kpi['practice_takes']}"),
            (i18n.tr("Error words"), f"{kpi['error_words']}"),
            (i18n.tr("Review pool"), f"{kpi['review_pool']}"),
            (i18n.tr("Drill sessions"), f"{kpi['drill_sessions']}"),
            (i18n.tr("Dictation sessions"), f"{kpi['dictation_sessions']}"),
            (i18n.tr("Audio coverage"),
             f"{coverage:.0%}" if coverage is not None else "—"),
            (i18n.tr("Mastered drills"), f"{kpi['mastered_drills']}"),
        ]
        for i, (title, value) in enumerate(kpi_items):
            card = tk.Frame(kpi_row, bg=THEME["bg_panel"], bd=1,
                            highlightbackground=THEME["border"],
                            highlightthickness=1)
            card.grid(row=i // 4, column=i % 4, sticky="nsew",
                      padx=3, pady=4)
            kpi_row.columnconfigure(i % 4, weight=1, uniform="advance_kpi")
            tk.Label(card, text=title,
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     bg=THEME["bg_panel"], fg=THEME["text_dim"]
                     ).pack(pady=(6, 0))
            tk.Label(card, text=value,
                     font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                     bg=THEME["bg_panel"], fg=THEME["text_emph"]
                     ).pack(pady=(0, 6))

        # Chart grid (same 2-column pattern as the drill tab).
        grid = tk.Frame(self._advance, bg=THEME["bg_main"])
        grid.pack(fill=tk.X)

        def cell(row, col):
            c = tk.Frame(grid, bg=THEME["bg_main"])
            c.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(col, weight=1, uniform="advance")
            return c

        def chart(cell_frame, height=170):
            ch = _CanvasChart(cell_frame, width=430, height=height)
            ch.canvas.pack(padx=4, pady=4)
            return ch

        # (1) Error-word flow sankey.
        wf = advance_analysis.word_flow(takes, drill_sessions)
        nodes, links = advance_analysis.word_flow_nodes(wf)
        flow = chart(cell(0, 0), height=190)
        flow.draw_sankey(nodes, links,
                         title=i18n.tr("Error words → drill → mastery"))

        # (2) Phoneme weakness vs drill coverage (top weak phonemes).
        pw = advance_analysis.phoneme_weakness(takes, drill_sessions)
        weak_items = [(f"/{ipa}/", avg * 100.0)
                      for ipa, avg, _, _ in pw["weak"][:8]]
        weak = chart(cell(0, 1))
        weak.draw_hbars(weak_items,
                        title=i18n.tr("Weak phonemes (coverage: {d}/{w})"
                                      .format(d=pw["drilled_count"],
                                              w=pw["weak_count"])),
                        color=THEME["info"])

        # (3) Drill take origin (the score-consistency question answered
        # structurally: a drill take IS the practice take it references).
        origin = advance_analysis.drill_take_origin(drill_sessions, takes)
        origin_chart = chart(cell(1, 0))
        origin_chart.draw_stacked_bars(
            [
                (i18n.tr("Linked"), [float(origin["referenced"])]),
                (i18n.tr("Unlinked"), [float(origin["unreferenced"])]),
            ],
            [i18n.tr("Drill takes")],
            title=i18n.tr("Drill takes linked to practice"))

        # (4) Activity timeline: per-day session counts, 3 types.
        tl = advance_analysis.activity_timeline(sessions, drill_sessions, dictation_sessions)
        timeline = chart(cell(1, 1))
        timeline.draw_stacked_bars(
            [
                ("Practice",
                 [float(v) for v in tl["practice"]]),
                ("Drill",
                 [float(v) for v in tl["drill"]]),
                ("Dictation",
                 [float(v) for v in tl["dictation"]]),
            ],
            [d[5:] for d in tl["days"]],
            title=i18n.tr("Activity by day"))

        # (5) Audio coverage matrix: practice vs drill with/without.
        acm = advance_analysis.audio_coverage_matrix(takes, drill_sessions)
        audio = chart(cell(2, 0))
        audio.draw_stacked_bars(
            [
                (i18n.tr("With audio"),
                 [float(acm["practice"]["with"]),
                  float(acm["drill"]["with"])]),
                (i18n.tr("No audio"),
                 [float(acm["practice"]["without"]),
                  float(acm["drill"]["without"])]),
            ],
            [i18n.tr("Practice"), i18n.tr("Drill")],
            title=i18n.tr("Audio coverage"))

        # (6) Phrase funnel (practice → errors → review → drill → pass).
        funnel = advance_analysis.phrase_funnel(takes, drill_sessions, review_phrases)
        stages = funnel["stages"]
        labels = [i18n.tr(label) for label, _ in stages]
        funnel_chart = chart(cell(2, 1))
        funnel_chart.draw_bars(
            [float(count) for _, count in stages],
            labels,
            title=i18n.tr("Phrase funnel"))

        # (7) Day correlation between activity types.
        corr = advance_analysis.activity_correlation(sessions, drill_sessions,
                                       dictation_sessions)
        corr_items = [
            (i18n.tr("Practice-Drill"),
             corr["practice_drill"] * 100.0 if corr["practice_drill"] is not None else 0.0),
            (i18n.tr("Practice-Dictation"),
             corr["practice_dictation"] * 100.0 if corr["practice_dictation"] is not None else 0.0),
            (i18n.tr("Drill-Dictation"),
             corr["drill_dictation"] * 100.0 if corr["drill_dictation"] is not None else 0.0),
        ]
        corr_chart = chart(cell(3, 0))
        corr_chart.draw_hbars(corr_items,
                              title=i18n.tr("Day correlation ({d} days)"
                                            .format(d=corr["days"])),
                              color=THEME["accent"])

        # (8) Join gotchas note (rendered as a short list).
        note = tk.Label(self._advance,
                        text=i18n.tr("Join notes") + ":",
                        font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                        bg=THEME["bg_main"], fg=THEME["text_emph"])
        note.pack(anchor="w", pady=(8, 2))
        for gotcha in advance_analysis.JOIN_GOTCHAS:
            tk.Label(self._advance, text="• " + i18n.tr(gotcha),
                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                     fg=THEME["text_dim"], bg=THEME["bg_main"]
                     ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

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

    def _on_export_report_png(self) -> None:
        if self._cb.export_report_png is None:
            return
        path = self._cb.export_report_png()
        if path:
            messagebox.showinfo(i18n.tr("Export complete"),
                                i18n.tr("Report saved to:\n{path}", path=path),
                                parent=self._window)

    def _on_export_report_pdf(self) -> None:
        if self._cb.export_report_pdf is None:
            return
        path = self._cb.export_report_pdf()
        if path:
            messagebox.showinfo(i18n.tr("Export complete"),
                                i18n.tr("Report saved to:\n{path}", path=path),
                                parent=self._window)


def _compute_streak(sessions: List[Dict],
                    target_minutes: int) -> int:
    """Count consecutive days (ending today or yesterday) that hit
    the daily target.

    Uses session ``started_at`` for the day key and ``duration_sec``
    for the daily total. A missing day breaks the streak.
    """
    if not sessions or target_minutes <= 0:
        return 0
    day_seconds: Dict[str, int] = defaultdict(int)
    for s in sessions:
        try:
            day = datetime.fromisoformat(s.get("started_at") or "").strftime(
                "%Y-%m-%d")
        except ValueError:
            continue
        day_seconds[day] += int(s.get("duration_sec") or 0)
    target_sec = target_minutes * 60
    streak = 0
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = datetime.now().date()
    # Allow yesterday to count (today may not have practice yet).
    if today not in day_seconds and (cursor - timedelta(days=1)).strftime(
            "%Y-%m-%d") in day_seconds:
        cursor -= timedelta(days=1)
    while True:
        day_key = cursor.strftime("%Y-%m-%d")
        if day_seconds.get(day_key, 0) >= target_sec:
            streak += 1
            cursor -= timedelta(days=1)
        else:
            break
    return streak


def _type_name(target_type: str) -> str:
    """The i18n display name of a drill target type.

    The drill engine's stored types are ``phoneme`` / ``word`` /
    ``single_word`` / ``free_form``; the charts label them with these
    short names (the full i18n keys live in the drill tab's key block).
    """
    names = {
        "phoneme": "Phoneme",
        "word": "Word",
        "single_word": "Single word",
        "free_form": "Free form",
    }
    return names.get(target_type, target_type)


def _type_label(target_type: str, fallback: str) -> str:
    """The i18n label for a target type, defaulting to ``fallback``.

    Used by the stacked-bar and line-series legends so a type that the
    engine has never stored still gets a stable label instead of an empty
    legend entry.
    """
    name = _type_name(target_type)
    if name == target_type:
        return fallback
    return i18n.tr(name)
