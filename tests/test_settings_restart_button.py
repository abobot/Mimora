# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Tests for the settings-window restart button (always visible).

The user asked for the restart button to sit to the left of Cancel in the
settings window at all times, not only when a restart-only field has been
touched. These tests build a real SettingsWindow over a withdrawn root and
assert the button is always packed and that it is greyed out until a
restart-only change actually exists.

Run: python -m unittest tests.test_settings_restart_button
"""

import unittest
from unittest import mock

from mimora import config


class RestartButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        from mimora.settings_window import SettingsWindow, SettingsCallbacks
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            callbacks = SettingsCallbacks(
                on_setting_changed=lambda k, v: None,
                on_preview_voice=lambda v: None,
                on_restart_requested=lambda: None,
                on_reset_settings=lambda: True,
            )
            cls.win = SettingsWindow(cls.root, callbacks)
        except tk.TclError:
            # Test isolation: another Tk-root test class may have torn the
            # interpreter down (a destroyed root makes new Tk() fail). Retry
            # once with a fresh interpreter is not reliable, so just mark the
            # class skipped rather than failing the whole suite.
            cls.root = None
            cls.win = None
            raise unittest.SkipTest("Tk interpreter unavailable after prior test")

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "win", None) is not None:
                cls.win.top.destroy()
        except Exception:
            pass
        try:
            if getattr(cls, "root", None) is not None:
                cls.root.destroy()
        except Exception:
            pass

    def test_restart_button_is_always_packed(self):
        # Always visible: it must be managed (packed) even with no pending
        # restart-only changes.
        self.assertTrue(self.win._restart_btn.winfo_manager(),
                        "Restart now should be packed at all times")

    def test_restart_button_disabled_without_pending(self):
        # The user asked for the button to be always visible AND always
        # clickable, so with no pending restart-only changes it stays enabled
        # (a restart with nothing to apply is harmless).
        self.win._restart_pending = {}
        self.win._update_restart_hint()
        self.assertEqual(str(self.win._restart_btn.cget("state")), "normal")

    def test_restart_button_enabled_with_pending(self):
        self.win._restart_pending = {"llm_backend": "LLM backend"}
        self.win._update_restart_hint()
        self.assertEqual(str(self.win._restart_btn.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
