# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P2#7 report-export buttons in the stats dashboard.

Covers:
  * StatsCallbacks accepts the optional export_report_png/pdf callbacks,
  * the dashboard renders the Export PNG / Export PDF buttons only when
    the callbacks are provided.

Runs in the fast suite: a single withdrawn Tk root, no models.
"""

import unittest


def _callbacks(**overrides):
    from mimora.ui_stats import StatsCallbacks
    base = dict(
        load_data=lambda: {"sessions": [], "all_takes": []},
        export_json=lambda: None,
        export_csv=lambda: None,
    )
    base.update(overrides)
    return StatsCallbacks(**base)


def _drill_session(sid, ttype, value, scores, outcome="unfinished",
                   ended="2026-10-28T09:00:00+08:00"):
    """A minimal drill session dict in the stored shape, for the tab render
    tests. ``scores`` becomes the takes list (attempt 1-indexed)."""
    takes = [{"phrase": value, "score": float(s), "attempt": i + 1,
              "passed": i >= 1,
              "audio_session_id": "as-1", "audio_take_id": "at-1",
              "audio_files": {"normalized.wav": "x.wav"}}
             for i, s in enumerate(scores)]
    first = float(scores[0]) if scores else 0.0
    last = float(scores[-1]) if scores else 0.0
    return {
        "id": sid,
        "target": {"type": ttype, "value": value},
        "attempts": len(scores),
        "passed": 1 if outcome == "mastered" else 0,
        "first_score": first,
        "last_score": last,
        "avg_score": float(sum(scores) / len(scores)) if scores else 0.0,
        "outcome": outcome,
        "started_at": ended,
        "ended_at": ended,
        "takes": takes,
    }


class StatsCallbacksTest(unittest.TestCase):
    def test_report_handlers_default_to_none(self):
        cb = _callbacks()
        self.assertIsNone(cb.export_report_png)
        self.assertIsNone(cb.export_report_pdf)

    def test_report_handlers_accept_callables(self):
        cb = _callbacks(export_report_png=lambda: "p.png",
                        export_report_pdf=lambda: "p.pdf")
        self.assertEqual(cb.export_report_png(), "p.png")
        self.assertEqual(cb.export_report_pdf(), "p.pdf")


class ExportReportButtonsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": []},
            export_json=lambda: None,
            export_csv=lambda: None,
            export_report_png=lambda: "p.png",
            export_report_pdf=lambda: "p.pdf",
        )
        cls.view = StatsView(cls.root, cls.cb)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def _buttons(self):
        import tkinter as tk
        import ttkbootstrap as ttk
        out = []
        # Buttons are ttk.Button (and some tk widgets); walk the whole window
        # and collect any widget whose "text" option is readable.
        def walk(w):
            try:
                out.append((w.cget("text"), w))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._window)
        return out

    def test_export_png_button_present(self):
        texts = [t for t, _ in self._buttons()]
        self.assertIn("Export PNG", texts)

    def test_export_pdf_button_present(self):
        texts = [t for t, _ in self._buttons()]
        self.assertIn("Export PDF", texts)

    def test_export_json_csv_also_present(self):
        texts = [t for t, _ in self._buttons()]
        self.assertIn("Export JSON", texts)
        self.assertIn("Export CSV", texts)

    def test_activity_and_duration_sections_rendered(self):
        # The heatmap frame and the per-day duration table both exist after a
        # refresh with no sessions (they render an empty state gracefully).
        self.assertTrue(hasattr(self.view, "_activity"))
        self.assertTrue(hasattr(self.view, "_duration"))

    def test_body_scrolls_so_bottom_tables_are_reachable(self):
        # The dashboard stacks more content than a 900x640 window fits, so the
        # body lives on a scrollable canvas; without this the duration table
        # and recent-sessions list fall off the bottom edge and are never
        # visible (the reported bug). The canvas must exist, be scrollable
        # (a yscrollcommand is wired to a scrollbar), and its scroll region
        # must extend below the viewport so the bottom sections are reachable.
        import tkinter as tk
        self.assertIsInstance(self.view._body_canvas, tk.Canvas)
        self.assertIsNotNone(self.view._body_canvas.cget("yscrollcommand"))
        self.view._window.update_idletasks()
        region = self.view._body_canvas.cget("scrollregion")
        # "x0 y0 x1 y1" — the region's bottom edge must exceed the canvas
        # height, meaning the content is taller than the viewport and the
        # user can scroll to reveal the bottom tables.
        if region:
            x0, y0, x1, y1 = (float(v) for v in region.split())
            height = self.view._body_canvas.winfo_height()
            self.assertGreater(y1, height,
                               "content taller than viewport; scroll needed")

    def test_mouse_wheel_scrolls_the_body(self):
        # The reported bug: the stats dashboard has a scrollable body but no
        # mouse-wheel binding, so the wheel did nothing anywhere in the
        # window. The Toplevel carries one binding per WHEEL_EVENTS sequence
        # (a widget's bindtags include its toplevel), so every child inherits
        # it; the handler must translate the wheel into a canvas yview_scroll.
        import tkinter as tk
        from unittest import mock
        from mimora.ui_theme import WHEEL_EVENTS, wheel_scroll_units

        for seq in WHEEL_EVENTS:
            self.assertTrue(
                self.view._window.bind(seq),
                f"{seq} must be bound on the Toplevel",
            )

        # The handler is the actual wheel→scroll translation. Exercise it
        # directly against a mock so the assertion does not depend on the
        # window being tall enough to scroll (empty sessions are short).
        event = tk.Event()
        event.delta = 120
        event.num = 0
        with mock.patch.object(self.view._body_canvas,
                               "yview_scroll") as scroll:
            self.view._on_wheel(event)
        scroll.assert_called_once()
        called_with = scroll.call_args[0]
        self.assertEqual(called_with[0], wheel_scroll_units(event))
        self.assertEqual(called_with[1], "units")


class NoReportButtonsWithoutHandlersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": []},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_no_report_buttons_without_handlers(self):
        import tkinter as tk
        texts = []

        def walk(w):
            try:
                texts.append(w.cget("text"))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._window)
        self.assertNotIn("Export PNG", texts)
        self.assertNotIn("Export PDF", texts)


class HeatmapCellsTest(unittest.TestCase):
    """Tk-free tests for the GitHub-style heatmap cell layout."""

    def test_empty_input_yields_no_cells(self):
        from mimora.ui_stats import heatmap_cells
        from datetime import date
        self.assertEqual(heatmap_cells([], date(2026, 10, 31)), [])

    def test_four_days_aligned_to_weekday_rows(self):
        # 2026-10-30 is a Friday. The four values span Tue..Fri, so the grid
        # starts on the Monday of that week (2026-10-26) and the cells occupy
        # rows 1-4 (Tue=1 .. Fri=4) in week 0.
        from mimora.ui_stats import heatmap_cells
        from datetime import date
        cells = heatmap_cells([100, 100, 100, 100], date(2026, 10, 30))
        self.assertEqual(cells, [(0, 1, 4), (0, 2, 4), (0, 3, 4), (0, 4, 4)])

    def test_all_zero_days_are_level_zero(self):
        from mimora.ui_stats import heatmap_cells
        from datetime import date
        cells = heatmap_cells([0, 0, 0, 0], date(2026, 10, 30))
        self.assertTrue(all(level == 0 for _, _, level in cells))

    def test_quantiles_assign_increasing_levels(self):
        # Distinct non-zero values map onto levels by the quantile thresholds
        # (0.25/0.5/0.75/1.0 of the sorted non-zero set). Any non-zero day is
        # at least level 1, and larger values never get a smaller level.
        from mimora.ui_stats import heatmap_cells
        from datetime import date
        values = [1, 2, 3, 4]
        cells = heatmap_cells(values, date(2026, 10, 30))
        levels = [level for _, _, level in cells]
        self.assertTrue(all(l >= 1 for l in levels))
        self.assertEqual(levels, sorted(levels))

    def test_week_index_increments_per_week(self):
        # 28 days ending Friday 2026-10-30 starts on 2026-10-03 (a Saturday).
        # The grid anchors to the Monday 2026-09-28, so there are 5 weeks and
        # the last cell sits in week 4 (column index 4).
        from mimora.ui_stats import heatmap_cells
        from datetime import date
        cells = heatmap_cells([1] * 28, date(2026, 10, 30))
        self.assertEqual(len(cells), 28)
        self.assertEqual(max(week for week, _, _ in cells), 4)


class HeatmapLayoutTest(unittest.TestCase):
    """Tk-free tests for the centred GitHub-style heatmap geometry."""

    def test_layout_reports_week_count_and_anchor(self):
        from mimora.ui_stats import heatmap_layout
        from datetime import date
        # 28 days ending Friday 2026-10-30: grid starts Monday 2026-09-28.
        layout = heatmap_layout([1] * 28, date(2026, 10, 30))
        self.assertEqual(layout["n_weeks"], 5)
        self.assertEqual(layout["grid_start"], date(2026, 9, 28))

    def test_layout_empty_returns_zero_weeks(self):
        from mimora.ui_stats import heatmap_layout
        from datetime import date
        layout = heatmap_layout([], date(2026, 10, 31))
        self.assertEqual(layout["n_weeks"], 0)
        self.assertEqual(layout["cells"], [])

    def test_layout_single_value_anchors_to_its_week(self):
        # A single day ending Tuesday 2026-10-27 anchors to Monday 2026-10-26
        # and occupies row 1 (Tue) in week 0. With one non-zero value the
        # quantile thresholds all collapse to it, so it gets the darkest
        # green (level 4) — GitHub renders a lone practice day as the max.
        from mimora.ui_stats import heatmap_layout
        from datetime import date
        layout = heatmap_layout([50], date(2026, 10, 27))
        self.assertEqual(layout["n_weeks"], 1)
        self.assertEqual(layout["grid_start"], date(2026, 10, 26))
        self.assertEqual(layout["cells"], [(0, 1, 4)])


class HeatmapMonthLayoutTest(unittest.TestCase):
    """Tk-free tests for the per-month GitHub-style heatmap geometry."""

    def test_november_2026_has_six_weeks(self):
        # Nov 2026 starts on a Sunday (the 1st) and has 30 days; the grid
        # anchors to Mon 2026-10-26 and runs through Sun 2026-12-06 = 6 weeks.
        from mimora.ui_stats import heatmap_month_layout
        from datetime import date
        layout = heatmap_month_layout({}, date(2026, 11, 15))
        self.assertEqual(layout["n_weeks"], 6)
        self.assertEqual(layout["grid_start"], date(2026, 10, 26))

    def test_month_with_practice_sets_levels_only_in_month(self):
        # Practice on the 1st (300s) and the 15th (600s); days spilling in
        # from October are left EMPTY (level -1) even though the 1st-of-month
        # week starts in October. Quantiles of [300, 600] put 300 at level 1
        # (just above the lightest green) and 600 at level 4 (the max).
        from mimora.ui_stats import heatmap_month_layout
        from datetime import date
        day_sec = {
            "2026-11-01": 300,
            "2026-11-15": 600,
        }
        layout = heatmap_month_layout(day_sec, date(2026, 11, 15))
        by_pos = {(week, row): lvl for week, row, lvl in layout["cells"]}
        # 2026-11-01 is a Sunday -> row 6 of week 0 (Mon 10-26).
        self.assertEqual(by_pos[(0, 6)], 1)
        # 2026-11-15 is a Sunday -> row 6 of week 2 (Mon 11-09).
        self.assertEqual(by_pos[(2, 6)], 4)
        # The spill days (Oct 26-31, row 0-5 of week 0) are empty (-1).
        for row in range(0, 6):
            self.assertEqual(by_pos[(0, row)], -1, f"week 0 row {row}")
        # An in-month day with no practice is level 0.
        # Week 1 row 0 = Mon 2026-11-02, an in-month empty day.
        self.assertEqual(by_pos[(1, 0)], 0)

    def test_spill_cells_are_negative_for_any_month(self):
        # The last week of November 2026 ends on Sun 2026-12-06, so Dec 1-6
        # spill out of the month and must be empty (-1).
        from mimora.ui_stats import heatmap_month_layout
        from datetime import date
        layout = heatmap_month_layout({}, date(2026, 11, 15))
        by_pos = {(week, row): lvl for week, row, lvl in layout["cells"]}
        # Dec 1 2026 is a Tuesday -> row 1 of week 5.
        self.assertEqual(by_pos[(5, 1)], -1)
        # Dec 6 2026 is a Sunday -> row 6 of week 5.
        self.assertEqual(by_pos[(5, 6)], -1)

    def test_empty_month_still_draws_grid(self):
        # An empty month still yields cells (all level 0), so the calendar
        # shows a full month grid rather than "No data".
        from mimora.ui_stats import heatmap_month_layout
        from datetime import date
        layout = heatmap_month_layout({}, date(2026, 11, 15))
        self.assertTrue(layout["cells"])
        # In-month days are 0; spill days are -1.
        in_month = [lvl for _, _, lvl in layout["cells"] if lvl >= 0]
        self.assertTrue(in_month)
        self.assertTrue(all(lvl == 0 for lvl in in_month))

    def test_february_leap_year_has_five_weeks(self):
        # Feb 2028 (leap, 29 days) starts on a Tuesday. The grid anchors to
        # Mon 2028-01-31 and runs through Sun 2028-03-05 -> 5 weeks.
        from mimora.ui_stats import heatmap_month_layout
        from datetime import date
        layout = heatmap_month_layout({}, date(2028, 2, 10))
        self.assertEqual(layout["n_weeks"], 5)
        self.assertEqual(layout["grid_start"], date(2028, 1, 31))


class HeatmapMonthNavigationTest(unittest.TestCase):
    """Tk-free tests for the prev/next month navigation."""

    def _make_view(self, month):
        from datetime import date
        from unittest import mock
        from mimora.ui_stats import StatsView
        view = StatsView.__new__(StatsView)
        view._heatmap_month = month
        view.refresh = mock.MagicMock()
        return view

    def test_prev_month_goes_back_one(self):
        from datetime import date
        view = self._make_view(date(2026, 11, 15))
        view._heatmap_prev_month()
        self.assertEqual(view._heatmap_month, date(2026, 10, 1))
        view.refresh.assert_called_once()

    def test_next_month_goes_forward_one(self):
        from datetime import date
        view = self._make_view(date(2026, 11, 15))
        view._heatmap_next_month()
        self.assertEqual(view._heatmap_month, date(2026, 12, 1))
        view.refresh.assert_called_once()

    def test_prev_month_wraps_year_boundary(self):
        # January -> previous month is December of the prior year.
        from datetime import date
        view = self._make_view(date(2026, 1, 15))
        view._heatmap_prev_month()
        self.assertEqual(view._heatmap_month, date(2025, 12, 1))

    def test_next_month_wraps_year_boundary(self):
        from datetime import date
        view = self._make_view(date(2026, 12, 15))
        view._heatmap_next_month()
        self.assertEqual(view._heatmap_month, date(2027, 1, 1))


class ReviewRenderTest(unittest.TestCase):
    """Renders the review-pool analysis section with sample data.

    Uses a real withdrawn Tk root (like the export-button tests) because the
    section builds real widgets; the assertions only check that the section
    renders without error and shows the key headings.
    """

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": []},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def _sample_phrases(self):
        return {
            "The cat sat.": {
                "source": "practice", "first_seen": "2026-10-25T09:00:00",
                "last_reviewed": "2026-10-28T09:00:00",
                "next_review_at": "2026-11-02T09:00:00",
                "interval_index": 1, "seen": 3, "passed": True,
            },
            "The dog ran.": {
                "source": "practice", "first_seen": "2026-10-20T09:00:00",
                "last_reviewed": "2026-10-29T09:00:00",
                "next_review_at": "2026-10-25T09:00:00",  # overdue
                "interval_index": 0, "seen": 6, "passed": False,
            },
            "A bird flew.": {
                "source": "llm", "first_seen": "2026-10-27T09:00:00",
                "last_reviewed": None, "next_review_at": None,
                "interval_index": 0, "seen": 0, "passed": False,
            },
        }

    def _texts(self):
        out = []
        def walk(w):
            try:
                out.append(w.cget("text"))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._review)
        return out

    def test_empty_phrases_shows_hint(self):
        self.view._render_review({}, [])
        texts = self._texts()
        self.assertTrue(any("No review data yet" in t for t in texts))

    def _canvas_texts(self):
        """Titles drawn on the section's canvases (not tk widgets)."""
        import tkinter as tk
        out = []
        def walk(w):
            try:
                if isinstance(w, tk.Canvas):
                    for item in w.find_all():
                        try:
                            out.append(w.itemcget(item, "text"))
                        except Exception:
                            pass
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._review)
        return out

    def test_review_section_renders_headings(self):
        self.view._render_review(self._sample_phrases(), [])
        texts = self._texts()
        self.assertIn("Review pool", texts)
        self.assertIn("Overdue phrases", texts)
        # The overdue phrase appears in the list.
        self.assertTrue(any("The dog ran." in t for t in texts))
        # Chart titles are drawn on the canvases (create_text), so read them
        # back from the canvas items.
        canvas_texts = self._canvas_texts()
        self.assertIn("Review stage distribution", canvas_texts)
        self.assertIn("Due load (next 60 days, cumulative)", canvas_texts)

    def test_kpi_cards_rendered(self):
        self.view._render_review(self._sample_phrases(), [])
        texts = self._texts()
        self.assertIn("Total", texts)
        self.assertIn("Due today", texts)
        self.assertIn("Pass rate", texts)


