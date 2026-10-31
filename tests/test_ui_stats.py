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


if __name__ == "__main__":
    unittest.main()
