# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Regression test: a drill take must not leak into the main view.

Double-clicking a word opens a drill. The drill records and scores takes
through the shared ``analyze_recording`` path, which sets the controller's
``last_user_audio`` to the *drill's* recording. When the drill closes, that
must be restored to the main loop's own recording (or cleared), so the main
view's "play my recording" never plays a drill take.

Covers the controller-level save/restore in ``_start_drill`` /
``_cancel_drill_if_active`` without building the full GUI.
"""

import unittest
from unittest import mock

import numpy as np

from mimora import config
from mimora.app import PronunciationTrainerGUI
from mimora.ui import ViewCallbacks


def _callbacks():
    return ViewCallbacks(
        on_gui_btn_press=lambda: None, on_gui_btn_release=lambda: None,
        on_generate_phrase=lambda: None, play_reference=lambda: None,
        play_reference_slow=lambda: None, play_user_recording=lambda: None,
        on_settings_clicked=lambda: None, on_history_clicked=lambda: None,
        on_stats_clicked=lambda: None, on_weakness_clicked=lambda: None,
        on_drill_clicked=lambda: None, on_dictation_clicked=lambda: None,
        on_practice_collapsed_toggled=lambda: None,
        on_show_face_toggled=lambda: None, on_prosody_toggled=lambda: None,
        on_test_reference=lambda: None, on_sound_example=lambda w: None,
        on_word_clicked=lambda w: None, on_word_drill_clicked=lambda w: None,
        on_word_single_drill_clicked=lambda w: None,
        on_take_scored=lambda *a, **k: None,
        on_history_entry=lambda e: None, on_replay_take=lambda a, b: None,
    )


def _make_controller():
    """A bare controller with just what _start_drill/_cancel need."""
    ctrl = PronunciationTrainerGUI.__new__(PronunciationTrainerGUI)
    ctrl.view = mock.MagicMock()
    ctrl.view._cb = _callbacks()
    ctrl.current_phrase = "The thought is deep."
    ctrl.last_user_audio = None
    ctrl._saved_current_phrase = None
    ctrl._saved_last_user_audio = None
    ctrl._drill_saved_last_user_audio = False
    ctrl._original_on_take_scored = None
    ctrl._drill_engine = None
    ctrl._drill_view = None
    ctrl._reset_to_retry = mock.MagicMock()
    # _start_drill opens a real DrillView (Tk Toplevel); stub it out so the
    # test never touches Tk. Leave _drill_view None - _start_drill tolerates
    # that (it only logs when the view did not get created).
    ctrl._open_or_lift = mock.MagicMock()
    ctrl._get_source_text = mock.MagicMock(return_value="The thought is deep.")
    return ctrl


class DrillRecordingLeakTest(unittest.TestCase):
    """The controller restores last_user_audio when the drill closes."""

    def test_drill_take_does_not_leak_into_main_recording(self):
        ctrl = _make_controller()
        # The main loop had no recording before the drill.
        ctrl.last_user_audio = None
        ctrl._start_drill({"type": "single_word", "value": "thought"})
        # A drill take overwrites the shared field with the drill's audio.
        ctrl.last_user_audio = np.zeros(40, dtype=np.float32)
        ctrl._cancel_drill_if_active()
        # The drill's recording must be gone: main has nothing to replay.
        self.assertIsNone(ctrl.last_user_audio)
        # The main view's button state was re-synced after the restore.
        ctrl._reset_to_retry.assert_called_once()

    def test_main_recording_restored_after_drill(self):
        ctrl = _make_controller()
        # The main loop had a recording of its own before the drill.
        main_audio = np.zeros(80, dtype=np.float32)
        ctrl.last_user_audio = main_audio
        ctrl._start_drill({"type": "single_word", "value": "thought"})
        # The drill records its own take.
        ctrl.last_user_audio = np.zeros(40, dtype=np.float32)
        ctrl._cancel_drill_if_active()
        # The main loop's own recording comes back, not the drill's.
        self.assertIs(ctrl.last_user_audio, main_audio)

    def test_starting_new_drill_keeps_main_recording(self):
        ctrl = _make_controller()
        # _start_drill calls _cancel_drill_if_active first to close a prior
        # drill; with no drill active that must NOT clear the main recording.
        main_audio = np.zeros(80, dtype=np.float32)
        ctrl.last_user_audio = main_audio
        ctrl._start_drill({"type": "single_word", "value": "thought"})
        # The main recording was saved, not cleared, by the drill start.
        self.assertIs(ctrl._saved_last_user_audio, main_audio)
        self.assertTrue(ctrl._drill_saved_last_user_audio)

    def test_cancel_without_active_drill_leaves_recording_alone(self):
        ctrl = _make_controller()
        main_audio = np.zeros(80, dtype=np.float32)
        ctrl.last_user_audio = main_audio
        ctrl._cancel_drill_if_active()
        # No drill was active; the main recording is untouched and the button
        # state was not re-synced.
        self.assertIs(ctrl.last_user_audio, main_audio)
        ctrl._reset_to_retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
