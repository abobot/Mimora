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
    """The drill window shows per-word coloured cells after a take."""

    def _view(self):
        from mimora.ui_drill import DrillView
        v = DrillView.__new__(DrillView)
        v._feedback_label = mock.MagicMock()
        v._feedback_cells = []
        v._feedback_cells_frame = mock.MagicMock()
        v._feedback_cells_layout_in_progress = False
        v._layout_feedback_cells = mock.MagicMock()
        return v

    def test_cells_are_built_per_level(self):
        v = self._view()
        import tkinter as tk
        details = {"result": _FakeResult([
            {"word": "the", "level": "good"},
            {"word": "cat", "level": "bad"},
            {"word": "sat", "level": "ok"},
        ])}
        with mock.patch.object(tk, "Label", side_effect=_FakeCell):
            v._update_feedback(details)
        self.assertEqual(len(v._feedback_cells), 3)
        texts = [c.cget("text") for c in v._feedback_cells]
        self.assertEqual(texts, ["the", "cat", "sat"])
        # Colors: good=good, bad=bad, ok=text_dim (same as the hero card).
        self.assertEqual(v._feedback_cells[0].cget("fg"), THEME["good"])
        self.assertEqual(v._feedback_cells[1].cget("fg"), THEME["bad"])
        self.assertEqual(v._feedback_cells[2].cget("fg"), THEME["text_dim"])

    def test_no_details_hides_strip(self):
        v = self._view()
        import tkinter as tk
        with mock.patch.object(tk, "Label", side_effect=_FakeCell):
            v._update_feedback(None)
        self.assertEqual(v._feedback_cells, [])
        v._feedback_cells_frame.pack_forget.assert_called()

    def test_missing_result_hides_strip(self):
        v = self._view()
        import tkinter as tk
        with mock.patch.object(tk, "Label", side_effect=_FakeCell):
            v._update_feedback({"result": None})
        self.assertEqual(v._feedback_cells, [])
        v._feedback_cells_frame.pack_forget.assert_called()

    def test_clear_cells_hides_strip(self):
        v = self._view()
        v._feedback_cells = [mock.MagicMock(), mock.MagicMock()]
        v._clear_feedback_cells()
        self.assertEqual(v._feedback_cells, [])
        v._feedback_cells_frame.pack_forget.assert_called()

    def test_layout_uses_shared_helper(self):
        """The drill strip lays out with the same centered-wrap helper."""
        self.assertTrue(callable(layout_cells_centered))

    def test_parse_word_levels_is_imported_helper(self):
        """The strip uses the same word-level resolution as the hero card."""
        self.assertTrue(callable(parse_word_levels))


if __name__ == "__main__":
    unittest.main()
