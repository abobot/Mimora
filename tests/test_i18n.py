# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Unit tests for the UI localization module (mimora/i18n.py).

These validate the tr() contract without any Tk widgets:

* English is the identity catalog - a key with no entry renders as itself.
* Chinese renders every catalog key, and a missing key degrades to English
  instead of raising or blanking the UI.
* str.format placeholders resolve against the translated template; a kwarg
  the template does not reference is ignored, and a malformed template or
  stray braces in user data never raise out of the translator.
* set_language validates its argument and falls back to English.

Run from the project root with:

    python -m unittest tests.test_i18n
"""

import unittest

from mimora import i18n


class EnglishIdentityTests(unittest.TestCase):
    """With the language at its default, tr() is the identity."""

    def setUp(self):
        i18n.set_language("en")

    def test_default_language_is_english(self):
        self.assertEqual(i18n.get_language(), "en")

    def test_known_key_renders_as_itself(self):
        self.assertEqual(i18n.tr("Ready"), "Ready")

    def test_unknown_key_renders_as_itself(self):
        # A key absent from the catalog degrades to the English source string.
        self.assertEqual(i18n.tr("Something never translated"),
                         "Something never translated")

    def test_placeholders_resolve(self):
        self.assertEqual(i18n.tr("New phrase: {phrase}", phrase="hello"),
                         "New phrase: hello")

    def test_extra_kwargs_are_ignored(self):
        self.assertEqual(i18n.tr("Ready", unused="ignored"), "Ready")


class ChineseCatalogTests(unittest.TestCase):
    """The zh catalog translates every documented key."""

    def setUp(self):
        i18n.set_language("zh")

    def test_ready_translates(self):
        self.assertEqual(i18n.tr("Ready"), "就绪")

    def test_missing_key_degrades_to_english(self):
        # No entry for this literal: the English string comes back untouched.
        self.assertEqual(i18n.tr("Totally missing string"),
                         "Totally missing string")

    def test_placeholder_reorders(self):
        self.assertEqual(i18n.tr("New phrase: {phrase}", phrase="hello"),
                         "新句子：hello")

    def test_plural_suffix_dropped(self):
        # The Chinese template has no {plural}; the English-only suffix kwarg
        # is ignored instead of leaking a literal placeholder.
        self.assertEqual(i18n.tr("{count} phrase{plural}", count=3, plural="s"),
                         "3 个句子")

    def test_format_value_style(self):
        self.assertEqual(i18n.tr("Playing reference ({speed}×)...", speed="0.5"),
                         "正在播放参考发音（0.5×）…")

    def test_stray_braces_in_user_data_never_raise(self):
        # User text can contain braces; tr() must not propagate a format error.
        self.assertEqual(i18n.tr("Error: {msg}", msg="{bad}"), "错误：{bad}")


class LanguageSelectionTests(unittest.TestCase):
    """set_language validates and never raises."""

    def test_unsupported_code_falls_back_to_english(self):
        i18n.set_language("bogus")
        self.assertEqual(i18n.get_language(), "en")
        self.assertEqual(i18n.tr("Ready"), "Ready")
        i18n.set_language("en")

    def test_roundtrip_en_zh(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.get_language(), "zh")
        i18n.set_language("en")
        self.assertEqual(i18n.get_language(), "en")

    def test_supported_languages_contains_default_first(self):
        self.assertEqual(i18n.SUPPORTED_LANGUAGES, ("en", "zh"))
        self.assertEqual(i18n.available_languages(), i18n.SUPPORTED_LANGUAGES)


class CatalogIntegrityTests(unittest.TestCase):
    """Structural checks on the zh catalog."""

    def test_every_tr_literal_in_ui_modules_is_catalogued(self):
        # Every str-literal passed to i18n.tr() in the UI modules must have a
        # zh entry, or the Chinese UI silently shows English for it. This is the
        # completeness guarantee - the one test that breaks when a developer
        # adds a new string to the UI and forgets to translate it.
        import ast
        import glob
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        catalog = i18n._CATALOGS["zh"]
        modules = (
            "app.py", "ui.py", "ui_hero.py", "ui_practice.py", "ui_history.py",
            "ui_prosody.py", "ui_stats.py", "ui_weakness.py", "ui_dictation.py",
            "ui_drill.py", "ui_history_browse.py", "settings_window.py",
            "settings_ctl.py", "progress_widget.py",
        )
        missing = []
        for name in modules:
            path = os.path.join(root, "mimora", name)
            if not os.path.exists(path):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "tr":
                    pass
                elif isinstance(func, ast.Name) and func.id == "tr":
                    pass
                else:
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literal = arg.value
                    if literal not in catalog:
                        missing.append((name, literal))
        self.assertEqual(missing, [],
                         f"tr() literals without a zh entry: {missing}")

    def test_translation_never_blank(self):
        for key, value in i18n._CATALOGS["zh"].items():
            self.assertTrue(value.strip(), f"blank translation for {key!r}")

    def test_zh_placeholders_are_subset_of_en_placeholders(self):
        # A Chinese translation may drop a placeholder (e.g. the English plural
        # suffix) but must never invent one the English source does not have.
        import re

        def placeholders(template):
            return set(re.findall(r"\{([^}:]+)", template))

        for key, value in i18n._CATALOGS["zh"].items():
            zh_ph = placeholders(value)
            en_ph = placeholders(key)
            self.assertTrue(zh_ph.issubset(en_ph),
                            f"{key!r}: zh uses {zh_ph - en_ph} not in en")


if __name__ == "__main__":
    unittest.main()
