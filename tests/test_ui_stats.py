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


if __name__ == "__main__":
    unittest.main()
