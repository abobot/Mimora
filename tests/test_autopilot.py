"""Tests for the auto-pilot loop decision (mimora/autopilot.py).

The auto-pilot loop is a pure gate over the existing practice flow: when the
master toggle is on and a scored take clears the score threshold, the loop
advances to the next phrase and records it automatically; a take that does not
clear the threshold (or that the pass gate refuses) does NOT advance - the
loop stays on the same phrase and re-records it after the record delay, until
it clears. The decision lives in the pure module ``mimora/autopilot.py`` so it
can be tested without the GUI stack; ``app.py`` delegates to it.

Also covers the settings keys the loop reads (must be known and defaulted), so
a future rename cannot silently drop the config the loop depends on.
"""

import unittest

from mimora import autopilot, config


class AutopilotShouldAdvanceTest(unittest.TestCase):
    """The pure gate: does the loop advance past this take?"""

    def _advance(self, raw_score, threshold=80.0, gate_enabled=False,
                 passed=True, enabled=True):
        return autopilot.should_advance(
            enabled, raw_score, threshold, gate_enabled, passed)

    def test_disabled_never_advances(self):
        self.assertFalse(self._advance(95.0, enabled=False))

    def test_above_threshold_advances(self):
        self.assertTrue(self._advance(95.0))

    def test_exactly_threshold_does_not_advance(self):
        # The user's spec is "score > x" (strictly greater), so an exact hit
        # does not advance - the take must be strictly better, and the loop
        # re-records the same phrase instead.
        self.assertFalse(self._advance(80.0))

    def test_below_threshold_does_not_advance(self):
        self.assertFalse(self._advance(79.0))

    def test_zero_score_never_advances(self):
        # An empty/failed take is discarded upstream; even with the toggle on
        # it must not advance (0 is not a valid practice score).
        self.assertFalse(self._advance(0.0))

    def test_gate_refused_does_not_advance(self):
        self.assertFalse(self._advance(95.0, gate_enabled=True, passed=False))

    def test_gate_passed_advances(self):
        self.assertTrue(self._advance(95.0, gate_enabled=True, passed=True))

    def test_gate_off_advances_regardless_of_passed(self):
        self.assertTrue(self._advance(95.0, gate_enabled=False, passed=False))


class AutopilotSettingsKeysTest(unittest.TestCase):
    """The settings keys the loop reads are known and defaulted.

    The key-set tests (test_settings_fields, test_language_profiles) pin
    _KNOWN_USER_KEYS == USER_SETTING_DEFAULTS; these three keys must be in both
    or the loop reads a default that can never be persisted or edited.
    """

    def test_keys_are_known_and_defaulted(self):
        for key in ("auto_pilot_enabled", "auto_pilot_score_threshold",
                    "auto_pilot_record_delay"):
            self.assertIn(key, config._KNOWN_USER_KEYS, key)
            self.assertIn(key, config.USER_SETTING_DEFAULTS, key)

    def test_enabled_defaults_off(self):
        # A new loop must never start on its own on an existing install.
        self.assertIs(config.AUTO_PILOT_ENABLED, False)

    def test_threshold_default_within_range(self):
        t = config.AUTO_PILOT_SCORE_THRESHOLD
        self.assertGreaterEqual(t, 0)
        self.assertLessEqual(t, 100)

    def test_record_delay_default_nonnegative(self):
        self.assertGreaterEqual(config.AUTO_PILOT_RECORD_DELAY, 0)


class AutopilotViewCallbacksTest(unittest.TestCase):
    """The view contract the loop depends on is present on ViewCallbacks.

    The controller wiring for the auto-pilot loop and its parameters popup is
    driven by two required callbacks; a future view refactor that drops one
    would fail at construction, so pin them here.
    """

    def test_callbacks_exist(self):
        from mimora.ui import ViewCallbacks
        fields = ViewCallbacks.__dataclass_fields__
        self.assertIn("on_autopilot_toggled", fields)
        self.assertIn("on_autopilot_settings", fields)


if __name__ == "__main__":
    unittest.main()
