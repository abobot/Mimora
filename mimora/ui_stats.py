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
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
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


class _CanvasChart:
    """Tiny helper: draw bars or lines on a Canvas without re-writing
    the layout each time.

    The chart is drawn once per data refresh; no interaction. Kept
    simple on purpose.
    """

    def __init__(self, master, width=360, height=140, bg=None):
        self.bg = bg or THEME["bg_panel"]
        self.canvas = tk.Canvas(master, width=width, height=height,
                                bg=self.bg, highlightthickness=1,
                                highlightbackground=THEME["border"])
        self._width = width
        self._height = height

    def draw_line(self, values: List[float],
                  max_value: float = 100.0,
                  color: Optional[str] = None,
                  label: str = "") -> None:
        """Draw a line chart with ``values`` on the y-axis 0..max_value."""
        c = self.canvas
        c.delete("all")
        color = color or THEME["good"]
        pad_l, pad_r, pad_t, pad_b = 32, 8, 12, 24
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        # Grid lines.
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["border"])
        # y-axis labels.
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if not values:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data yet"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        # Data points.
        points = []
        n = len(values)
        for i, v in enumerate(values):
            x = pad_l + (plot_w * i / max(n - 1, 1))
            y = pad_t + plot_h * (1 - min(v, max_value) / max_value)
            points.append((x, y))
        if len(points) >= 2:
            flat = [coord for pt in points for coord in pt]
            c.create_line(*flat, fill=color, width=2)
        # Points.
        for x, y in points:
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=color, outline="")
        # Title.
        if label:
            c.create_text(pad_l, 6, text=label, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_bars(self, values: List[float], labels: List[str],
                  max_value: Optional[float] = None,
                  color: Optional[str] = None,
                  title: str = "") -> None:
        """Draw a bar chart with values + labels on the x-axis."""
        c = self.canvas
        c.delete("all")
        color = color or THEME["good"]
        pad_l, pad_r, pad_t, pad_b = 32, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        if max_value is None:
            max_value = max(max(values or [0.0]), 1.0)
        # Grid.
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["border"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if not values:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        n = len(values)
        bar_w = plot_w / n * 0.7
        for i, v in enumerate(values):
            x = pad_l + plot_w * (i + 0.5) / n
            y = pad_t + plot_h * (1 - min(v, max_value) / max_value)
            c.create_rectangle(x - bar_w / 2, y,
                               x + bar_w / 2, pad_t + plot_h,
                               fill=color, outline="")
            # Value above bar.
            c.create_text(x, y - 4, text=f"{v:.0f}", anchor="s",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            # Label below bar.
            c.create_text(x, pad_t + plot_h + 4,
                          text=labels[i] if i < len(labels) else "",
                          anchor="n", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_heatmap(self, values: List[float], cell: int = 24,
                     label: str = "") -> None:
        """Draw a GitHub-contributions style heatmap as a calendar.

        ``values`` holds one cell per day (the last N days ending today).
        The grid is laid out like GitHub's contribution calendar: one
        **column per week**, rows are the days of the week (Mon-Sun, top to
        bottom). Each cell is coloured on GitHub's 5-level green scale
        (quantiles of the non-zero values); empty days stay the dark grey.
        The day-of-month is drawn inside every cell, like a calendar. Month
        labels sit above the columns where a new month starts and weekday
        labels (Mon/Wed/Fri) sit to the left; the whole block is centred in
        the canvas. The caller must give the canvas enough height for the
        month row, all seven day rows and the caption label below.
        """
        c = self.canvas
        c.delete("all")
        pad = 8
        if not values:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        layout = heatmap_layout(values, datetime.today().date())
        cells = layout["cells"]
        grid_start = layout["grid_start"]
        n_weeks = layout["n_weeks"]
        self._draw_heatmap_grid(cells, grid_start, n_weeks, cell, label)

    def draw_heatmap_month(self, day_seconds: Dict[str, int], month: date,
                           cell: int = 24, label: str = "") -> None:
        """Draw a GitHub-style heatmap for one calendar month.

        ``day_seconds`` maps ``"YYYY-MM-DD"`` to practice seconds and
        ``month`` is any date in the month to display. This is the
        month-navigable variant of :meth:`draw_heatmap` used by the
        heatmap's prev/next month arrows: each month is scaled by its own
        non-zero days, and the single month label is drawn in the header.
        """
        c = self.canvas
        c.delete("all")
        pad = 8
        layout = heatmap_month_layout(day_seconds, month)
        cells = layout["cells"]
        grid_start = layout["grid_start"]
        n_weeks = layout["n_weeks"]
        if n_weeks == 0:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        self._draw_heatmap_grid(cells, grid_start, n_weeks, cell, label,
                                month_label=month.month)

    def _draw_heatmap_grid(self, cells: List[Tuple[int, int, int]],
                           grid_start: date, n_weeks: int,
                           cell: int, label: str,
                           month_label: int | None = None) -> None:
        """Shared grid renderer for :meth:`draw_heatmap` and the month
        variant: one **row per week** with the days of the week flowing
        left-to-right (Mon-Sun, like a wall calendar), weekday labels across
        the top, day numbers in the cells, centred block, caption below."""
        c = self.canvas
        pad = 8
        gap = 3
        month_h = 16
        col_lbl_h = 16
        grid_w = 7 * (cell + gap) - gap          # 7 weekday columns
        grid_h = n_weeks * (cell + gap) - gap    # one row per week
        # Centre the whole block (labels + grid) horizontally.
        total_w = grid_w
        grid_x = pad + max(0, (self._width - pad * 2 - total_w) // 2)
        grid_y = pad + month_h + col_lbl_h
        # Month label: drawn above the grid (one month at a time).
        month_abbr = [i18n.tr(m) for m in _MONTH_ABBR]
        label_month = month_label if month_label is not None else grid_start.month
        c.create_text(grid_x, pad, text=month_abbr[label_month - 1],
                      anchor="nw", fill=THEME["text_dim"],
                      font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        # Weekday labels across the top (Mon / Wed / Fri).
        weekday_abbr = [i18n.tr(d) for d in _WEEKDAY_ABBR]
        for col in (0, 2, 4):
            cx = grid_x + col * (cell + gap) + cell / 2
            c.create_text(cx, pad + month_h, text=weekday_abbr[col],
                          anchor="center", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        for week, row, lvl in cells:
            # Transpose: week = row index, row (weekday) = column index, so
            # days flow left-to-right and each week is a horizontal line.
            x = grid_x + row * (cell + gap)
            y = grid_y + week * (cell + gap)
            if lvl < 0:
                # A day outside the displayed month: leave the cell empty so
                # the calendar shows only the month's own days.
                continue
            fill = GITHUB_HEATMAP_LEVELS[lvl]
            c.create_rectangle(x, y, x + cell, y + cell,
                               fill=fill, outline=fill)
            # Day-of-month number inside the cell, like a calendar. The text
            # colour contrasts with the fill (dark text on the lightest green,
            # light text on the empty grey and the darker greens).
            d = grid_start + timedelta(days=week * 7 + row)
            c.create_text(x + cell / 2, y + cell / 2, text=str(d.day),
                          fill=_HEAT_TEXT_COLORS[lvl],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if label:
            c.create_text(self._width / 2, self._height - 6,
                          text=label, anchor="s",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))


GITHUB_HEATMAP_LEVELS = ["#2d333b", "#0e4429", "#006d32", "#26a641", "#39d353"]

# Text colour for the day-of-month number inside a heatmap cell, indexed by
# the same 0-4 level as GITHUB_HEATMAP_LEVELS. The lightest green (level 4)
# needs dark text for contrast; everything else reads best in white.
_HEAT_TEXT_COLORS = ["#c9d1d9", "#ffffff", "#ffffff", "#ffffff", "#0d1117"]

# Month and weekday labels for the heatmap axis. These are passed through
# i18n.tr() so a non-English UI shows the abbreviated names in its own
# language; the English catalog renders them as-is (identity).
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def heatmap_layout(values: List[float], today: date) -> Dict[str, Any]:
    """Pure GitHub-style heatmap geometry (Tk-free).

    ``values`` holds one value per day, oldest first, ending at ``today``.
    Returns a dict with:
      * ``n_weeks``: int — number of week columns,
      * ``grid_start``: date — the Monday the grid is anchored to,
      * ``cells``: List[(week, row, level)] — ``week`` is the column index,
        ``row`` is the weekday (0=Mon .. 6=Sun), ``level`` is 0-4 on GitHub's
        green scale (quantiles of the non-zero values; 0 = no practice).
    The grid is anchored to the Monday of the week containing the first day,
    so leading days occupy their weekday row exactly as GitHub does.
    """
    if not values:
        return {"n_weeks": 0, "grid_start": today, "cells": []}
    nonzero = sorted(v for v in values if v > 0) or [1.0]

    def q(p: float) -> float:
        return nonzero[min(len(nonzero) - 1, int(p * len(nonzero)))]

    thresholds = [q(0.25), q(0.5), q(0.75), q(1.0)]

    def level(v: float) -> int:
        if v <= 0:
            return 0
        # Any non-zero day shows at least the lightest green (GitHub never
        # renders a non-zero value as the empty grey).
        lvl = 1
        for i, t in enumerate(thresholds):
            if v >= t:
                lvl = i + 1
        return lvl

    start_day = today - timedelta(days=len(values) - 1)
    dow = start_day.weekday()                 # 0=Mon .. 6=Sun
    grid_start = start_day - timedelta(days=dow)
    n_weeks = (dow + len(values) + 6) // 7
    cells = []
    for week in range(n_weeks):
        for row in range(7):
            idx = week * 7 + row - dow
            if idx < 0 or idx >= len(values):
                continue
            cells.append((week, row, level(values[idx])))
    return {"n_weeks": n_weeks, "grid_start": grid_start, "cells": cells}


def heatmap_month_layout(day_seconds: Dict[str, int],
                         month: date) -> Dict[str, Any]:
    """GitHub-style heatmap geometry for one calendar month (pure, Tk-free).

    ``day_seconds`` maps ``"YYYY-MM-DD"`` to practice seconds. ``month`` is
    any date in the month (only its year and month matter). The grid is a
    calendar of that month: one **column per week**, rows Mon-Sun, anchored
    to the Monday of the week containing the 1st. Days that spill into the
    week from the adjacent month are **left empty** (level ``-1``, so the
    renderer draws nothing there — the month shows only its own days). The
    green scale is quantiles of the month's non-zero days, so each month is
    scaled independently.

    Returns a dict with ``n_weeks`` (int), ``grid_start`` (date) and
    ``cells`` (list of ``(week, row, level)`` tuples). ``level`` is ``-1``
    for a day outside the month, ``0`` for an in-month day with no practice,
    and 1-4 on the green scale otherwise.
    """
    first = month.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    last = next_first - timedelta(days=1)
    grid_start = first - timedelta(days=first.weekday())
    last_sunday = last + timedelta(days=6 - last.weekday())
    n_weeks = (last_sunday - grid_start).days // 7 + 1

    # Quantile levels over the non-zero days inside this month only.
    in_month = [
        day_seconds.get((first + timedelta(days=i)).strftime("%Y-%m-%d"), 0)
        for i in range((last - first).days + 1)
    ]
    nonzero = sorted(v for v in in_month if v > 0) or [1.0]

    def q(p: float) -> float:
        return nonzero[min(len(nonzero) - 1, int(p * len(nonzero)))]

    thresholds = [q(0.25), q(0.5), q(0.75), q(1.0)]

    def level(v: float) -> int:
        if v <= 0:
            return 0
        lvl = 1
        for i, t in enumerate(thresholds):
            if v >= t:
                lvl = i + 1
        return lvl

    cells = []
    for week in range(n_weeks):
        for row in range(7):
            d = grid_start + timedelta(days=week * 7 + row)
            if first <= d <= last:
                v = day_seconds.get(d.strftime("%Y-%m-%d"), 0)
                cells.append((week, row, level(v)))
            else:
                # Spill from the adjacent month: render nothing.
                cells.append((week, row, -1))
    return {"n_weeks": n_weeks, "grid_start": grid_start, "cells": cells}


def heatmap_cells(values: List[float], today: date) -> List[Tuple[int, int, int]]:
    """GitHub-style heatmap cell layout (pure, Tk-free).

    Returns the ``cells`` part of :func:`heatmap_layout` — a list of
    ``(week, row, level)`` tuples. See :func:`heatmap_layout`.
    """
    return heatmap_layout(values, today)["cells"]


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

        # Summary cards.
        self._cards = tk.Frame(self._window, bg=THEME["bg_main"])
        self._cards.pack(fill=tk.X, padx=16, pady=4)

        # Charts area.
        self._charts = tk.Frame(self._window, bg=THEME["bg_main"])
        self._charts.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # Activity heatmap (calendar of practice minutes) - second chart row.
        # A header holds the prev/next month arrows and the month title; the
        # heatmap itself is redrawn in place when the month changes.
        activity_head = tk.Frame(self._window, bg=THEME["bg_main"])
        activity_head.pack(fill=tk.X, padx=16, pady=(4, 0))
        ttk.Button(activity_head, text=i18n.tr("‹ Prev"),
                   command=self._heatmap_prev_month).pack(side=tk.LEFT)
        self._heatmap_month_var = tk.StringVar()
        tk.Label(activity_head, textvariable=self._heatmap_month_var,
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT, padx=10)
        ttk.Button(activity_head, text=i18n.tr("Next ›"),
                   command=self._heatmap_next_month).pack(side=tk.LEFT)

        self._activity = tk.Frame(self._window, bg=THEME["bg_main"])
        self._activity.pack(fill=tk.X, padx=16, pady=4)

        # Practice-duration table (per day).
        self._duration = tk.Frame(self._window, bg=THEME["bg_main"])
        self._duration.pack(fill=tk.X, padx=16, pady=4)

        # Recent sessions list.
        self._recent = tk.Frame(self._window, bg=THEME["bg_main"])
        self._recent.pack(fill=tk.X, padx=16, pady=(0, 12))

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
        self._render_recent(sessions)

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
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
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