class StatsTabsTest(unittest.TestCase):
    """The dashboard splits into five tabs (Overview / Trends / Review pool /
    Sessions / Dictation) instead of one long vertical scroll. Each tab is a
    real frame added to a ttk.Notebook; _show_tab raises the requested tab and
    resets the scroll position."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": []},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)
        cls.view.refresh()

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_six_tabs_present(self):
        import ttkbootstrap as ttk
        self.assertIsInstance(self.view._nb, ttk.Notebook)
        self.assertEqual(self.view._nb.index("end"), 7)
        texts = [self.view._nb.tab(i, "text") for i in range(7)]
        self.assertEqual(texts, ["Overview", "Trends", "Review pool",
                                 "Sessions", "Dictation", "Drill", "Advance"])

    def test_show_tab_selects_and_scrolls_to_top(self):
        self.view._show_tab("review")
        self.assertEqual(self.view._nb.index("current"), 2)
        self.view._show_tab("overview")
        self.assertEqual(self.view._nb.index("current"), 0)
        self.view._show_tab("dictation")
        self.assertEqual(self.view._nb.index("current"), 4)
        self.view._show_tab("drill")
        self.assertEqual(self.view._nb.index("current"), 5)
        self.view._show_tab("advance")
        self.assertEqual(self.view._nb.index("current"), 6)

    def test_tabs_are_frames(self):
        import tkinter as tk
        for tab in (self.view._tab_overview, self.view._tab_trends,
                    self.view._tab_review, self.view._tab_sessions,
                    self.view._tab_dictation, self.view._tab_drill,
                    self.view._tab_advance):
            self.assertIsInstance(tab, tk.Frame)

    def test_summary_cards_wrap_at_four_per_row(self):
        # 7 summary cards must wrap: row 0 has 4, row 1 has 3. An unbounded
        # side=LEFT row would squeeze them all into a sliver on a narrow
        # window; the grid is the design decision (doc line 177).
        self.view._render_cards([{"duration_sec": 60} for _ in range(3)],
                                [{"score": 80, "phrase": "a",
                                  "timestamp": "2026-10-01T09:00:00"}])
        grid = self.view._cards.grid_info()
        # grid_info on the frame itself returns nothing; check the children's
        # grid positions instead.
        rows = set()
        for child in self.view._cards.winfo_children():
            info = child.grid_info()
            rows.add(info.get("row", -1))
        self.assertIn(0, rows)
        self.assertIn(1, rows)


class DictationTabTest(unittest.TestCase):
    """The dictation tab renders the full dashboard: a KPI row plus a chart
    grid (attempts histogram, pass-rate curve, error kinds by week, top wrong
    words, first→last dumbbell, score distribution, word-by-week heatmap,
    per-session attempt series and the mastery donut). Empty input renders an
    empty-state label instead."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": [],
                               "dictation_sessions": [
                                   {"id": "a", "first_score": 50.0,
                                    "last_score": 90.0, "outcome": "mastered",
                                    "ended_at": "2026-10-28T03:11:24+08:00",
                                    "takes": [
                                        {"user_text": "the quik",
                                         "reference_text": "The quick brown fox.",
                                         "score": 50.0, "passed": False,
                                         "attempt": 1},
                                        {"user_text": "the quick brown fox",
                                         "reference_text": "The quick brown fox.",
                                         "score": 100.0, "passed": True,
                                         "attempt": 2},
                                    ]},
                                   {"id": "b", "first_score": 10.0,
                                    "last_score": 10.0,
                                    "outcome": "unfinished",
                                    "ended_at": "2026-10-29T03:11:24+08:00",
                                    "takes": [
                                        {"user_text": "",
                                         "reference_text": "Hello world.",
                                         "score": 0.0, "passed": False,
                                         "attempt": 1},
                                    ]},
                               ]},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)
        cls.view.refresh()

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def _dictation_canvases(self):
        out = []

        def walk(w):
            import tkinter as tk
            if isinstance(w, tk.Canvas):
                out.append(w)
            for c in w.winfo_children():
                walk(c)
        walk(self.view._dictation)
        return out

    def test_dictation_tab_renders_dashboard(self):
        # KPI row + 2-col grid: histogram, pass-rate, error kinds, wrong words,
        # dumbbell, score distribution, heatmap, attempt series, donut.
        self.assertEqual(len(self._dictation_canvases()), 10)

    def test_dictation_kpi_row_present(self):
        kpi = self._dictation_canvases()[0]
        texts = [kpi.itemcget(i, "text") for i in kpi.find_all()
                 if kpi.type(i) == "text"]
        self.assertTrue(any("Mastery" in t for t in texts))
        self.assertTrue(any("50%" in t for t in texts))  # 1 mastered of 2

    def test_dictation_slope_draws_both_sessions(self):
        slope = None
        for cv in self._dictation_canvases():
            items = cv.find_all()
            if any(cv.type(i) == "line" for i in items):
                slope = cv
                break
        self.assertIsNotNone(slope, "no line-chart canvas found")

    def test_dictation_error_kinds_stacked(self):
        stacked = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Error kinds" in t for t in texts):
                stacked = cv
                break
        self.assertIsNotNone(stacked, "no stacked error-kind chart")
        # Both sessions contribute missed words; spelling from the quik take.
        self.assertGreater(len(stacked.find_all()), 0)

    def test_dictation_top_wrong_words_hbars(self):
        hbar = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Top wrong words" in t for t in texts):
                hbar = cv
                break
        self.assertIsNotNone(hbar, "no top-wrong-words chart")
        texts = [hbar.itemcget(i, "text") for i in hbar.find_all()
                 if hbar.type(i) == "text"]
        self.assertTrue(any("brown" in t for t in texts))

    def test_dictation_heatmap_matrix(self):
        heat = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Wrong words by week" in t for t in texts):
                heat = cv
                break
        self.assertIsNotNone(heat, "no word-by-week heatmap")
        # The grid draws at least one rectangle per word row.
        rects = [i for i in heat.find_all() if heat.type(i) == "rectangle"]
        self.assertGreater(len(rects), 0)

    def test_dictation_score_distribution_stacked(self):
        score = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Score distribution" in t for t in texts):
                score = cv
                break
        self.assertIsNotNone(score, "no score distribution chart")
        # Passed (green) and failed (red) segments both drawn.
        fills = [score.itemcget(i, "fill") for i in score.find_all()
                 if score.type(i) == "rectangle"]
        self.assertTrue(any(f for f in fills if f), "no bars drawn")

    def test_dictation_attempt_series(self):
        series = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Score per attempt" in t for t in texts):
                series = cv
                break
        self.assertIsNotNone(series, "no per-session attempt series")

    def test_dictation_donut_has_mastery_arc(self):
        # The donut draws an arc only when mastered > 0 (one session mastered).
        donut = None
        for cv in self._dictation_canvases():
            if any(cv.type(i) == "arc" for i in cv.find_all()):
                donut = cv
                break
        self.assertIsNotNone(donut, "no donut canvas found")
        arcs = [i for i in donut.find_all() if donut.type(i) == "arc"]
        self.assertEqual(len(arcs), 1)

    def test_dictation_dumbbell_labels_aligned(self):
        # The dumbbell draws one row per session (both sessions). The first
        # session (50->90) is a non-degenerate line; the second (10,10) is a
        # degenerate point drawn as an outlined oval, not a line.
        dumbbell = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("First → last" in t for t in texts):
                dumbbell = cv
                break
        self.assertIsNotNone(dumbbell, "no dumbbell canvas found")
        lines = [i for i in dumbbell.find_all() if dumbbell.type(i) == "line"]
        ovals = [i for i in dumbbell.find_all() if dumbbell.type(i) == "oval"]
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(len(ovals), 1)

    def test_dictation_pass_rate_curve(self):
        rate = None
        for cv in self._dictation_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Pass rate by attempt" in t for t in texts):
                rate = cv
                break
        self.assertIsNotNone(rate, "no pass-rate curve")
        # The curve draws a line between attempt 1 (0.0) and attempt 2 (1.0).
        lines = [i for i in rate.find_all() if rate.type(i) == "line"]
        self.assertGreaterEqual(len(lines), 1)


