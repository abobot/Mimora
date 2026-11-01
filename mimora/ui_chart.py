# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Shared Tk canvas chart helpers for the stats dashboard.

Charts are drawn on ``tk.Canvas`` because Mimora is a Tkinter app - no
matplotlib, no third-party plotting. The helpers are small and
self-contained.

:class:`_CanvasChart` and the ``heatmap_*`` functions were split out of
:mod:`mimora.ui_stats` so the view file can hold the tab rendering and the
chart primitives live on their own. ``ui_stats`` re-exports them for
backward compatibility.
"""

from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mimora import i18n
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
)


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
            c.create_line(pad_l, y, pad_l + plot_w, y,
                          fill=THEME["chart_grid"])
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
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
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

    def draw_stacked_bars(self, series: List[Tuple[str, List[float]]],
                          labels: List[str],
                          title: str = "") -> None:
        """Draw a stacked bar chart.

        ``series`` is a list of ``(key, [values...])`` where each entry is one
        stack segment (drawn bottom-up in list order) and the values are
        aligned with ``labels`` (one bar per label). A segment with all-zero
        values is skipped so a never-present error kind does not draw a
        phantom band. The value label above each bar shows the stack total.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 32, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        # Totals are per LABEL (one stack per label); segments contribute to
        # the label's stack, so the length is len(labels), not len(series).
        totals = [sum((vals[i] if i < len(vals) else 0.0)
                      for _, vals in series)
                  for i in range(len(labels))]
        max_value = max(max(totals or [0.0]), 1.0)
        # Grid.
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if not labels:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        n = len(labels)
        bar_w = plot_w / n * 0.7
        for i, label in enumerate(labels):
            x = pad_l + plot_w * (i + 0.5) / n
            # Stack bottom-up.
            y_bottom = pad_t + plot_h
            for _, vals in series:
                v = vals[i] if i < len(vals) else 0.0
                if v <= 0:
                    continue
                h = plot_h * v / max_value
                y_top = y_bottom - h
                c.create_rectangle(x - bar_w / 2, y_top,
                                   x + bar_w / 2, y_bottom,
                                   fill=THEME["good"], outline="")
                y_bottom = y_top
            # Stack total above the bar.
            if totals[i] > 0:
                y = pad_t + plot_h * (1 - totals[i] / max_value)
                c.create_text(x, y - 4, text=f"{totals[i]:.0f}", anchor="s",
                              fill=THEME["text_emph"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            # Label below bar.
            c.create_text(x, pad_t + plot_h + 4, text=label, anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_hbars(self, items: List[Tuple[str, float]],
                   title: str = "", color: Optional[str] = None) -> None:
        """Draw a horizontal bar chart (one bar per label, best first).

        ``items`` is a list of ``(label, value)`` with the largest value
        first. The bar for ``items[0]`` is drawn at the TOP of the plot, so
        a Top-N list reads top-down from the most frequent. The label is
        drawn to the left of the bar; the value to the right of it.
        """
        c = self.canvas
        c.delete("all")
        color = color or THEME["good"]
        pad_l, pad_r, pad_t, pad_b = 72, 40, 12, 12
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        if not items:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        max_value = max(max(v for _, v in items), 1.0)
        n = len(items)
        bar_h = plot_h / n * 0.6
        for i, (label, v) in enumerate(items):
            y = pad_t + plot_h * (i + 0.5) / n
            w = plot_w * v / max_value
            c.create_rectangle(pad_l, y - bar_h / 2, pad_l + w, y + bar_h / 2,
                               fill=color, outline="")
            # Label left of the bar (ellipsised by the caller if needed).
            c.create_text(pad_l - 6, y, text=label, anchor="e",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            # Value right of the bar.
            c.create_text(pad_l + w + 6, y, text=f"{v:.0f}", anchor="w",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_stacked_histogram(self, bins: List[Tuple[str, int, int]],
                               title: str = "") -> None:
        """Draw a stacked pass/fail histogram of take scores.

        ``bins`` is ``[(bin_label, passed_count, failed_count)]`` from
        :func:`mimora.dictation_analysis.score_distribution` - the score axis
        split into equal bins, each split by the take-level ``passed`` flag.
        A failed (red) segment sits under a passed (green) segment, so the
        pass threshold shows as the colour boundary between the bins.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 32, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        totals = [p + f for _, p, f in bins]
        max_value = max(max(totals or [0.0]), 1.0)
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if not bins:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        n = len(bins)
        bar_w = plot_w / n * 0.7
        for i, (label, p, f) in enumerate(bins):
            x = pad_l + plot_w * (i + 0.5) / n
            y_bottom = pad_t + plot_h
            # Failed (red) segment first, then passed (green) above it.
            if f:
                h = plot_h * f / max_value
                c.create_rectangle(x - bar_w / 2, y_bottom - h,
                                   x + bar_w / 2, y_bottom,
                                   fill=THEME["bad"], outline="")
                y_bottom -= h
            if p:
                h = plot_h * p / max_value
                c.create_rectangle(x - bar_w / 2, y_bottom - h,
                                   x + bar_w / 2, y_bottom,
                                   fill=THEME["good"], outline="")
            if totals[i] > 0:
                y = pad_t + plot_h * (1 - totals[i] / max_value)
                c.create_text(x, y - 4, text=f"{totals[i]:.0f}", anchor="s",
                              fill=THEME["text_emph"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            c.create_text(x, pad_t + plot_h + 4, text=label, anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_line_series(self, series: List[Tuple[str, List[Optional[float]], str]],
                         labels: List[str],
                         max_value: float = 100.0,
                         title: str = "",
                         legend: bool = True) -> None:
        """Draw one or more coloured line series over shared x labels.

        ``series`` is ``[(name, [values...], color)]`` and each value list is
        aligned with ``labels``. A ``None`` value is a gap: the line is
        broken there (the point is not drawn), so a target that skipped a
        week does not connect across the hole. Points are drawn as small
        dots; the series names are rendered as a compact legend along the
        top-right (set ``legend=False`` to omit it — for many series the
        legend would overflow). Used for the per-attempt pass-rate curve
        (one series), the error-type three-line trend, and the same-target
        progress curves (with gaps).
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 32, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if not labels or not any(any(v is not None for v in s[1]) for s in series):
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        n = len(labels)
        for name, values, color in series:
            points = []
            for i, v in enumerate(values):
                if i >= n:
                    break
                if v is None:
                    continue
                x = pad_l + plot_w * i / max(n - 1, 1)
                y = pad_t + plot_h * (1 - min(v, max_value) / max_value)
                points.append((x, y))
            # Draw line segments only across consecutive present points, so
            # a gap (None) breaks the line instead of connecting across it.
            for a, b in zip(points, points[1:]):
                c.create_line(a[0], a[1], b[0], b[1], fill=color, width=2)
            for x, y in points:
                c.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                              fill=color, outline="")
        # Legend along the top-right (omitted for many-series charts).
        if legend:
            legend_x = self._width - pad_r
            for name, _, color in series:
                c.create_line(legend_x - 40, 12, legend_x - 24, 12,
                              fill=color, width=2)
                c.create_text(legend_x - 20, 12, text=name, anchor="e",
                              fill=THEME["text_dim"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION))
                legend_x -= 20 + len(name) * 7 + 20
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_scatter(self, points: List[Tuple[float, float]],
                     title: str = "",
                     x_label: str = "",
                     y_label: str = "",
                     max_x: Optional[float] = None,
                     max_y: float = 100.0) -> None:
        """Draw a scatter plot of ``(x, y)`` points.

        Used by the drill investment-vs-outcome chart: x is total attempts,
        y is the last score. The x-axis scales to the data (or ``max_x`` if
        given); the y-axis is 0..``max_y`` (scores are 0-100). A point is a
        small filled dot; the axes are labelled.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 36, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        if not points:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_y * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if max_x is None:
            max_x = max(max(x for x, _ in points), 1.0)
        for i in range(5):
            x = pad_l + plot_w * i / 4
            val = max_x * i / 4
            c.create_text(x, pad_t + plot_h + 4, text=f"{val:.0f}", anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        for x, y in points:
            px = pad_l + plot_w * min(x, max_x) / max_x
            py = pad_t + plot_h * (1 - min(y, max_y) / max_y)
            c.create_oval(px - 3, py - 3, px + 3, py + 3,
                          fill=THEME["accent"], outline="")
        if x_label:
            c.create_text(pad_l + plot_w / 2, pad_t + plot_h + 16,
                          text=x_label, anchor="n", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if y_label:
            c.create_text(8, pad_t + plot_h / 2, text=y_label, angle=90,
                          anchor="n", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_sankey(self, nodes: List[Tuple[str, Any, float, int]],
                    links: List[Tuple[str, str, float]],
                    title: str = "") -> None:
        """Draw a compact Sankey-style flow diagram.

        ``nodes`` is ``[(node_id, label, value, column)]`` and ``links`` is
        ``[(src_id, dst_id, value)]`` - the shape
        :func:`mimora.advance_analysis.word_flow_nodes` returns. Each column
        is a stage; the nodes in it are stacked top-down proportionally to
        their ``value``, and each link is a filled trapezoid from its source
        node to its target node (drawn with an interpolated colour between
        the two column fills). Node values are the label suffix; a link's
        value is drawn on the ribbon only when it is large enough to fit.
        """
        c = self.canvas
        c.delete("all")
        if not nodes or not links:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        pad_l, pad_r, pad_t, pad_b = 8, 8, 12, 24
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        columns: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for node_id, label, value, column in nodes:
            columns[column].append({
                "id": node_id, "label": label, "value": value,
                "column": column,
            })
        col_ids = sorted(columns)
        n_cols = max(len(col_ids), 1)
        gap = 60
        node_w = min(90, (plot_w - gap * (n_cols - 1)) // n_cols)
        # Per-column colour ramp (info -> accent -> good).
        palette = [THEME["info"], THEME["accent"], THEME["good"]]
        col_x: Dict[int, int] = {}
        for i, col in enumerate(col_ids):
            col_x[col] = pad_l + i * (node_w + gap)
        # Stack nodes within a column, proportional to value.
        node_rects: Dict[str, Tuple[float, float, float, float]] = {}
        for col in col_ids:
            total = sum(n["value"] for n in columns[col]) or 1.0
            y = pad_t
            for n in columns[col]:
                h = plot_h * n["value"] / total
                node_rects[n["id"]] = (
                    col_x[col], y, col_x[col] + node_w, y + h)
                y += h
        # Draw links first (under the nodes), then node boxes on top.
        for src, dst, value in links:
            if src not in node_rects or dst not in node_rects:
                continue
            sx0, sy0, sx1, sy1 = node_rects[src]
            dx0, dy0, dx1, dy1 = node_rects[dst]
            # Link occupies the vertical slice of the source node
            # proportional to its value share of that node's total out-flow.
            src_out = sum(v for s, _, v in links if s == src) or value
            out_share = value / src_out if src_out else 0.0
            src_h = sy1 - sy0
            src_top = sy0 + src_h * (1 - out_share)
            # Destination takes the matching share of its in-flow.
            dst_in = sum(v for _, d, v in links if d == dst) or value
            in_share = value / dst_in if dst_in else 0.0
            dst_h = dy1 - dy0
            dst_top = dy1 - dst_h * in_share
            # Ribbon between source right edge and target left edge.
            c.create_polygon(
                sx1, src_top, sx1, src_top + src_h * out_share,
                dx0, dst_top, dx0, dst_top + dst_h * in_share,
                fill=palette[1], outline="")
        for node_id, label, value, column in nodes:
            x0, y0, x1, y1 = node_rects[node_id]
            color = palette[min(column, len(palette) - 1)]
            c.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            mid_y = (y0 + y1) / 2
            if y1 - y0 > 14:
                c.create_text(x0 + node_w / 2, mid_y - 5, text=label,
                              anchor="n", fill=THEME["text_bright"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION))
                c.create_text(x0 + node_w / 2, mid_y + 3, text=f"{value:.0f}",
                              anchor="n", fill=THEME["text_bright"],
                              font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_scatter_diagonal(self, points: List[Tuple[float, float]],
                              title: str = "",
                              x_label: str = "",
                              y_label: str = "",
                              max_value: float = 100.0) -> None:
        """Draw a scatter plot with the y=x diagonal reference line.

        Used by the advance tab to compare a drill take's score against the
        practice take it references (both 0-100). The diagonal runs from
        bottom-left to top-right at the same scale on both axes, so a point
        exactly on the line is "consistent"; above it the drill scored higher
        than the practice take, below it lower. Axis scales are shared
        (0..``max_value``) so the diagonal is a true 45-degree line.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 36, 8, 12, 28
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        if not points:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        for i in range(5):
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, pad_l + plot_w, y, fill=THEME["chart_grid"])
        for i in range(5):
            y = pad_t + plot_h * i / 4
            val = max_value * (1 - i / 4)
            c.create_text(pad_l - 4, y, text=f"{val:.0f}", anchor="e",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        for i in range(5):
            x = pad_l + plot_w * i / 4
            val = max_value * i / 4
            c.create_text(x, pad_t + plot_h + 4, text=f"{val:.0f}", anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        # Diagonal (y=x) reference line, dashed grey.
        c.create_line(pad_l, pad_t + plot_h, pad_l + plot_w, pad_t,
                      fill=THEME["text_dim"], dash=(4, 4))
        for x, y in points:
            px = pad_l + plot_w * min(x, max_value) / max_value
            py = pad_t + plot_h * (1 - min(y, max_value) / max_value)
            c.create_oval(px - 3, py - 3, px + 3, py + 3,
                          fill=THEME["accent"], outline="")
        if x_label:
            c.create_text(pad_l + plot_w / 2, pad_t + plot_h + 16,
                          text=x_label, anchor="n", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if y_label:
            c.create_text(8, pad_t + plot_h / 2, text=y_label, angle=90,
                          anchor="n", fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_heatmap_matrix(self, rows: List[str], cols: List[str],
                            matrix: List[List[int]],
                            palette: List[str],
                            title: str = "") -> None:
        """Draw a generic cell heatmap (rows x cols) with a colour scale.

        ``matrix[i][j]`` is the value for ``rows[i]`` in ``cols[j]``; each
        cell is coloured by the quantile of the non-zero values mapped to
        ``palette`` (an N-entry colour list, 0 = empty). Row labels sit to
        the left, column labels above, and the value is drawn inside cells
        that are non-zero.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 76, 8, 22, 12
        cell = 18
        gap = 2
        if not rows or not cols or not matrix:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        # Scale cells down if the grid would overflow the canvas.
        cell_w = min(cell, max(6, (plot_w - len(cols) * gap) // len(cols)))
        cell_h = min(cell, max(6, (plot_h - len(rows) * gap) // len(rows)))
        # Quantile thresholds over non-zero values.
        nonzero = sorted(v for row in matrix for v in row if v > 0)
        n_levels = len(palette)

        def level(v: float) -> int:
            if v <= 0:
                return 0
            if not nonzero:
                return 1
            # Rank among non-zero values -> 1..n_levels-1.
            pos = sum(1 for x in nonzero if x < v) / len(nonzero)
            return min(n_levels - 1, 1 + int(pos * (n_levels - 1)))

        x0 = pad_l
        for j, col in enumerate(cols):
            x = x0 + j * (cell_w + gap)
            # Column label (week) above the grid.
            c.create_text(x + cell_w / 2, pad_t - 6, text=col, anchor="n",
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        for i, row in enumerate(rows):
            y = pad_t + i * (cell_h + gap)
            # Row label (word) to the left.
            c.create_text(pad_l - 6, y + cell_h / 2, text=row, anchor="e",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            for j in range(len(cols)):
                v = matrix[i][j] if j < len(matrix[i]) else 0
                lvl = level(v)
                fill = palette[lvl] if lvl < len(palette) else palette[-1]
                x = x0 + j * (cell_w + gap)
                c.create_rectangle(x, y, x + cell_w, y + cell_h,
                                   fill=fill, outline=fill)
                if v > 0:
                    c.create_text(x + cell_w / 2, y + cell_h / 2,
                                  text=str(v), fill=THEME["text_bright"],
                                  font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_dumbbell(self, items: List[Tuple[str, float, float]],
                      title: str = "") -> None:
        """Draw a dumbbell / slope chart.

        ``items`` is ``[(label, value_a, value_b)]`` in display order; each
        row is one horizontal line from ``value_a`` to ``value_b`` (both
        clamped 0-100), coloured green when ``value_b >= value_a`` (progress)
        and red otherwise. A degenerate point (``value_a == value_b``) draws
        as a single outlined circle so it reads as "no change" rather than a
        flat line.
        """
        c = self.canvas
        c.delete("all")
        pad_l, pad_r, pad_t, pad_b = 72, 40, 12, 12
        plot_w = self._width - pad_l - pad_r
        plot_h = self._height - pad_t - pad_b
        if not items:
            c.create_text(self._width / 2, self._height / 2,
                          text=i18n.tr("No data"), fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_BODY))
            return
        n = len(items)
        for i, (label, a, b) in enumerate(items):
            y = pad_t + plot_h * (i + 0.5) / n
            xa = pad_l + plot_w * min(max(a, 0.0), 100.0) / 100.0
            xb = pad_l + plot_w * min(max(b, 0.0), 100.0) / 100.0
            color = THEME["good"] if b >= a else THEME["bad"]
            if abs(xa - xb) < 1.0:
                # Degenerate: a single outlined dot (no change).
                c.create_oval(xa - 4, y - 4, xa + 4, y + 4,
                              fill=THEME["bg_main"], outline=color, width=2)
            else:
                c.create_line(xa, y, xb, y, fill=color, width=2)
                c.create_oval(xa - 3, y - 3, xa + 3, y + 3,
                              fill=color, outline="")
                c.create_oval(xb - 3, y - 3, xb + 3, y + 3,
                              fill=color, outline="")
            c.create_text(pad_l - 6, y, text=label, anchor="e",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
        if title:
            c.create_text(pad_l, 6, text=title, anchor="nw",
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"))

    def draw_kpi_cards(self, cards: List[Tuple[str, str]]) -> None:
        """Draw a compact KPI card row: label above, value below.

        ``cards`` is ``[(label, value_string)]`` in display order. The cards
        are laid out left-to-right and wrap to the next row when they exceed
        the canvas width, so a 4-card row fits on the dashboard without a
        scroll.
        """
        c = self.canvas
        c.delete("all")
        pad = 8
        card_w = (self._width - pad * 2 - 12) // 4
        card_h = 40
        x = pad
        y = pad
        for i, (label, value) in enumerate(cards):
            if i > 0 and i % 4 == 0:
                x = pad
                y += card_h + 6
            c.create_rectangle(x, y, x + card_w, y + card_h,
                               fill=THEME["bg_card"], outline=THEME["border"])
            c.create_text(x + card_w / 2, y + 12, text=label,
                          fill=THEME["text_dim"],
                          font=(FONT_FAMILY, FONT_SIZE_CAPTION))
            c.create_text(x + card_w / 2, y + 28, text=value,
                          fill=THEME["text_emph"],
                          font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"))
            x += card_w + 6

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


GITHUB_HEATMAP_LEVELS = THEME["heatmap_levels"]

# Text colour for the day-of-month number inside a heatmap cell, indexed by
# the same 0-4 level as GITHUB_HEATMAP_LEVELS. The lightest green (level 4)
# needs dark text for contrast; everything else reads best in white.
_HEAT_TEXT_COLORS = THEME["heatmap_text"]

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
