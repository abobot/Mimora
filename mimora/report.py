# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Offline progress-report renderer (P2#7).

Draws the same practice statistics the stats dashboard shows - summary
cards, a daily-average score trend line and a score-distribution bar chart
- onto a Pillow image, and writes it out as PNG or PDF. Pure PIL, no Tk, so
it can run headless and is unit-testable without a display.

Pillow already ships with Mimora (the face widget renders with it), and PIL
can emit PDF natively (``Image.save(..., format='PDF')``), so no Ghostscript
or matplotlib dependency is added.

The module is deliberately GUI-free and imports nothing from ``ui_stats``:
it re-implements only the small data aggregation (daily averages, buckets)
that the dashboard performs, so the report stays a faithful snapshot even if
the on-screen charts change.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Layout constants (logical pixels; the image is rendered 2x for crispness).
# ---------------------------------------------------------------------------
_PX = 2  # supersampling factor


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """A monospace-free, portable font.

    Prefers DejaVu Sans (bundled with many Pillow distributions); falls back
    to Pillow's default bitmap font when no TrueType font is available, which
    keeps the module importable in the smallest environments.
    """
    import os
    candidates = []
    if os.name == "nt":
        candidates = [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    bold_candidates = [c.replace("Sans", "Sans-Bold").replace(
        "LiberationSans-Regular", "LiberationSans-Bold") for c in candidates]
    pool = bold_candidates if bold else candidates
    for path in pool:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# A fixed palette so the PNG/PDF report is legible regardless of the GUI theme.
_BG = (245, 245, 248)
_PANEL = (255, 255, 255)
_BORDER = (210, 210, 218)
_TEXT = (35, 35, 42)
_TEXT_DIM = (120, 120, 130)
_GOOD = (30, 158, 74)
_BAD = (211, 47, 47)
_GRID = (228, 228, 234)


def _fmt_seconds(seconds: int) -> str:
    """1440 → '2h 24m'; 60 → '1m'; 0 → '0m'."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return f"{hours}h {rem}m"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h"


def _daily_averages(takes: List[Dict[str, Any]],
                    lookback: int = 30) -> List[Tuple[str, float]]:
    """[(YYYY-MM-DD, avg_score)] for the last ``lookback`` days with data."""
    daily: Dict[str, List[float]] = defaultdict(list)
    for t in takes:
        ts = t.get("timestamp") or ""
        try:
            day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except ValueError:
            continue
        daily[day].append(float(t.get("score") or 0))
    days = sorted(daily.keys())[-lookback:]
    return [(d, sum(daily[d]) / len(daily[d])) for d in days]


def _score_buckets(takes: List[Dict[str, Any]]) -> List[int]:
    """[0-24, 25-49, 50-74, 75-100] counts."""
    buckets = [0, 0, 0, 0]
    for t in takes:
        s = float(t.get("score") or 0)
        buckets[min(int(s // 25), 3)] += 1
    return buckets


def _centered(anchor_xy: Tuple[float, float], text: str,
              font: ImageFont.ImageFont,
              above: bool = False) -> Tuple[int, int]:
    """The top-left corner that centers ``text`` on ``anchor_xy``.

    Works with any font, including Pillow's default bitmap font, which
    does not support the ``anchor`` parameter of ``ImageDraw.text``.
    """
    try:
        left, top, right, bottom = font.getbbox(text)
    except Exception:
        left, top, right, bottom = 0, 0, len(text) * 6, 12
    w = right - left
    h = bottom - top
    x = int(anchor_xy[0] - w / 2 - left)
    if above:
        y = int(anchor_xy[1] - h - top)
    else:
        y = int(anchor_xy[1] - top)
    return x, y


def _cards(sessions: List[Dict[str, Any]],
           takes: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """The summary card (title, value) pairs the dashboard shows."""
    total_time = sum(int(s.get("duration_sec") or 0) for s in sessions)
    scores = [float(t.get("score") or 0) for t in takes if t.get("score")]
    avg = sum(scores) / len(scores) if scores else 0.0
    best = max(scores) if scores else 0.0
    return [
        ("Practice time", _fmt_seconds(total_time)),
        ("Sessions", str(len(sessions))),
        ("Takes", f"{len(takes):,}"),
        ("Avg score", f"{avg:.1f}" if avg else "—"),
        ("Best score", f"{best:.1f}" if best else "—"),
    ]


def _render_trend(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int],
                  daily: List[Tuple[str, float]], font_t: ImageFont.ImageFont,
                  font_l: ImageFont.ImageFont) -> None:
    """Draw the daily-average line chart inside ``box``."""
    x0, y0, x1, y1 = box
    draw.rectangle([x0, y0, x1, y1], fill=_PANEL, outline=_BORDER, width=1)
    pad_l, pad_r, pad_t, pad_b = 40, 10, 30, 22
    pw = x1 - x0 - pad_l - pad_r
    ph = y1 - y0 - pad_t - pad_b
    # Grid lines + y labels (0..100).
    for i in range(5):
        y = y0 + pad_t + ph * i / 4
        draw.line([(x0 + pad_l, y), (x0 + pad_l + pw, y)], fill=_GRID, width=1)
        val = 100 * (1 - i / 4)
        # Right-align the label by measuring it; bitmap fonts have no anchor.
        lx, ly = _centered((x0 + pad_l - 5, y), f"{val:.0f}", font_l)
        draw.text((lx - (x0 + pad_l - 5 - lx), ly), f"{val:.0f}",
                  fill=_TEXT_DIM, font=font_l)
    draw.text((x0 + pad_l, y0 + 4), "Daily avg score (last 30 days)",
              fill=_TEXT, font=font_t)
    if not daily:
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        tx, ty = _centered((cx, cy), "No data yet", font_l)
        draw.text((tx, ty), "No data yet", fill=_TEXT_DIM, font=font_l)
        return
    n = len(daily)
    pts = []
    for i, (_, v) in enumerate(daily):
        x = x0 + pad_l + (pw * i / max(n - 1, 1))
        y = y0 + pad_t + ph * (1 - min(v, 100.0) / 100.0)
        pts.append((x, y))
    if len(pts) >= 2:
        draw.line(pts, fill=_GOOD, width=2)
    for x, y in pts:
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=_GOOD)


def _render_distribution(draw: ImageDraw.ImageDraw,
                         box: Tuple[int, int, int, int],
                         buckets: List[int], font_t: ImageFont.ImageFont,
                         font_l: ImageFont.ImageFont) -> None:
    """Draw the score-distribution bar chart inside ``box``."""
    x0, y0, x1, y1 = box
    draw.rectangle([x0, y0, x1, y1], fill=_PANEL, outline=_BORDER, width=1)
    pad_l, pad_r, pad_t, pad_b = 40, 10, 30, 28
    pw = x1 - x0 - pad_l - pad_r
    ph = y1 - y0 - pad_t - pad_b
    max_v = max(max(buckets or [0]), 1)
    for i in range(5):
        y = y0 + pad_t + ph * i / 4
        draw.line([(x0 + pad_l, y), (x0 + pad_l + pw, y)], fill=_GRID, width=1)
        val = max_v * (1 - i / 4)
        draw.text((x0 + pad_l - 5, y - 6), f"{val:.0f}", fill=_TEXT_DIM,
                  font=font_l, anchor="rm")
    draw.text((x0 + pad_l, y0 + 4), "Score distribution", fill=_TEXT, font=font_t)
    labels = ["0-24", "25-49", "50-74", "75-100"]
    n = len(buckets)
    bar_w = pw / n * 0.7
    for i, v in enumerate(buckets):
        x = x0 + pad_l + pw * (i + 0.5) / n
        y = y0 + pad_t + ph * (1 - v / max_v)
        draw.rectangle([x - bar_w / 2, y, x + bar_w / 2, y0 + pad_t + ph],
                       fill=_GOOD)
        # Center text horizontally under/above each bar by measuring it; the
        # default bitmap font does not support the anchor parameter.
        draw.text(_centered((x, y - 6), f"{v}", font_l, above=True),
                  f"{v}", fill=_TEXT, font=font_l)
        draw.text(_centered((x, y0 + pad_t + ph + 4),
                            labels[i] if i < len(labels) else "", font_l),
                  labels[i] if i < len(labels) else "",
                  fill=_TEXT_DIM, font=font_l)


def build_report_image(sessions: List[Dict[str, Any]],
                       takes: List[Dict[str, Any]],
                       title: Optional[str] = None) -> Image.Image:
    """Render a progress-report image (PNG/PDF-able) from stats data.

    ``sessions`` are session dicts (``duration_sec``, ``started_at``);
    ``takes`` are take dicts (``score``, ``timestamp``). The layout mirrors
    the on-screen dashboard: cards row, trend chart, distribution chart.
    """
    width, height = 1000, 640
    img = Image.new("RGB", (width * _PX, height * _PX), _BG)
    draw = ImageDraw.Draw(img)
    title_font = _font(22, bold=True)
    sub_font = _font(14)
    body_font = _font(13)
    cap_font = _font(11)

    def s(v):
        return v * _PX

    # Header.
    draw.text((s(24), s(20)), title or "Mimora progress report",
              fill=_TEXT, font=title_font)
    draw.text((s(24), s(58)),
              f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
              fill=_TEXT_DIM, font=cap_font)

    # Cards.
    cards = _cards(sessions, takes)
    card_w = (width - 24 * 2 - 8 * (len(cards) - 1)) // len(cards)
    y_card = 90
    for i, (c_title, c_value) in enumerate(cards):
        cx = 24 + i * (card_w + 8)
        draw.rectangle([s(cx), s(y_card), s(cx + card_w), s(y_card + 80)],
                       fill=_PANEL, outline=_BORDER, width=1)
        draw.text((s(cx + 10), s(y_card + 14)), c_title,
                  fill=_TEXT_DIM, font=cap_font)
        draw.text((s(cx + 10), s(y_card + 40)), c_value,
                  fill=_TEXT, font=body_font)

    # Charts.
    chart_y = 190
    trend_box = (24, chart_y, width // 2 - 8, chart_y + 200)
    dist_box = (width // 2 + 8, chart_y, width - 24, chart_y + 200)
    daily = _daily_averages(takes)
    buckets = _score_buckets(takes)
    _render_trend(draw, [s(v) for v in trend_box], daily, title_font, cap_font)
    _render_distribution(draw, [s(v) for v in dist_box], buckets,
                         title_font, cap_font)

    # Recent sessions footer.
    footer_y = chart_y + 216
    draw.text((s(24), s(footer_y)), "Recent sessions", fill=_TEXT,
              font=sub_font)
    fy = footer_y + 28
    for session in reversed(sessions[-6:]):
        totals = session.get("totals") or {}
        started = (session.get("started_at") or "")[:16].replace("T", " ")
        avg_text = f"{float(totals.get('avg_best_score') or 0):.0f}"
        text = (f"{started}  ·  {totals.get('takes', 0)} takes  ·  "
                f"avg {avg_text}  ·  "
                f"{_fmt_seconds(int(session.get('duration_sec') or 0))}")
        draw.text((s(24), s(fy)), text, fill=_TEXT, font=cap_font)
        fy += 22
        if fy > height - 20:
            break

    if _PX != 1:
        img = img.resize((width, height), Image.LANCZOS)
    return img


def render_report_png(sessions: List[Dict[str, Any]],
                      takes: List[Dict[str, Any]],
                      title: Optional[str] = None) -> bytes:
    """Render the report and return PNG bytes."""
    img = build_report_image(sessions, takes, title=title)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_report_pdf(sessions: List[Dict[str, Any]],
                      takes: List[Dict[str, Any]],
                      title: Optional[str] = None) -> bytes:
    """Render the report and return PDF bytes (PIL native PDF writer)."""
    img = build_report_image(sessions, takes, title=title)
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()