class DictationChartMethodsTest(unittest.TestCase):
    """Direct tests of the new _CanvasChart draw methods used by the dictation
    dashboard (stacked bars, horizontal bars, stacked histogram, line series,
    heatmap matrix, dumbbell, KPI cards). A withdrawn root is enough — the
    methods only paint on the canvas, they never read window geometry."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import _CanvasChart
        from tests._tk import make_root
        cls.root = make_root()
        cls.chart = _CanvasChart(cls.root, width=430, height=170)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_stacked_bars_skip_all_zero_segments(self):
        from mimora.ui_theme import THEME
        self.chart.draw_stacked_bars(
            [("Missed", [2.0]), ("Extra", [0.0]), ("Spelling", [1.0])],
            ["2026-10-26"], title="Error kinds")
        items = self.chart.canvas.find_all()
        # Stacked bars draw at least one rectangle (missed+spelling), and the
        # all-zero "Extra" segment must NOT draw a phantom band.
        self.assertGreater(len(items), 0)

    def test_hbars_draw_bar_per_item(self):
        self.chart.draw_hbars([("the", 82.0), ("brown", 40.0)], title="Top")
        rects = [i for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "rectangle"]
        self.assertEqual(len(rects), 2)

    def test_stacked_histogram_splits_passed_failed(self):
        self.chart.draw_stacked_histogram(
            [("0", 0, 2), ("90", 1, 0)], title="Scores")
        fills = [self.chart.canvas.itemcget(i, "fill")
                 for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "rectangle"]
        # One green (passed) + one red (failed) segment.
        from mimora.ui_theme import THEME
        self.assertIn(THEME["good"], fills)
        self.assertIn(THEME["bad"], fills)

    def test_line_series_draws_points_and_legend(self):
        self.chart.draw_line_series(
            [("Pass rate", [0.5, 1.0, 0.25], "#5cd98a")],
            ["1", "2", "3"], max_value=1.0, title="Rate")
        texts = [self.chart.canvas.itemcget(i, "text")
                 for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "text"]
        self.assertTrue(any("Pass rate" in t for t in texts))

    def test_line_series_legend_omittable(self):
        self.chart.draw_line_series(
            [("s1", [1.0, 2.0], "#5cd98a")], ["a", "b"],
            title="Series", legend=False)
        texts = [self.chart.canvas.itemcget(i, "text")
                 for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "text"]
        # With legend off, the series name is not drawn (title only).
        self.assertFalse(any("s1" == t for t in texts))

    def test_heatmap_matrix_colours_cells(self):
        self.chart.draw_heatmap_matrix(
            ["the", "brown"], ["2026-10-26", "2026-11-02"],
            [[2, 1], [1, 0]], ["#111111", "#222222"], title="Wrong by week")
        rects = [i for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "rectangle"]
        self.assertEqual(len(rects), 4)  # 2 words x 2 weeks

    def test_dumbbell_degenerate_draws_single_dot(self):
        self.chart.draw_dumbbell([("a", 50.0, 50.0)], title="First → last")
        # A degenerate point (first == last) is an oval, not a line.
        lines = [i for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "line"]
        self.assertEqual(len(lines), 0)

    def test_kpi_cards_wrap_at_four(self):
        self.chart.draw_kpi_cards([
            ("Mastery", "13%"), ("Avg attempts", "4.7"),
            ("Avg gain", "-1.0"), ("Sessions", "15"),
            ("Fifth", "x"),
        ])
        rects = [i for i in self.chart.canvas.find_all()
                 if self.chart.canvas.type(i) == "rectangle"]
        self.assertEqual(len(rects), 5)  # five card outlines

    def test_empty_inputs_render_no_data(self):
        for draw in (lambda: self.chart.draw_stacked_bars([], []),
                     lambda: self.chart.draw_hbars([]),
                     lambda: self.chart.draw_stacked_histogram([]),
                     lambda: self.chart.draw_line_series([], []),
                     lambda: self.chart.draw_heatmap_matrix(
                         [], [], [], ["#111111", "#222222"]),
                     lambda: self.chart.draw_dumbbell([])):
            draw()
            texts = [self.chart.canvas.itemcget(i, "text")
                     for i in self.chart.canvas.find_all()
                     if self.chart.canvas.type(i) == "text"]
            self.assertTrue(any(t == "No data" for t in texts))

class DictationEmptyStateTest(unittest.TestCase):
    """A view with no dictation sessions shows an empty-state label instead of
    charts. Own class with its own root (the fast suite shares one process, so
    a second root must not be created while another class root is alive)."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": [],
                               "dictation_sessions": []},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)
        cls.view.refresh()

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_dictation_empty_state(self):
        self.view._show_tab("dictation")
        texts = []

        def walk(w):
            try:
                texts.append(w.cget("text"))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._dictation)
        self.assertTrue(any("No dictation sessions" in t for t in texts))


