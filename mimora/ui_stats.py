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
from datetime import datetime, timedelta
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional


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

    def draw_heatmap(self, values: List[float], n_cols: int = 7,
                     cell: int = 18, label: str = "") -> None:
        """Draw a calendar-style heatmap: ``values`` one cell per value,
        laid out left-to-right, top-to-bottom in ``n_cols`` columns.

        Each cell is coloured by intensity: empty (0) stays neutral, low
        values a light accent, high values the good colour. Useful for
        the daily-activity calendar and per-day duration view.
        """
        c = self.canvas
        c.delete("all")
        pad = 8
        if not values:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        max_v = max(values) or 1.0
        for i, v in enumerate(values):
            col = i % n_cols
            row = i // n_cols
            x = pad + col * (cell + 2)
            y = pad + row * (cell + 2)
            if v <= 0:
                fill = THEME["border"]
            else:
                ratio = min(v / max_v, 1.0)
                # Blend neutral → good by intensity.
                fill = _blend_color(THEME["border"], THEME["good"], ratio)
            c.create_rectangle(x, y, x + cell, y + cell,
                               fill=fill, outline=THEME["border"])
        if label:
            c.create_text(self._width / 2, self._height - 6,
                          text=label, anchor="s",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))


def _blend_color(c1: str, c2: str, ratio: float) -> str:
    """Blend two '#rrggbb' colours toward c2 by ``ratio`` (0..1)."""
    try:
        r1, g1, b1 = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
        r2, g2, b2 = (int(c2[i:i + 2], 16) for i in (1, 3, 5))
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, TypeError):
        return c1


class StatsView:
    """Toplevel showing the practice stats dashboard."""

    def __init__(self, root, callbacks: StatsCallbacks):
        self.root = root
        self._cb = callbacks
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
        """Render the practice-activity heatmap (calendar) into the charts area.

        Each day in the last 28 days is a cell, coloured by the total practice
        seconds that day (empty days stay neutral). Drawn as a second row
        beneath the trend and distribution charts.
        """
        for child in self._activity.winfo_children():
            child.destroy()
        # Daily total practice seconds, last 28 days ending today.
        day_sec: Dict[str, int] = defaultdict(int)
        for s in sessions:
            key = _session_date_key(s)
            if key:
                day_sec[key] += int(s.get("duration_sec") or 0)
        today = datetime.now().date()
        values = []
        days = []
        for offset in range(27, -1, -1):
            d = today - timedelta(days=offset)
            key = d.strftime("%Y-%m-%d")
            values.append(day_sec.get(key, 0))
            days.append(key)
        heat = _CanvasChart(self._activity, width=430, height=110)
        heat.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        heat.draw_heatmap(values, n_cols=7, label=i18n.tr("Practice minutes (last 28 days)"))

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
