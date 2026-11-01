# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the shared time helpers in :mod:`mimora.time_utils`.

Covers the single ISO-date/parse implementation that the ``*_analysis`` and
stats modules now share: ``iso_date`` (accepts timestamps and bare dates,
normalises to a zero-padded date, returns None on unparseable), ``week_start``
(Monday of the week) and ``parse_ts`` (timezone-aware datetime, naive input
assumed local). Fast suite: no Tk, no models.
"""

import datetime
import unittest


class IsoDateTest(unittest.TestCase):
    def test_full_timestamp(self):
        from mimora.time_utils import iso_date
        self.assertEqual(iso_date("2026-11-01T21:00:00+08:00"), "2026-11-01")

    def test_bare_date(self):
        from mimora.time_utils import iso_date
        self.assertEqual(iso_date("2026-11-01"), "2026-11-01")

    def test_zero_padding(self):
        from mimora.time_utils import iso_date
        # A parsed timestamp normalises; a raw slice would give "2026-1-5".
        self.assertEqual(iso_date("2026-01-05T10:00:00"), "2026-01-05")

    def test_empty_and_bad(self):
        from mimora.time_utils import iso_date
        self.assertIsNone(iso_date(""))
        self.assertIsNone(iso_date(None))
        self.assertIsNone(iso_date("not-a-date"))

    def test_naive_timestamp(self):
        from mimora.time_utils import iso_date
        self.assertEqual(iso_date("2026-11-01T21:00:00"), "2026-11-01")


class WeekStartTest(unittest.TestCase):
    def test_monday_of_week(self):
        from mimora.time_utils import week_start
        # 2026-11-01 is a Sunday; the week's Monday is 2026-10-26.
        self.assertEqual(week_start("2026-11-01"), "2026-10-26")
        self.assertEqual(week_start("2026-10-26"), "2026-10-26")


class ParseTsTest(unittest.TestCase):
    def test_aware_roundtrip(self):
        from mimora.time_utils import parse_ts
        parsed = parse_ts("2026-11-01T21:00:00+08:00")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_naive_gets_local_tz(self):
        from mimora.time_utils import parse_ts
        parsed = parse_ts("2026-11-01T21:00:00")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)
        # Naive input assumed local, so it compares >= a local now minus a day.
        self.assertGreater(parsed, datetime.datetime.now().astimezone()
                           - datetime.timedelta(days=1))

    def test_empty_and_bad(self):
        from mimora.time_utils import parse_ts
        self.assertIsNone(parse_ts(""))
        self.assertIsNone(parse_ts(None))
        self.assertIsNone(parse_ts("nope"))


if __name__ == "__main__":
    unittest.main()
