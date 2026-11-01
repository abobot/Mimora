# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for the semantic design tokens in mimora/ui_tokens.py.

The token module is panel-agnostic and Tk-free, so all of this runs without a
root window. The palette is theme-dependent (whatever ``config.THEME`` resolved
at import), so the colour assertions compare against the *resolved* palette
rather than hardcoded hex values - the semantics are what the module owns, not
the specific hex. ``assert_contrast`` reads the resolved palette too, so the
contrast tests assert the mechanics (ratio math, failure-list shape, key
coverage) rather than a hardcoded pass/fail, because the light/cream themes are
allowed to trade contrast for warmth on purpose.
"""

import unittest

from mimora.ui_tokens import (
    contrast_ratio,
    score_color,
    level_color,
    status_color,
    assert_contrast,
)
from mimora.ui_theme import THEME


class ContrastRatioTests(unittest.TestCase):
    """WCAG 2.x relative-luminance and contrast-ratio math."""

    def test_black_on_white_is_max(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=2)

    def test_white_on_black_is_max(self):
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#000000"), 21.0, places=2)

    def test_same_colour_is_one(self):
        self.assertAlmostEqual(contrast_ratio("#888888", "#888888"), 1.0, places=2)

    def test_ratio_is_symmetric(self):
        self.assertAlmostEqual(contrast_ratio("#123456", "#abcdef"),
                               contrast_ratio("#abcdef", "#123456"), places=6)

    def test_rejects_non_hex(self):
        with self.assertRaises(ValueError):
            contrast_ratio("red", "#ffffff")


class ScoreColorTests(unittest.TestCase):
    """The single score->colour entry point (shared across all panels)."""

    def test_high_score_is_excellent(self):
        self.assertEqual(score_color(95), THEME["good"])

    def test_mid_high_is_ok(self):
        self.assertEqual(score_color(75), THEME["text_emph"])

    def test_mid_low_is_needs_work(self):
        self.assertEqual(score_color(60), THEME["text_dim"])

    def test_low_score_is_weak(self):
        self.assertEqual(score_color(30), THEME["bad"])

    def test_scale_other_than_100(self):
        # A 0-5 scale: bucket 4/5 == 0.8 >= 0.7 -> ok band.
        self.assertEqual(score_color(4, scale=5), THEME["text_emph"])

    def test_band_boundaries(self):
        # Exactly at the 85/70/50 breakpoints: >= means the upper band wins.
        self.assertEqual(score_color(85), THEME["good"])
        self.assertEqual(score_color(70), THEME["text_emph"])
        self.assertEqual(score_color(50), THEME["text_dim"])


class LevelColorTests(unittest.TestCase):
    """Per-word quality level -> traffic-light colour."""

    def test_known_levels(self):
        self.assertEqual(level_color("good"), THEME["good"])
        self.assertEqual(level_color("bad"), THEME["bad"])
        self.assertEqual(level_color("ok"), THEME["text_dim"])

    def test_unknown_level_falls_back_to_dim(self):
        self.assertEqual(level_color("nope"), THEME["text_dim"])


class StatusColorTests(unittest.TestCase):
    """Weakness-panel aggregation status -> colour."""

    def test_known_statuses(self):
        self.assertEqual(status_color("need_practice"), THEME["bad"])
        self.assertEqual(status_color("improving"), THEME["good"])

    def test_unknown_status_falls_back_to_dim(self):
        self.assertEqual(status_color("unknown"), THEME["text_dim"])


class AssertContrastTests(unittest.TestCase):
    """The palette audit returns a failure list instead of raising."""

    def test_returns_list(self):
        failures = assert_contrast()
        self.assertIsInstance(failures, list)
        for item in failures:
            self.assertIn("on", item)
            self.assertRegex(item, r": \d+\.\d{2}$")

    def test_min_ratio_changes_result(self):
        # A stricter threshold can only fail more, never less.
        loose = assert_contrast(min_ratio=3.0)
        strict = assert_contrast(min_ratio=7.0)
        self.assertLessEqual(len(loose), len(strict))

    def test_all_checked_keys_exist_in_theme(self):
        # Every colour named by the check must resolve (this is what breaks the
        # moment someone drops a key from a schema but not from the check list).
        for key in ("text_muted", "text_dim", "text_accent",
                    "bg_main", "bg_panel", "bg_card",
                    "bg_accent", "bg_accent_active", "bg_button"):
            self.assertIn(key, THEME)


class FontScaleEnforcementTests(unittest.TestCase):
    """The design doc's rule: ``font=(...)`` must reference FONT_SIZE_* constants.

    There is no linter in this project (AGENTS.md: "No linting/formatting
    config"), so the rule is enforced by a test instead. Every view module's
    ``font=(FONT_FAMILY, SIZE, ...)`` tuple must use a named constant as the
    size, never a bare number - a bare number is how a font quietly drifts out
    of the scale. The one documented exception is the splash (its own process,
    stdlib-only, cannot import ui_theme), which is excluded here.
    """

    _MODULES = (
        "ui.py", "ui_hero.py", "ui_practice.py", "ui_history.py",
        "ui_prosody.py", "ui_stats.py", "ui_chart.py", "ui_weakness.py",
        "ui_dictation.py", "ui_drill.py", "ui_history_browse.py",
        "ui_feedback_strip.py", "ui_long_press_popup.py",
    )

    def test_font_size_in_font_tuples_is_a_constant_not_a_literal(self):
        import ast
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        violations = []
        for name in self._MODULES:
            path = os.path.join(root, "mimora", name)
            if not os.path.exists(path):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # font=... as a keyword anywhere (widget constructors,
                # configure/tag_configure/itemconfigure) - "font" is a keyword
                # name only Tk accepts, so no false positives.
                for keyword in node.keywords:
                    if keyword.arg == "font" and keyword.value is not None:
                        self._check_font_tuple(keyword.value, name, violations)
                # A widget constructor with font=(FAMILY, size, ...) as the
                # *first positional* arg (tk.Label(text=..., font=...)).
                if isinstance(node.func, ast.Attribute) and \
                        node.func.attr in ("Label", "Button", "Text", "Entry",
                                           "Canvas", "Menubutton", "Radiobutton",
                                           "Checkbutton", "Message", "Spinbox"):
                    self._check_first_arg_font(node, name, violations)
        self.assertEqual(violations, [], f"font size not from FONT_SIZE_*: {violations}")

    @staticmethod
    def _check_first_arg_font(node, name, violations):
        import ast
        if not node.args:
            return
        first = node.args[0]
        if isinstance(first, ast.Tuple) and first.elts:
            FontScaleEnforcementTests._check_font_tuple(first, name, violations)

    @staticmethod
    def _check_font_tuple(node, name, violations):
        import ast
        # A font tuple is (family, size[, style...]); the size is elts[1].
        if not isinstance(node, ast.Tuple) or len(node.elts) < 2:
            return
        size = node.elts[1]
        # A bare number here is the violation; a Name (constant ref) is fine.
        if isinstance(size, ast.Constant) and isinstance(size.value, (int, float)):
            violations.append(f"{name}:{getattr(node, 'lineno', '?')} font=({node!r})")


if __name__ == "__main__":
    unittest.main()
