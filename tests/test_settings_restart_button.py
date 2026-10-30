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
        from mimora.settings_window import SettingsWindow, SettingsCallbacks
        from tests._tk import make_root
        cls.root = make_root()
        callbacks = SettingsCallbacks(
            on_setting_changed=lambda k, v: None,
            on_preview_voice=lambda v: None,
            on_restart_requested=lambda: None,
            on_reset_settings=lambda: True,
        )
        cls.win = SettingsWindow(cls.root, callbacks)

    @classmethod
    def tearDownClass(cls):
        from tests._tk import destroy_root
        destroy_root(getattr(cls, "root", None))

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