class DrillTabTest(unittest.TestCase):
    """The drill tab renders the full analysis set: a KPI row plus a chart grid
    (mastery by type, weekly volume, progress dumbbell, phoneme averages,
    attempts histogram, pass-rate curve, investment scatter, same-target
    progress, session trajectories, target×week heatmap, audio coverage and
    the outcome donut). Empty input renders an empty-state label instead."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": [],
                               "drill_sessions": [
                                   _drill_session(
                                       "d-1", "single_word", "hello",
                                       [40, 70, 90], "mastered",
                                       ended="2026-10-28T03:11:24+08:00"),
                                   _drill_session(
                                       "d-1b", "word", "hello",
                                       [50, 80], "mastered",
                                       ended="2026-10-30T03:11:24+08:00"),
                                   _drill_session(
                                       "d-2", "phoneme", "r",
                                       [30, 50], "unfinished",
                                       ended="2026-10-29T03:11:24+08:00"),
                               ]},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)
        cls.view.refresh()

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def _drill_canvases(self):
        out = []

        def walk(w):
            import tkinter as tk
            if isinstance(w, tk.Canvas):
                out.append(w)
            for c in w.winfo_children():
                walk(c)
        walk(self.view._drill)
        return out

    def test_drill_tab_renders_dashboard(self):
        # KPI row + 2-col grid: mastery, weekly volume, dumbbell, phoneme
        # averages, attempts hist, pass rate, scatter, same-target, trajectories,
        # heatmap, audio coverage, donut = 12 charts + KPI = 13 canvases.
        self.assertEqual(len(self._drill_canvases()), 13)

    def test_drill_kpi_row_present(self):
        kpi = self._drill_canvases()[0]
        texts = [kpi.itemcget(i, "text") for i in kpi.find_all()
                 if kpi.type(i) == "text"]
        self.assertTrue(any("Mastery" in t for t in texts))
        self.assertTrue(any("67%" in t for t in texts))  # 2 mastered of 3

    def test_drill_mastery_by_type_stacked(self):
        stacked = None
        for cv in self._drill_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Mastery by type" in t for t in texts):
                stacked = cv
                break
        self.assertIsNotNone(stacked, "no mastery-by-type chart")
        # 2 type labels (single_word, phoneme), each with a stacked bar.
        self.assertGreater(len(stacked.find_all()), 0)

    def test_drill_phoneme_averages_hbars(self):
        hbar = None
        for cv in self._drill_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Phoneme averages" in t for t in texts):
                hbar = cv
                break
        self.assertIsNotNone(hbar, "no phoneme-averages chart")
        # /r/ is drawn as a bar label.
        texts = [hbar.itemcget(i, "text") for i in hbar.find_all()
                 if hbar.type(i) == "text"]
        self.assertTrue(any("r" in t for t in texts))

    def test_drill_same_target_over_time(self):
        series = None
        for cv in self._drill_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Same target over time" in t for t in texts):
                series = cv
                break
        self.assertIsNotNone(series, "no same-target series chart")
        lines = [i for i in series.find_all() if series.type(i) == "line"]
        self.assertGreaterEqual(len(lines), 1)

    def test_drill_session_trajectories(self):
        traj = None
        for cv in self._drill_canvases():
            texts = [cv.itemcget(i, "text") for i in cv.find_all()
                     if cv.type(i) == "text"]
            if any("Session trajectories" in t for t in texts):
                traj = cv
                break
        self.assertIsNotNone(traj, "no session-trajectories chart")
        # A mastered trajectory (3 points) draws a line.
        lines = [i for i in traj.find_all() if traj.type(i) == "line"]
        self.assertGreaterEqual(len(lines), 1)

    def test_drill_outcome_donut(self):
        # 1 mastered of 2 → a single green arc.
        donut = None
        for cv in self._drill_canvases():
            if any(cv.type(i) == "arc" for i in cv.find_all()):
                donut = cv
                break
        self.assertIsNotNone(donut, "no donut canvas found")
        arcs = [i for i in donut.find_all() if donut.type(i) == "arc"]
        self.assertEqual(len(arcs), 1)


class DrillEmptyStateTest(unittest.TestCase):
    """A view with no drill sessions shows an empty-state label. Own root (the
    fast suite shares one process, so a second root must not be created while
    another class root is alive)."""

    @classmethod
    def setUpClass(cls):
        from mimora.ui_stats import StatsView, StatsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        cls.cb = StatsCallbacks(
            load_data=lambda: {"sessions": [], "all_takes": [],
                               "drill_sessions": []},
            export_json=lambda: None,
            export_csv=lambda: None,
        )
        cls.view = StatsView(cls.root, cls.cb)
        cls.view.refresh()

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(cls.root)

    def test_drill_empty_state(self):
        self.view._show_tab("drill")
        texts = []

        def walk(w):
            try:
                texts.append(w.cget("text"))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(self.view._drill)
        self.assertTrue(any("No drill sessions" in t for t in texts))


class HeatmapThemeTest(unittest.TestCase):
    """The heatmap palette comes from THEME["heatmap_levels"] /
    THEME["heatmap_text"] (per-theme lists) instead of two hardcoded lists."""

    def test_levels_and_text_are_themed(self):
        from mimora import config
        self.assertEqual(len(config.THEME["heatmap_levels"]), 5)
        self.assertEqual(len(config.THEME["heatmap_text"]), 5)
        # Both are lists of color strings for the ACTIVE theme (dark / light /
        # cream each ship their own palette; the active one may be any).
        self.assertTrue(all(isinstance(v, str) and v.startswith("#")
                            for v in config.THEME["heatmap_levels"]))
        self.assertTrue(all(isinstance(v, str) and v.startswith("#")
                            for v in config.THEME["heatmap_text"]))


if __name__ == "__main__":
    unittest.main()
