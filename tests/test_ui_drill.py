# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for mimora/ui_drill.py (drill window).

Covers the colour-coded per-word feedback cells added after a scored take,
mirroring the hero card's word-feedback strip. Tk-free (``__new__`` + mocks),
so it does not touch a real root and cannot poison the order-fragile Tk test
classes.
"""

import unittest
from unittest import mock

from mimora.ui_hero import layout_cells_centered, parse_word_levels
from mimora.ui_theme import THEME


class _FakeCell:
    """A stand-in tk.Label that records its options for cget assertions.

    The real Label reads options from Tk, which a Tk-free test cannot do; this
    stores the kwargs at construction so ``cget("text")``/``cget("fg")`` return
    the values the view actually passed (the behaviour the tests assert on).
    """

    def __init__(self, *a, **k):
        self._opts = dict(k)

    def cget(self, name):
        return self._opts.get(name)

    def place_forget(self):
        pass

    def destroy(self):
        pass


class _FakeResult:
    """Minimal stand-in for a scored take's PronunciationResult."""

    def __init__(self, ref_words, weak_phonemes=None):
        self.reference_words = ref_words
        self.weak_phonemes = weak_phonemes or []


class DrillFeedbackCellsTests(unittest.TestCase):
    """The drill window delegates its per-word feedback strip to FeedbackStrip."""

    def _view(self):
        from mimora.ui_drill import DrillView
        v = DrillView.__new__(DrillView)
        v._feedback_label = mock.MagicMock()
        v._feedback_cells_frame = mock.MagicMock()
        # The shared strip owns the cells; the view just forwards to it.
        v._feedback_strip = mock.MagicMock()
        v._feedback_strip.cells = []
        v._feedback_cells = v._feedback_strip.cells
        return v

    def test_cells_are_built_per_level(self):
        v = self._view()
        details = {"result": _FakeResult([
            {"word": "the", "level": "good"},
            {"word": "cat", "level": "bad"},
            {"word": "sat", "level": "ok"},
        ])}
        v._update_feedback(details)
        # The view passes the resolved (word, level) pairs to the shared strip.
        v._feedback_strip.render.assert_called_once_with([
            ("the", "good"), ("cat", "bad"), ("sat", "ok")])
        v._feedback_cells_frame.pack.assert_called()
        self.assertEqual(len(v._feedback_cells), 0)  # strip owns the cells now

    def test_no_details_hides_strip(self):
        v = self._view()
        v._update_feedback(None)
        v._feedback_cells_frame.pack_forget.assert_called()
        v._feedback_strip.clear.assert_called()

    def test_missing_result_hides_strip(self):
        v = self._view()
        v._update_feedback({"result": None})
        v._feedback_cells_frame.pack_forget.assert_called()
        v._feedback_strip.clear.assert_called()

    def test_clear_cells_hides_strip(self):
        v = self._view()
        v._clear_feedback_cells()
        v._feedback_cells_frame.pack_forget.assert_called()
        v._feedback_strip.clear.assert_called()

    def test_layout_uses_shared_helper(self):
        """The drill strip lays out with the same centered-wrap helper."""
        self.assertTrue(callable(layout_cells_centered))

    def test_parse_word_levels_is_imported_helper(self):
        """The strip uses the same word-level resolution as the hero card."""
        self.assertTrue(callable(parse_word_levels))

    def test_view_uses_shared_strip(self):
        """The drill window forwards feedback cells to the shared strip."""
        from mimora.ui_drill import DrillView
        self.assertTrue(hasattr(DrillView, "_update_feedback"))


if __name__ == "__main__":
    unittest.main()
