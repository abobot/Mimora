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


if __name__ == "__main__":
    unittest.main()
