# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the P2#7 progress-report renderer (mimora/report.py).

Covers the pure aggregation helpers, the PNG/PDF byte output and the
rendered image layout. PIL is a runtime dependency, so no stubbing needed.
"""

import unittest


_SESSIONS = [
    {
        "id": "s-1",
        "started_at": "2026-10-25T10:00:00+08:00",
        "duration_sec": 300,
        "totals": {"takes": 3, "avg_best_score": 75.0},
    },
    {
        "id": "s-2",
        "started_at": "2026-10-26T10:00:00+08:00",
        "duration_sec": 600,
        "totals": {"takes": 5, "avg_best_score": 82.0},
    },
]

_TAKES = [
    {"timestamp": "2026-10-25T10:00:00+08:00", "score": 80.0},
    {"timestamp": "2026-10-25T11:00:00+08:00", "score": 40.0},
    {"timestamp": "2026-10-26T10:00:00+08:00", "score": 90.0},
    {"timestamp": "2026-10-26T11:00:00+08:00", "score": 55.0},
    {"timestamp": "bad-date", "score": 70.0},  # skipped by aggregation
]


class FmtSecondsTest(unittest.TestCase):
    def test_seconds(self):
        from mimora.report import _fmt_seconds
        self.assertEqual(_fmt_seconds(45), "45s")

    def test_minutes(self):
        from mimora.report import _fmt_seconds
        self.assertEqual(_fmt_seconds(120), "2m")

    def test_hours(self):
        from mimora.report import _fmt_seconds
        self.assertEqual(_fmt_seconds(3600), "1h 0m")

    def test_days(self):
        from mimora.report import _fmt_seconds
        self.assertEqual(_fmt_seconds(90000), "1d 1h")


class DailyAveragesTest(unittest.TestCase):
    def test_groups_and_averages(self):
        from mimora.report import _daily_averages
        result = _daily_averages(_TAKES)
        by_day = dict(result)
        self.assertIn("2026-10-25", by_day)
        self.assertIn("2026-10-26", by_day)
        # (80+40)/2 = 60; (90+55)/2 = 72.5
        self.assertAlmostEqual(by_day["2026-10-25"], 60.0, places=5)
        self.assertAlmostEqual(by_day["2026-10-26"], 72.5, places=5)

    def test_bad_dates_are_skipped(self):
        from mimora.report import _daily_averages
        result = _daily_averages([{"timestamp": "garbage", "score": 50}])
        self.assertEqual(result, [])


class ScoreBucketsTest(unittest.TestCase):
    def test_buckets(self):
        from mimora.report import _score_buckets
        buckets = _score_buckets([
            {"score": 10}, {"score": 30}, {"score": 60}, {"score": 90},
            {"score": 100}, {"score": 0},
        ])
        self.assertEqual(buckets, [2, 1, 1, 2])


class RenderReportTest(unittest.TestCase):
    def test_png_bytes(self):
        from mimora.report import render_report_png
        png = render_report_png(_SESSIONS, _TAKES)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 1000)

    def test_pdf_bytes(self):
        from mimora.report import render_report_pdf
        pdf = render_report_pdf(_SESSIONS, _TAKES)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_empty_data_renders(self):
        from mimora.report import render_report_png, render_report_pdf
        self.assertTrue(render_report_png([], []).startswith(b"\x89PNG"))
        self.assertTrue(render_report_pdf([], []).startswith(b"%PDF-"))

    def test_image_dimensions(self):
        from mimora.report import build_report_image
        img = build_report_image(_SESSIONS, _TAKES)
        self.assertEqual(img.size, (1000, 640))


if __name__ == "__main__":
    unittest.main()
