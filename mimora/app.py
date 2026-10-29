# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Mimora application: the Tkinter GUI and the startup sequence behind it.

Wires together the pronunciation-trainer components and runs the Tkinter GUI:
the local LLM (LLMManager / LLMServerController), text-to-speech (TTSManager
with the Kokoro / Supertonic backends), audio capture (AudioRecorder) and
pronunciation analysis via the engine
dispatcher (mimora/engine.py, which binds the backend chosen by config.ENGINE -
"phoneme" by default), all driven from PronunciationTrainerGUI, which composes
the TrainerView (mimora/ui.py) for the widgets. :func:`run` at the bottom is
the startup sequence: root logging, the first-run window, then the GUI.

The process-level preamble is deliberately NOT here. Importing this module
pulls in torch, transformers and Kokoro, which takes many seconds on a slow
machine, so argument parsing and ``bootstrap.early_init()`` live in
``mimora/cli.py`` and run BEFORE this import: an entry point that had to import
the application to answer ``--version`` would pay the whole load to print one
line. By the time this module is imported, ``early_init()`` has already run.

Reached as ``mimora`` (console script), ``python -m mimora`` or
``python main.py`` - all three arrive through ``mimora/cli.py``.
"""

import random
import time
import threading
import uuid
import wave
from typing import Dict, Optional
import os

# __version__ for the window title (the log states it in its own header now),
# bootstrap for setup_logging() in run(). early_init() is NOT called here: it
# has to precede the heavy imports below, and cli.py has already run it.
from mimora import __version__, bootstrap

import logging
import tkinter as tk
from tkinter import messagebox, ttk
import numpy as np

from mimora import (config, detect_hardware, first_run, first_run_window,
                    i18n, lifecycle, models_info, prosody, spacy_model_fetch,
                    splash, window_icon)
from mimora.llm import LLMManager, phrase_sampling
from mimora.llm_server_ctl import LLMServerController
from mimora.phrase_source import SourceTextPhraseProvider
from mimora.tts import TTSManager
from mimora.translator import TranslatorManager
from mimora.recorder import (
    AudioRecorder,
    RECORD_MODEL_FILE,
    RECORD_NORMALIZED_FILE,
    RECORD_PHRASE_FILE,
    RECORD_RAW_FILE,
    dump_record_text,
    dump_record_wav,
    normalize_audio,
)
# Pronunciation runs through the engine dispatcher (mimora/engine.py), which binds
# the backend chosen by config.ENGINE ("acoustic" -> pronunciation/acoustic/, "phoneme" ->
# pronunciation/phoneme/) and exposes one interface. This module never imports an
# engine directly, so switching is a single settings.json flip.
from mimora import engine
from mimora.persistence import StatsStore
from mimora.persistence.stats import extract_error_words, extract_phoneme_scores
from mimora.persistence.recorder_store import (
    dump_take_audio,
    dump_take_meta,
    resolve_take_audio,
)
from mimora.drill.engine import DrillEngine
from mimora.dictation.engine import DictationEngine
from mimora.playback import PlaybackController
from mimora.session import SessionState
from mimora.settings_ctl import SettingsGlue
from mimora.ui import TrainerView, ViewCallbacks, LENGTH_FEW_WORDS
from mimora.ui_history_browse import HistoryBrowseView, HistoryCallbacks
from mimora.ui_stats import StatsView, StatsCallbacks
from mimora.ui_weakness import WeaknessView, WeaknessCallbacks
from mimora.ui_drill import DrillView, DrillCallbacks
from mimora.ui_dictation import DictationView, DictationCallbacks
from mimora.settings_window import (
    PREVIEW_PHRASE,
    SettingsCallbacks,
    SettingsWindow,
)


class PronunciationTrainerGUI:
    """Tkinter front-end for the Mimora pronunciation trainer.

    Holds the controller logic (threads, analysis, state transitions) and owns
    the view by composition: ``self.view`` is a TrainerView (ui.py) that builds
    and renders the widgets. The controller drives the UI through
    ``self.view.*`` and the view forwards widget callbacks back to the
    controller. Microphone capture lives in mimora/recorder.py and the local
    LLM server lifecycle in mimora/llm_server_ctl.py.

    Flow per phrase (state machine):
        Prompt   -> the TTS voice speaks an LLM-generated reference phrase.
        Record   -> user repeats it (shared recording path).
        Analyze  -> engine.analyze() runs in a daemon thread.
        Feedback -> score + problem words shown via root.after().
        Loop     -> the user decides: record the same phrase again or generate
                    the next one (result.passed is not enforced - see the
                    Pass-threshold note in AGENTS.md).
    """

    def __init__(self):
        # Deliberately no version here: bootstrap._log_header states it on the
        # first line of every log, and a second copy three lines later would be
        # a second source of one fact.
        logging.info("Starting Mimora Pronunciation Trainer...")

        # Core Tkinter setup
        # The taskbar identity first: Windows reads it when the taskbar
        # button of the window appears.
        window_icon.set_app_user_model_id()
        self.root = tk.Tk(className=window_icon.WM_CLASS)
        window_icon.apply_window_icon(self.root)
        self.root.title(i18n.tr("Mimora · {language} - Pronunciation Trainer v{version}",
                                language=config.TARGET_LANGUAGE, version=__version__))

        # Tk swallows exceptions raised in widget callbacks (button commands,
        # event handlers, after() callbacks) unless we install a handler. A
        # raised exception in a button command would otherwise appear as a
        # "button does nothing" to the user. Log them instead so they are
        # never silent. See the AGENTS.md note on callback exceptions.
        def _tk_callback_exception(exc_type, exc_value, exc_tb):
            logging.error("Unhandled exception in Tk callback:",
                          exc_info=(exc_type, exc_value, exc_tb))

        self.root.report_callback_exception = _tk_callback_exception

        # Fixed width; the window spans the full usable screen height. We query the
        # Windows desktop work area (screen minus the taskbar) so the window fits
        # without being clipped, and fall back to the full screen height on other
        # platforms or if the query fails. Horizontally centered, pinned to the top
        # of the work area. winfo_screen* are valid before the first mainloop
        # iteration, so the size/position are correct from the start.
        window_width = 600
        work_top, avail_height = 0, self.root.winfo_screenheight()
        try:
            import ctypes
            from ctypes import wintypes
            SPI_GETWORKAREA = 0x0030
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                work_top = rect.top
                avail_height = rect.bottom - rect.top
        except Exception:
            logging.debug("Work-area query failed; using full screen height.", exc_info=True)

        x = (self.root.winfo_screenwidth() - window_width) // 2

        # Tk's geometry height is the client area, while the position is the outer
        # frame. With a full work-area height the title bar pushes the client area
        # past the work area, hiding the bottom status bar under the taskbar. So we
        # apply a first guess, measure the actual frame (title bar + borders) once
        # the window is realized, and subtract it so the whole window fits and the
        # status bar stays visible. If the frame can't be measured yet, the guess
        # is kept unchanged.
        self.root.geometry(f"{window_width}x{avail_height}+{x}+{work_top}")
        self.root.update_idletasks()
        caption = max(self.root.winfo_rooty() - self.root.winfo_y(), 0)
        border = max(self.root.winfo_rootx() - self.root.winfo_x(), 0)
        window_height = avail_height - caption - border
        self.root.geometry(f"{window_width}x{window_height}+{x}+{work_top}")

        # App-wide shutdown flag: set in _shutdown_runtime, observed by the
        # blocking audio writes (TTSManager.play_array) so they bail out on
        # exit. The per-playback stop events live in self.playback
        # (mimora/playback.py), composed below once the view exists.
        self.shutdown_event = threading.Event()

        # Recording is press-to-start / auto-stop (see the recording controls
        # section). _record_key_held only tracks whether a record key (spacebar
        # or the Down arrow) is physically held, so key-autorepeat does not fire
        # repeated toggles - it is NOT a "hold to record" flag. The actual
        # capture lives in AudioRecorder; all four callbacks fire on the capture
        # thread, so they marshal every UI touch onto the Tk main thread via
        # root.after.
        self._record_key_held = False
        self.recorder = AudioRecorder(
            on_max_duration=self._on_record_max_duration,
            on_stream_error=self._on_record_stream_error,
            on_silence_stop=self._on_record_silence_stop,
            on_level=self._on_record_level,
        )

        # Audio processing guard - prevents concurrent analysis runs
        self.is_processing_audio = False
        self.processing_lock = threading.Lock()

        # Application readiness and per-phrase practice state
        self.app_ready = False
        self.is_generating = False
        self._closing = False  # guards quit_app against double invocation
        self.current_phrase: Optional[str] = None
        # Translation of the current phrase shown under the phrase card. Filled by
        # the phrase generator when a translation language is selected; kept
        # beside current_phrase so the two are always shown together.
        self.current_translation: str = ""
        # Session score tally and bounded attempt history (mimora/session.py).
        # SessionState is pure data, no Tk: on_take_scored / on_history_entry
        # feed it and forward the returned values to the view.
        self.session = SessionState()
        # Persistent stats/history store (mimora/persistence/stats.py). Owns
        # config/stats.json and per-session recording directories. Created
        # here so ``on_take_scored`` can record each scored take; when
        # STATS_ENABLED is False the store is still constructed but refuses
        # to write, so toggling it later does not need a code change.
        self.stats_store = StatsStore()
        # TTS voice the current reference was synthesized with (logged with
        # every analysis sample - the acoustic calibration is voice-specific).
        self.current_voice: str = config.TTS_VOICE
        # Reference waveform at the active backend's native rate
        # (TTSManager.sample_rate; e.g. Kokoro 24 kHz, Supertonic 44.1 kHz).
        self.reference_audio: Optional[np.ndarray] = None
        self.last_user_audio: Optional[np.ndarray] = None   # 16 kHz recorded attempt
        # Last user name written to settings.json; lets on_user_name_changed
        # skip the file write when the field loses focus without an edit.
        self._saved_user_name: str = config.USER_NAME
        # Whether the prosody block is expanded, mirrored from the show_prosody
        # flag so analysis workers can skip the (expensive) prosody computation
        # while it is collapsed without touching Tk widgets. Written only on the
        # Tk main thread (here and in on_prosody_toggled); workers only read it.
        self._prosody_wanted: bool = config.SHOW_PROSODY

        # Initialize core modular sub-managers
        self.tts_mgr = TTSManager()
        # Offline phrase translator (NLLB-200). Loaded lazily: only when a
        # translation language is selected (see load_components / translate).
        self.translator_mgr = TranslatorManager()
        # Whether the "translator could not be loaded" line has been shown.
        # Written only from the generation thread, which is serialized against
        # itself by the is_generating guard, and read nowhere else.
        self._translator_failure_reported = False

        # LLM backend (used only to generate practice phrases)
        self.llm_backend = config.LLM_BACKEND
        # No-op unless the "llama-server" backend is active.
        self.llm_server = LLMServerController()

        if self.llm_backend == "off":
            # No LLM at all: nothing is loaded or started; practice phrases
            # are the source text's own sentences, verbatim and in order
            # (mimora/phrase_source.py). Duck-typed drop-in for LLMManager in
            # the generation path - this module only calls generate_phrase on it.
            logging.info("LLM backend is off: phrases come verbatim from the source text.")
            self.llm_mgr = SourceTextPhraseProvider()
        elif self.llm_backend == "lm-studio":
            logging.info("Using LM Studio LLM backend (LLMManager).")
            self.llm_mgr = LLMManager()
        else:
            # "llama-server": the llama.cpp binary is started by
            # LLMServerController and then talked to like any other
            # OpenAI-compatible server. Config sanitizes an unknown
            # "llm_backend" to this backend, so it is also the catch-all.
            self.llm_backend = "llama-server"
            logging.info("Using llama-server LLM backend (server subprocess).")
            self.llm_mgr = LLMManager()

        # Compose the view: it builds and owns the widgets, and forwards widget
        # callbacks back to this controller through an explicit ViewCallbacks
        # bundle (the view never holds the controller itself).
        # Settings window (mimora/settings_window.py); at most one instance,
        # created on demand by on_settings_clicked. Persistence, the window
        # mirroring and the "Default" reset live in self.settings_ctl
        # (mimora/settings_ctl.py), composed below after the view.
        self._settings_window: Optional[SettingsWindow] = None
        # v2: Toplevels for the history / stats / weakness / drill views.
        # All of them are created on demand by their on_*_clicked handler.
        self._history_view: Optional[object] = None
        self._stats_view: Optional[object] = None
        self._weakness_view: Optional[object] = None
        self._drill_view: Optional[object] = None
        self._drill_engine: Optional[DrillEngine] = None
        # Dictation mode state (mimora/dictation/engine.py): the engine and the
        # view live for the duration of one dictation session. Unlike drill,
        # dictation never rewires on_take_scored - it has its own submit path.
        self._dictation_view: Optional[object] = None
        self._dictation_engine: Optional[DictationEngine] = None
        # The original on_take_scored handler, kept so drill mode can
        # temporarily rewire it and restore it on exit.
        self._original_on_take_scored: Optional[object] = None
        # The main window's phrase saved when a drill starts, restored on exit.
        self._saved_current_phrase: Optional[str] = None
        # Whether the drill view should auto-advance after each scored take.
        self._drill_auto_next: bool = False
        # The (session_id, take_id) pair of the recording written for the take
        # about to reach the attempt list, so its history row can offer a replay
        # button. None when the take's audio was not saved (HISTORY_MODE is not
        # "with_audio", or the capture was empty). Written on the analysis worker
        # before show_feedback is dispatched, read on the Tk thread in
        # on_history_entry - already set by then.
        self._last_take_audio: Optional[Dict[str, str]] = None
        # Session id used to file take audio while the stats store is off, so a
        # replay button still works without touching the stats file.
        self._audio_session_id: Optional[str] = None

        self.view = TrainerView(self.root, ViewCallbacks(
            on_settings_clicked=self.on_settings_clicked,
            on_history_clicked=self.on_history_clicked,
            on_stats_clicked=self.on_stats_clicked,
            on_weakness_clicked=self.on_weakness_clicked,
            on_drill_clicked=self.on_drill_clicked,
            on_dictation_clicked=self.on_dictation_clicked,
            on_practice_collapsed_toggled=self.on_practice_collapsed_toggled,
            on_gui_btn_press=self.on_gui_btn_press,
            on_gui_btn_release=self.on_gui_btn_release,
            on_show_face_toggled=self.on_show_face_toggled,
            on_prosody_toggled=self.on_prosody_toggled,
            on_test_reference=self.on_test_reference,
            play_user_recording=self.play_user_recording,
            play_reference=self.play_reference,
            play_reference_slow=self.play_reference_slow,
            on_generate_phrase=self.on_generate_phrase,
            on_word_clicked=self.on_word_clicked,
            on_word_drill_clicked=self._on_word_drill_clicked,
            on_word_single_drill_clicked=self._on_word_single_drill_clicked,
            on_sound_example=self.on_sound_example,
            on_take_scored=self.on_take_scored,
            on_history_entry=self.on_history_entry,
            on_replay_take=self.play_take_audio,
        ))
        # Playback machinery: per-playback stop events + the talking-mouth
        # track (mimora/playback.py). Composed after the view because it
        # drives the face and status line through the view facade.
        self.playback = PlaybackController(self.root, self.view, self.tts_mgr,
                                           self.shutdown_event)
        # Settings persistence glue (mimora/settings_ctl.py): persisting,
        # settings-window mirroring and the "Default" reset. The dispatch
        # (on_setting_changed and the on_*_changed handlers) stays here.
        self.settings_ctl = SettingsGlue(
            report_error=self.view.append_error_msg,
            get_window=lambda: self._settings_window,
            dispatch=self.on_setting_changed,
        )
        self.bind_events()

        # Bring the freshly launched window to the foreground and put keyboard
        # focus on it, so the space-to-record hotkey works without a first click.
        # This matters most after a settings restart: the replacement process is
        # launched detached (see restart_app), and Windows does not auto-focus a
        # detached process's window, so the record keys stayed dead until the
        # user clicked the window. Scheduled on the loop (focus is only reliable
        # once the window is realized).
        self.root.after(0, self._grab_initial_focus)

        # Load all models in the background to keep the UI responsive.
        threading.Thread(target=self.load_components, daemon=True).start()

    def _grab_initial_focus(self):
        """Foreground the window and give it keyboard focus (startup/restart).

        The record hotkeys are bound to the toplevel and fire only while it holds
        keyboard focus. A brief topmost flip defeats the Windows foreground lock
        that otherwise keeps a detached, self-launched window in the background,
        then releases it so the window is not pinned above everything. Focus goes
        to the window itself (not the practice-text box), so space records rather
        than typing a space.
        """
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass  # window torn down before the callback ran

    def bind_events(self):
        # Record keys: the spacebar and the Down arrow both toggle capture,
        # mirroring the mic button. Press/release share an auto-repeat guard so
        # holding the key does not fire repeated toggles.
        self.root.bind("<KeyPress-space>", self.on_keyboard_press)
        self.root.bind("<KeyRelease-space>", self.on_keyboard_release)
        self.root.bind("<KeyPress-Down>", self.on_keyboard_press)
        self.root.bind("<KeyRelease-Down>", self.on_keyboard_release)
        # Shortcut keys mirror the actions. Each is gated the same way its control
        # is: ignored while a text field has focus (so the key does its normal job
        # there) and only fired when the matching action is actually enabled, so a
        # hotkey can never trigger something the UI is currently disallowing
        # (e.g. replaying into an open mic). Mapping:
        #   Left -> Reference replay      Right -> New phrase
        #   Up   -> My recording replay   Down  -> mic / record toggle (above)
        #   t    -> reference self-test
        self.root.bind("<Left>", lambda _: self._hotkey(
            self.view.is_reference_enabled, self.play_reference))
        self.root.bind("<Right>", lambda _: self._hotkey(
            self.view.is_generate_enabled, self.on_generate_phrase))
        self.root.bind("<Up>", lambda _: self._hotkey(
            self.view.is_user_enabled, self.play_user_recording))
        self.root.bind("<KeyPress-t>", lambda _: self._hotkey(
            self.view.is_test_enabled, self.on_test_reference))
        self.root.bind("<KeyPress-T>", lambda _: self._hotkey(
            self.view.is_test_enabled, self.on_test_reference))
        self.root.bind("<Escape>", self._on_escape)
        # Clicking anywhere that is not a text input returns keyboard focus to
        # the window, so the hotkeys above resume working after the user edits
        # the source text. Without this, focus would stay in the text field
        # forever: plain frames/labels never take focus on click in Tk, and
        # only button handlers happen to call focus_set().
        self.root.bind_all("<Button-1>", self._on_global_click, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

    def _hotkey(self, is_enabled, action):
        """Run an arrow-key action if it is allowed right now.

        Mirrors button gating: skip when typing in a text field (let the arrow
        do its normal caret navigation) and only act when the corresponding
        button reports itself enabled. Returns "break" when the hotkey fired so
        Tk does not also apply the default arrow behavior to the focused widget.
        """
        if self._typing_in_text_field():
            return None
        if is_enabled():
            action()
            return "break"
        return None

    def _on_escape(self, _event=None):
        """Quit on Escape - except while typing, where it only drops focus.

        quit_app ends in a hard exit (TerminateProcess), so an accidental
        Escape mid-edit would silently discard the typed practice text.
        Typing therefore gets the same gate as every other hotkey; Escape
        then acts as "done editing" and hands focus back to the hotkeys.
        """
        if self._typing_in_text_field():
            self.root.focus_set()
            return "break"
        self.quit_app()
        return None

    # ------------------------------------------------------------------
    # Startup: LLM server + model loading
    # ------------------------------------------------------------------
    def load_components(self):
        logging.info("Starting model loading thread...")
        self.root.after(0, self.view.enter_loading)
        self.root.after(0, self.view.append_system_msg,
                        i18n.tr("Loading TTS and pronunciation models..."))

        try:
            self.tts_mgr.load_model()
            logging.info("TTS model loaded.")

            # The "none" engine loads no model, so the ~1.2 GB message would
            # only confuse; every other engine loads a Wav2Vec2 recognizer.
            if engine.name() != "none":
                self.root.after(0, self.view.append_system_msg,
                                i18n.tr("Loading Wav2Vec2 (pronunciation, ~1.2 GB on first run)..."))
            # Inject app settings into the active engine before it loads any model.
            # The dispatcher builds the engine-specific config from app settings;
            # this is the analyzer's composition root.
            engine.configure()
            engine.load_models()
            logging.info("Pronunciation engine ready (engine=%s).", engine.name())
            # The phoneme engine runs without a calibration tuned for this
            # language (it falls back to English): usable, but flagged as
            # experimental so scores are read with that caveat. The settings
            # window shows the same notice (settings_window.py).
            if config.PHONEME_EXPERIMENTAL:
                logging.warning(
                    "Phoneme engine is EXPERIMENTAL for %s: no model calibration "
                    "for this language yet, scoring falls back to the English "
                    "calibration.", config.TARGET_LANGUAGE)

            # Translator (NLLB) is loaded only when a language is selected at
            # startup, so a session with translation off pays no RAM/time cost.
            # If the user enables a language later, translate() loads on demand
            # - from disk, because enabling it is what triggered the restart
            # that downloaded the model (_offer_translator_download).
            if config.TRANSLATION_LANGUAGE:
                self.root.after(0, self.view.append_system_msg,
                                i18n.tr("Loading translator (NLLB) from disk..."))
                # load_model() logs the success itself - do not repeat it here.
                self.translator_mgr.load_model()

            if self.llm_backend == "off":
                # Nothing to start or connect to; generation is served by
                # SourceTextPhraseProvider (see __init__).
                self.root.after(0, self.view.append_system_msg,
                                i18n.tr("LLM is off - phrases are taken from the practice text."))
            elif self.llm_backend == "llama-server":
                model_name = os.path.basename(config.EXTERNAL_MODEL_PATH)
                self.root.after(0, self.view.append_system_msg,
                                i18n.tr("Starting llama-server with {name}...", name=model_name))
                self.root.after(0, self.view.enter_server_starting)
                if not self.llm_server.start(self.llm_mgr):
                    self.root.after(0, self.view.append_error_msg,
                                    self._server_failure_message())
                    self.root.after(0, self.view.server_failed)
                    return
                self.root.after(0, self.view.append_system_msg, i18n.tr("LLM server is ready."))
                # Before the window becomes ready, so the first phrase already
                # uses the sampling values for the server that answers.
                self.llm_mgr.adopted_server = self.llm_server.adopted
                # Logged because the values are chosen by a flag and are not
                # visible anywhere else in a run: the log is the only proof of
                # which pair this server received.
                temperature, top_p = phrase_sampling(self.llm_server.adopted)
                logging.info(
                    "Phrase sampling: temperature=%s, top_p=%s (%s server).",
                    temperature, top_p,
                    "adopted" if self.llm_server.adopted else "own")
                if self.llm_server.adopted:
                    # Said in the window and not only in the log: phrases now
                    # come from a server this run did not configure, which
                    # explains a model or a speed the settings do not.
                    self.root.after(
                        0, self.view.append_system_msg,
                        i18n.tr("Using the llama-server already running on "
                                "{host}:{port}. It keeps the model it was started with.",
                                host=config.LLM_SERVER_HOST,
                                port=config.LLM_SERVER_PORT))
                served_n_ctx = self.llm_server.served_n_ctx
                if (served_n_ctx is not None
                        and served_n_ctx < config.EXTERNAL_N_CTX):
                    # A warning and not a refusal: short prompts still fit.
                    # In the message list, because the status line is
                    # overwritten by the next state change at once.
                    self.root.after(
                        0, self.view.append_error_msg,
                        i18n.tr("Warning: the LLM server has a context of "
                                "{served} tokens instead of {expected}. "
                                "Long prompts may not fit. See logs/main.log.",
                                served=served_n_ctx,
                                expected=config.EXTERNAL_N_CTX))
            else:
                self.llm_mgr.init_client()
                if not self.llm_mgr.check_connection():
                    self.root.after(0, self.view.append_error_msg,
                                    i18n.tr("Warning: LM Studio is offline. Start it to generate phrases!"))

            if config.WARM_UP:
                self.root.after(0, self.view.enter_warming_up)
                self.tts_mgr.warm_up()
                engine.warm_up()
                if config.TRANSLATION_LANGUAGE:
                    self.translator_mgr.warm_up()
                logging.info("Models warmed up.")
            else:
                # settings.json "warm_up": false - skip the dummy passes so the
                # app is ready sooner on slow machines; the first take pays the
                # first-call latency instead (see config.WARM_UP).
                logging.info("Model warm-up skipped (warm_up=false).")

            self.root.after(0, self.load_practice_text)
            self.root.after(0, self.make_app_ready)
            logging.info("Mimora initialization complete.")

        except BaseException as e:
            # BaseException, not Exception, and that is the point of this
            # clause: spacy.util.run_command calls sys.exit when the command it
            # shells out to fails, which is how misaki reacts to a spaCy model
            # it cannot download. SystemExit is outside the Exception hierarchy,
            # so a narrower clause lets the loader thread die silently and the
            # window sits on "Loading models..." forever.
            #
            # Safe here because this is a daemon thread: SystemExit ends only
            # the thread it is raised in, and KeyboardInterrupt goes to the main
            # thread.
            #
            # The type name is part of the message because str(SystemExit(1))
            # is "1": without it the window would say "Initialization Error: 1".
            logging.exception("Error during initialization thread:")
            self.root.after(0, self.view.append_error_msg,
                            i18n.tr("Initialization Error: {type}: {msg}",
                                    type=type(e).__name__, msg=e))
            self.root.after(0, self.view.init_failed)

    def _server_failure_message(self) -> str:
        """What to tell the user when the llama-server did not come up.

        Three unrelated problems reach this point and they want three different
        answers, so the same three-way predicate the first-run plan uses
        decides which one is given. Collapsing the last two into "point
        llama_server_path at one" told somebody who had already pointed it
        somewhere to do what they had just done.

        This matters more than the first-run window's own version of it: the
        window only appears when something is missing, so on a machine where
        everything is downloaded and no llama.cpp build is pinned for the
        platform, this message is the only thing the user ever sees - at every
        single start.

        A busy port comes first and is not a question about the setup at all:
        the controller refused it before looking at the binary, and its own
        sentence names the port.
        """
        if self.llm_server.last_error:
            return i18n.tr("Error: {msg}", msg=self.llm_server.last_error)
        status = first_run.llama_server_status()
        if status == first_run.SERVER_PRESENT:
            # The binary is there and was still not usable, which is a question
            # about this machine rather than about the setup.
            return i18n.tr("Error: LLM server failed to start. Check model path and GPU memory.")
        if status == first_run.SERVER_MISCONFIGURED:
            # Downloading would not help: it lands in bin/llama/ while the
            # setting keeps winning in the resolver, so the way out is the
            # setting itself.
            return i18n.tr('Error: "llama_server_path" in config/settings.json '
                           'points at a file that is not there. Fix the path, or '
                           'clear it to use the build Mimora installs itself.')
        return i18n.tr('Error: no llama-server binary was found. Install one with '
                       "'python -m mimora.llama_server_fetch', or point "
                       '"llama_server_path" in config/settings.json at a build you '
                       'make yourself, or set "llm_backend" to "lm-studio" and '
                       'generate phrases with LM Studio instead.')

    def load_practice_text(self):
        """Pre-fill the source panel from the practice text file (main thread)."""
        text = ""
        try:
            with open(config.PRACTICE_TEXT_FILE, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            logging.warning(f"Could not read practice text file: {e}")
            # Fallback text comes from the active language profile, so it is
            # in the practiced language (config.LANGUAGE_PROFILES).
            text = config.PRACTICE_TEXT_FALLBACK

        self.view.set_practice_text(text.strip())

    def _load_practice_file(self, path: str):
        """Load *path* into the source panel and persist it (main thread).

        Called by the settings window's practice-file picker: the file is
        applied immediately and stored in settings.json. A relative *path*
        (the stored settings.json form) is resolved against that file's own
        directory, matching the loader convention.
        """
        if not os.path.isabs(path):
            path = str(config.CONFIG_DIR / path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except (OSError, UnicodeDecodeError) as e:
            logging.warning(f"Could not read practice text file {path!r}: {e}")
            self.view.append_error_msg(
                i18n.tr("Could not read {name}: {msg}",
                        name=os.path.basename(path), msg=e))
            self._revert_practice_file_setting()
            return
        if not text:
            self.view.append_error_msg(
                i18n.tr("{name} is empty - nothing to load.",
                        name=os.path.basename(path)))
            self._revert_practice_file_setting()
            return

        self.view.set_practice_text(text)
        self.view.append_system_msg(
            i18n.tr("Loaded practice text: {name}", name=os.path.basename(path)))
        logging.info(f"Practice text loaded from {path!r}.")

        # Persist for the next launch, always as the absolute path, never a
        # relative one: a relative value is read back against the settings
        # file's own directory, and a picked file is rarely under it. The
        # project root is not an alternative base - it stopped being one
        # well-defined place once the settings file and the shipped texts
        # landed in different roots in package mode. Relative values remain
        # fully supported for somebody hand-editing settings.json; they are
        # simply not what the picker writes.
        saved = str(path)
        self.settings_ctl.persist("practice_text_file", saved)
        # Keep the runtime view current for load_practice_text and the file
        # dialog's initialdir; the settings window reads the persisted value.
        config.PRACTICE_TEXT_FILE = str(path)
        self.settings_ctl.sync_window("practice_text_file", saved)

    def _revert_practice_file_setting(self):
        """Roll the settings window back to the persisted practice file.

        A failed load persists nothing, but the settings window has already
        committed the picked path (_emit runs before the controller); left
        alone it would keep displaying a path that is not in settings.json
        until reopened. set_value never re-emits, so no loop.
        """
        saved = config.user_setting("practice_text_file",
                                    config.PRACTICE_TEXT_FILE)
        self.settings_ctl.sync_window("practice_text_file", saved)

    def make_app_ready(self):
        self.app_ready = True
        self.view.enter_app_ready()
        self.view.append_system_msg(i18n.tr("Ready. Generate a phrase, listen, then press SPACE to repeat it."))
        # Speak a personal greeting first, then auto-generate the first phrase.
        # The name and voice are read here, on the main thread (Tk is not
        # thread-safe), and the playback stop event is installed here too (see
        # PlaybackController.new_event); the worker only receives plain values.
        name = self.view.get_user_name()
        voice = self._selected_voice()
        stop_event = self.playback.new_event()
        threading.Thread(target=self._greet_and_start,
                         args=(name, voice, stop_event), daemon=True).start()

    def _greet_and_start(self, name: str, voice: str, stop_event: threading.Event):
        """Speak a greeting, then start the first phrase. (Background thread.)

        The greeting uses the same TTS voice as the reference phrases. Any
        failure here is non-fatal - the first phrase is generated regardless,
        so a TTS hiccup cannot leave the app stuck without a phrase.
        """
        try:
            # Greeting text comes from the active language profile, so it is
            # spoken in the practiced language (config.LANGUAGE_PROFILES).
            greeting = (config.GREETING_NAMED.format(name=name) if name
                        else config.GREETING_ANONYMOUS)
            self.root.after(0, self.view.append_system_msg, greeting)
            audio = self.tts_mgr.synthesize(greeting, voice=voice)
            if audio.size > 0:
                self.playback.play_with_face(audio, self.tts_mgr.sample_rate,
                                             stop_event)
        except Exception:
            logging.exception("Greeting error:")
        finally:
            # Auto-generate the first phrase so the user isn't met with an
            # empty phrase card; afterwards generation is driven by the New
            # phrase button. Scheduled via after() - on_generate_phrase
            # touches widgets, so it must run on the Tk main thread.
            self.root.after(0, self.on_generate_phrase)

    # ------------------------------------------------------------------
    # Phrase generation + Prompt phase
    # ------------------------------------------------------------------
    def _selected_voice(self) -> str:
        """Return the currently selected TTS voice, falling back to the default."""
        try:
            return self.view.get_voice() or config.TTS_VOICE
        except AttributeError:
            return config.TTS_VOICE

    def _next_reference_voice(self) -> str:
        """Voice for the next phrase: the selected one, or a fresh random one.

        With random_voice on, picks among the active accent's voices excluding
        the voice the current reference was synthesized with, so every phrase
        audibly changes voice. The pick is ephemeral - it is never persisted
        and never touches the "voice" setting, which resumes when the option
        is turned off (see config.RANDOM_VOICE).
        """
        if not config.RANDOM_VOICE:
            return self._selected_voice()
        candidates = [v for v in config.TTS_VOICES if v != self.current_voice]
        if not candidates:
            return self._selected_voice()
        voice = random.choice(candidates)
        logging.info(f"Random voice for the next phrase: {voice}.")
        return voice

    def _selected_translation_language(self) -> str:
        """Return the selected translation-language label, or "" when off."""
        try:
            return self.view.get_translation_language() or ""
        except AttributeError:
            return ""

    def on_voice_changed(self, event=None):
        """Regenerate the phrase with the newly chosen voice.

        Re-using the standard generation path is simpler than re-synthesizing the
        current reference, and it also refreshes the analysis reference. If the app
        is busy the change is ignored here and simply applies to the next phrase.
        """
        logging.info(f"Reference voice changed to {self._selected_voice()}.")
        self.settings_ctl.persist("voice", self._selected_voice())
        self.settings_ctl.sync_window("voice", self._selected_voice())
        # Return focus to the window so the spacebar record toggle keeps working.
        self.root.focus_set()
        if self.app_ready and not self.is_generating:
            self.on_generate_phrase()

    def _selected_length(self) -> str:
        """Map the phrase-length setting to generate_phrase's mode ('full'/'fragment')."""
        try:
            return "fragment" if self.view.get_length_label() == LENGTH_FEW_WORDS else "full"
        except AttributeError:
            return "full"

    def on_user_name_changed(self, event=None):
        """Persist the user name after it changes in the Settings window."""
        name = self.view.get_user_name()
        if name == self._saved_user_name:
            return  # unchanged - don't rewrite the file
        if self.settings_ctl.persist("user_name", name):
            self._saved_user_name = name
            logging.info(f"User name saved: {name!r}.")
            self.settings_ctl.sync_window("user_name", name)

    def on_length_changed(self, event=None):
        """Regenerate the phrase when the desired length changes."""
        logging.info(f"Phrase length changed to {self.view.get_length_label()!r}.")
        self.settings_ctl.persist("phrase_length", self._selected_length())
        self.settings_ctl.sync_window("phrase_length", self._selected_length())
        # Reconcile the translation panel and selector. Fragments are translated
        # too, so the length mode does not affect them; this is a consistency
        # refresh, not a mode switch.
        self.view.refresh_translation_ui()
        # Return focus to the window so the spacebar record toggle keeps working.
        self.root.focus_set()
        if self.app_ready and not self.is_generating:
            self.on_generate_phrase()

    def on_translation_language_changed(self, event=None):
        """Persist the chosen translation language and reflect it in the panel.

        The new language is applied to the *next* generated phrase (matching how
        voice/length changes behave); the current phrase is not re-translated, so
        the panel shows '-' until then. Only the panel visibility and the saved
        setting change here.

        Turning translation on for the first time is the exception: the NLLB
        model may still have to be downloaded, and this process cannot do that
        - config decided at import time whether huggingface_hub may reach the
        network, and the first-run window is the one place a multi-gigabyte
        download is shown with a progress bar. So that case restarts into it
        (see _offer_translator_download), which is also why translate() can no
        longer be reached with the model absent.
        """
        language = self.view.get_translation_language()
        logging.info(f"Translation language changed to {language!r}.")
        if language and not first_run.model_present(models_info.NLLB):
            self._offer_translator_download(language)
            return
        self.settings_ctl.persist("translation_language", language)
        self.settings_ctl.sync_window("translation_language", language)
        # The cached translation belonged to the previous language, so drop it and
        # blank the panel to "-"; the next generated phrase fills it for the new
        # language (translations are applied to the next phrase, like voice/length).
        self.current_translation = ""
        self.view.set_translation("")
        self.view.refresh_translation_ui()
        # Return focus to the window so the spacebar record toggle keeps working
        # (a focused combobox would otherwise capture the spacebar).
        self.root.focus_set()

    def _offer_translator_download(self, language: str):
        """Ask before restarting to download the translator model.

        The setting is persisted only when the user agrees, and that ordering
        is the point: the plan the next process builds is read from
        settings.json, so persisting first is what puts NLLB into the first-run
        window's translator level. Declining leaves settings.json untouched and
        puts the live value back, because a saved language with no model on
        disk is exactly the split state this whole change removes - a setting
        that is on, does nothing, and says nothing.

        Not offered as a background download in this window: the process is
        already past config's offline decision, and a second progress UI for
        the same 2.5 GB the first-run window already knows how to show would be
        two implementations of one thing.
        """
        size_mb = models_info.NLLB.size_mb
        if not messagebox.askyesno(
                i18n.tr("Mimora"),
                i18n.tr("Translation needs the offline translator model "
                        "({size} GB), which is not downloaded yet.\n\n"
                        "Mimora will restart to download it. The current session "
                        "(phrase and score) is lost.\n\n"
                        "Restart and download now?",
                        size=f"{size_mb / 1000:.1f}"),
                parent=self.root):
            logging.info("Translator download declined; translation stays off.")
            config.TRANSLATION_LANGUAGE = ""
            self.settings_ctl.sync_window("translation_language", "")
            self.view.refresh_translation_ui()
            self.root.focus_set()
            return
        logging.info("Restarting to download the translator for %r.", language)
        self.settings_ctl.persist("translation_language", language)
        self.restart_app()

    def on_prosody_toggled(self):
        """Apply the prosody collapse toggle and persist the show_prosody flag."""
        self.view.toggle_prosody()
        # Keep the worker-visible flag in sync (read by _compute_prosody_safe
        # on analysis threads; written only here, on the Tk main thread).
        self._prosody_wanted = self.view.get_show_prosody()
        # Keep config current too: it is the live source of truth the reset
        # diffing (settings_ctl._default_differs_from_live) compares against.
        config.SHOW_PROSODY = self.view.get_show_prosody()
        self.settings_ctl.persist("show_prosody", self.view.get_show_prosody())
        self.settings_ctl.sync_window("show_prosody", self.view.get_show_prosody())

    def on_show_face_toggled(self):
        """Apply the face checkbox (show/hide the panel) and persist it."""
        self.view.toggle_face()
        config.SHOW_FACE = self.view.get_show_face()
        self.settings_ctl.persist("show_face", self.view.get_show_face())
        self.settings_ctl.sync_window("show_face", self.view.get_show_face())

    def on_practice_collapsed_toggled(self):
        """Apply the practice-text collapse toggle and persist it."""
        self.view.toggle_practice_text()
        collapsed = self.view.get_practice_collapsed()
        config.PRACTICE_TEXT_COLLAPSED = collapsed
        self.settings_ctl.persist("practice_text_collapsed", collapsed)
        self.settings_ctl.sync_window("practice_text_collapsed", collapsed)

    # ------------------------------------------------------------------
    # Settings window (mimora/settings_window.py)
    # ------------------------------------------------------------------
    def on_settings_clicked(self):
        """Open the settings window, or raise the one already open."""
        if self._settings_window is not None and self._settings_window.exists():
            self._settings_window.lift()
            return
        self._settings_window = SettingsWindow(self.root, SettingsCallbacks(
            on_setting_changed=self.on_setting_changed,
            on_preview_voice=self.on_preview_voice,
            on_restart_requested=self.restart_app,
            on_reset_settings=self.settings_ctl.reset_to_defaults,
        ))

    # ------------------------------------------------------------------
    # v2: History / Stats / Weakness / Drill
    # ------------------------------------------------------------------
    def _open_or_lift(self, attr: str, factory) -> None:
        """Open the Toplevel named by ``attr`` or lift it if already open."""
        view = getattr(self, attr)
        if view is not None and getattr(view, "exists", lambda: False)():
            # The view classes expose the Toplevel as ``_window``; they do
            # not define ``lift``/``focus`` themselves, so lift the window.
            window = getattr(view, "_window", None)
            if window is not None:
                try:
                    window.lift()
                    window.focus()
                except tk.TclError:
                    pass
            return
        setattr(self, attr, factory())

    def on_history_clicked(self) -> None:
        self._open_or_lift("_history_view", lambda: HistoryBrowseView(
            self.root, HistoryCallbacks(
                load_sessions=lambda: self.stats_store.all_sessions(),
                load_drill_sessions=lambda: self.stats_store.all_drill_sessions(),
                load_dictation_sessions=lambda: self.stats_store.all_dictation_sessions(),
                delete_take=lambda sid, tid: self.stats_store.delete_take(
                    sid, tid, delete_audio=(config.HISTORY_MODE == "with_audio")),
                delete_session=lambda sid: self.stats_store.delete_session(
                    sid, delete_audio=(config.HISTORY_MODE == "with_audio")),
                clear_all=lambda: self.stats_store.clear_all(
                    delete_audio=(config.HISTORY_MODE == "with_audio")),
                play_recording=self._play_take_file,
                export_json=self._stats_export_json,
                export_csv=self._stats_export_csv,
                start_drill=self._drill_take_from_history,
            )))

    def on_stats_clicked(self) -> None:
        self._open_or_lift("_stats_view", lambda: StatsView(
            self.root, StatsCallbacks(
                load_data=lambda: {
                    "sessions": self.stats_store.all_sessions(),
                    "all_takes": self.stats_store.all_takes(),
                    "drill_sessions": self.stats_store.all_drill_sessions(),
                },
                export_json=self._stats_export_json,
                export_csv=self._stats_export_csv,
                export_report_png=self._stats_export_report_png,
                export_report_pdf=self._stats_export_report_pdf,
            )))

    def on_weakness_clicked(self) -> None:
        self._open_or_lift("_weakness_view", lambda: WeaknessView(
            self.root, WeaknessCallbacks(
                load_takes=lambda: (
                    self.stats_store.all_sessions(),
                    self.stats_store.all_takes(),
                ),
                start_drill=self._start_drill,
                open_practice=lambda: self.root.lift(),
                play_example=self.on_sound_example,
            ),
            native_language=config.NATIVE_LANGUAGE,
        ))

    def on_drill_clicked(self) -> None:
        """Start a drill with the top suggestion, or a free-form drill
        of the current phrase when there is no history yet."""
        self._start_drill(self._pick_default_drill_target())

    # -- Drill plumbing ---------------------------------------------------

    def _pick_default_drill_target(self) -> dict:
        try:
            from mimora.analysis import (aggregate_error_words,
                                        aggregate_phoneme_scores,
                                        generate_suggestions)
            takes = self.stats_store.all_takes()
            word_agg = aggregate_error_words(takes)
            phoneme_agg = aggregate_phoneme_scores(
                takes, native_language=config.NATIVE_LANGUAGE)
            suggestions = generate_suggestions(
                word_agg, phoneme_agg, native_language=config.NATIVE_LANGUAGE)
            if suggestions:
                return suggestions[0]["target"]
        except Exception:
            logging.exception("Could not pick default drill target:")
        phrase = getattr(self, "current_phrase", None) or ""
        return {"type": "free_form", "value": phrase or "The thought is a thought."}

    def _drill_take_from_history(self, take: dict) -> None:
        """P2#6: start a Drill on one past take's weakest word.

        Called by the history browser's "Re-drill" row button. The take's own
        ``error_words`` is the direct evidence of what failed, so the first one
        (or the first phoneme when a take has phoneme errors but no word errors)
        becomes the drill target. A take with neither falls back to a free-form
        drill of its own phrase - always something to do, never a dead button.
        """
        target = self._drill_target_for_take(take)
        self._start_drill(target)

    @staticmethod
    def _drill_target_for_take(take: dict) -> dict:
        """Pick a Drill target dict for a past take (pure, testable)."""
        error_words = take.get("error_words") or []
        if error_words:
            word = error_words[0]["word"] if isinstance(error_words[0], dict) \
                else str(error_words[0])
            if word:
                return {"type": "word", "value": word}
        phoneme_scores = take.get("phoneme_scores") or []
        if phoneme_scores:
            phoneme = phoneme_scores[0]["ipa"] if isinstance(phoneme_scores[0], dict) \
                else str(phoneme_scores[0])
            if phoneme:
                return {"type": "phoneme", "value": phoneme}
        phrase = (take.get("phrase") or "").strip()
        return {"type": "free_form", "value": phrase or "The thought is a thought."}

    def _start_drill(self, target: dict) -> None:
        logging.info("Drill: _start_drill called with target=%r", target)
        self._cancel_drill_if_active()
        self._saved_current_phrase = self.current_phrase
        logging.info("Drill: saved current_phrase=%r", self._saved_current_phrase)
        engine = DrillEngine(
            target=target,
            source_text=self._get_source_text(),
            pass_threshold=config.DRILL_PASS_THRESHOLD,
            max_attempts=config.DRILL_MAX_ATTEMPTS,
        )
        self._drill_engine = engine

        def _drill_next_phrase() -> str:
            """Advance to the next drill phrase and update the analysis target."""
            phrase = engine.next_phrase()
            self.current_phrase = phrase
            return phrase

        def _drill_previous_phrase() -> str:
            """Go back to the previous drill phrase."""
            phrase = engine.previous_phrase()
            self.current_phrase = phrase
            return phrase

        # ViewCallbacks is frozen, so replace it with a new one that has
        # on_take_scored pointing at the drill bridge.
        from dataclasses import replace
        self._original_on_take_scored = self.view._cb.on_take_scored
        self.view._cb = replace(self.view._cb,
                                on_take_scored=self._drill_on_take_scored)
        self._open_or_lift("_drill_view", lambda: DrillView(
            self.root, DrillCallbacks(
                next_phrase=_drill_next_phrase,
                previous_phrase=_drill_previous_phrase,
                on_record_clicked=lambda: self.view._cb.on_gui_btn_press(),
                on_play_reference_clicked=self._drill_play_reference,
                on_play_recording_clicked=self._drill_play_recording,
                on_previous_clicked=lambda: (self._drill_view._on_previous()
                                             if self._drill_view else None),
                on_skip_clicked=lambda: (self._drill_view._on_skip()
                                         if self._drill_view else None),
                on_scored=lambda s: (self._drill_view.refresh_progress(s)
                                     if self._drill_view else None),
                on_finish=lambda: self._finish_drill(engine),
                on_cancel=lambda: self._cancel_drill_if_active(),
                is_auto_next=lambda: self._drill_auto_next,
                on_auto_next_toggled=self._drill_on_auto_next_toggled,
            ), engine=engine))
        dv = self._drill_view
        if dv is not None:
            try:
                w = getattr(dv, "_window", None)
                state = (f"exists={w.winfo_exists()} mapped={w.winfo_ismapped()} "
                         f"viewable={w.winfo_viewable()} geo={w.winfo_geometry()}" if w else "no _window")
            except tk.TclError as e:
                state = f"tclerr={e}"
            logging.info("Drill: DrillView created (%s)", state)
        else:
            logging.error("Drill: _start_drill finished but self._drill_view is None!")

    def _drill_on_take_scored(self, phrase: str, score: float,
                              graded: bool, details: Optional[dict]) -> None:
        if self._original_on_take_scored is not None:
            self._original_on_take_scored(phrase, score, graded, details)
        engine = self._drill_engine
        if engine is None:
            return
        # Always get the raw score from the result (0-100 scale).
        raw_score = float(score)
        if details is not None:
            result = details.get("result")
            if result is not None:
                raw_score = float(getattr(result, "score", raw_score))
        engine.record_take(phrase, raw_score)
        # Attach the recording written for this take, so the drill session
        # record (and the history browser) can replay it later. The audio was
        # saved by the shared analysis pipeline (_save_attempt_audio) before
        # on_take_scored fired; empty refs mean no audio is available.
        if self._last_take_audio and engine.takes:
            last = engine.takes[-1]
            last.audio_session_id = self._last_take_audio.get("session_id", "")
            last.audio_take_id = self._last_take_audio.get("take_id", "")
            last.audio_files = dict(self._last_take_audio.get("audio_files") or {})
        if self._drill_view is not None:
            self._drill_view.refresh_progress(last_score=raw_score, details=details)
            # Advance to the next phrase if auto-next is enabled.
            if self._drill_auto_next:
                if engine.done:
                    self.root.after(400, lambda: self._finish_drill(engine))
                else:
                    self.root.after(1500, self._drill_view._advance_phrase)
            else:
                if engine.done:
                    self.root.after(400, lambda: self._finish_drill(engine))

    def _drill_play_reference(self) -> None:
        """Synthesize and play the reference audio for the current drill phrase."""
        phrase = self.current_phrase
        if not phrase:
            return
        voice = self.current_voice or config.TTS_VOICE
        thread = threading.Thread(
            target=self._drill_play_reference_worker,
            args=(phrase, voice),
            daemon=True,
        )
        thread.start()

    def _drill_play_reference_worker(self, phrase: str, voice: str) -> None:
        """Background worker: synthesize and play the drill reference."""
        try:
            audio = self.tts_mgr.synthesize(phrase, voice=voice)
            if audio.size > 0:
                self.playback.play_with_face(audio, self.tts_mgr.sample_rate,
                                             self.playback.new_event())
        except Exception as e:
            logging.error(f"Drill reference playback failed: {e}")

    def _drill_play_recording(self) -> None:
        """Play back the current drill take's own recording.

        Uses the in-memory waveform captured by the shared analysis
        pipeline (self.last_user_audio), so it is always the latest take
        and needs no file lookup. Mirrors play_user_recording's gating.
        """
        if self.last_user_audio is None or self.last_user_audio.size == 0:
            return
        self.playback.play_async(self.last_user_audio, config.AUDIO_SAMPLE_RATE,
                                 i18n.tr("Playing your recording..."))

    def _drill_on_auto_next_toggled(self, enabled: bool) -> None:
        """Update the drill's auto-next state."""
        self._drill_auto_next = enabled

    def _finish_drill(self, engine: DrillEngine) -> None:
        try:
            self.stats_store.record_drill_session(engine.to_storage_dict())
        except Exception:
            logging.exception("Failed to persist drill session:")
        view = self._drill_view
        if view is not None:
            try:
                view._show_report(engine.finish())
            except tk.TclError:
                pass
        self._cancel_drill_if_active()

    def _cancel_drill_if_active(self) -> None:
        if self._original_on_take_scored is not None and self.view is not None:
            from dataclasses import replace
            self.view._cb = replace(self.view._cb,
                                    on_take_scored=self._original_on_take_scored)
            self._original_on_take_scored = None
        if self._saved_current_phrase is not None:
            self.current_phrase = self._saved_current_phrase
            self._saved_current_phrase = None
        self._drill_engine = None
        self._drill_view = None

    # -- Dictation plumbing ------------------------------------------------

    def on_dictation_clicked(self) -> None:
        """Open the dictation mode: hear a sentence, type what you heard.

        A new dictation session closes any previous one. Uses the same source
        text as phrase generation so the sentences come from the practice text
        the user is working on.
        """
        self._cancel_dictation_if_active()
        engine = DictationEngine(
            source_text=self._get_source_text(),
            pass_threshold=config.DICTATION_PASS_THRESHOLD,
            max_attempts=config.DICTATION_MAX_ATTEMPTS,
        )
        self._dictation_engine = engine

        def _dictation_next_phrase() -> str:
            phrase = engine.next_phrase()
            self.current_phrase = phrase
            return phrase

        def _dictation_previous_phrase() -> str:
            phrase = engine.previous_phrase()
            self.current_phrase = phrase
            return phrase

        self._open_or_lift("_dictation_view", lambda: DictationView(
            self.root, DictationCallbacks(
                next_phrase=_dictation_next_phrase,
                previous_phrase=_dictation_previous_phrase,
                on_play_clicked=self._dictation_play,
                on_play_slow_clicked=lambda: self._dictation_play(slow=True),
                on_submit=self._dictation_on_submit,
                on_previous_clicked=lambda: (self._dictation_view._on_previous()
                                             if self._dictation_view else None),
                on_skip_clicked=lambda: (self._dictation_view._on_skip()
                                         if self._dictation_view else None),
                on_finish=lambda: self._finish_dictation(engine),
                on_cancel=lambda: self._cancel_dictation_if_active(),
                get_practice_text=self._get_source_text,
                on_source_changed=lambda text: engine.set_source_text(text),
            ), engine=engine))

    def _dictation_play(self, slow: bool = False) -> None:
        """Synthesize and play the current dictation sentence (TTS).

        The sentence text is deliberately kept out of the GUI until the answer
        is checked, so this only has the phrase to speak - never to display.
        """
        phrase = self.current_phrase
        if not phrase:
            return
        voice = self.current_voice or config.TTS_VOICE
        speed = (max(config.REFERENCE_SPEED - config.REFERENCE_SLOW_DELTA,
                     config.REFERENCE_SLOW_MIN) if slow
                 else config.REFERENCE_SPEED)
        thread = threading.Thread(
            target=self._dictation_play_worker,
            args=(phrase, voice, speed),
            daemon=True,
        )
        thread.start()

    def _dictation_play_worker(self, phrase: str, voice: str, speed: float) -> None:
        """Background worker: synthesize and play the dictation sentence."""
        try:
            audio = self.tts_mgr.synthesize(phrase, voice=voice)
            if audio.size > 0:
                self.playback.play_with_face(
                    audio, int(self.tts_mgr.sample_rate * speed),
                    self.playback.new_event())
        except Exception as e:
            logging.error(f"Dictation playback failed: {e}")

    def _dictation_on_submit(self, user_text: str) -> None:
        """Score the typed answer against the current dictation sentence.

        Runs on the Tk thread (the text box is a Tk widget). Scoring is pure
        string work, so it is safe here and fast.
        """
        engine = self._dictation_engine
        view = self._dictation_view
        if engine is None or view is None:
            return
        reference = self.current_phrase or ""
        if not reference:
            return
        from mimora.dictation.scoring import score_dictation
        details = score_dictation(user_text, reference)
        score = details["score"]
        # Wrong words = missed or misspelled reference words; extra = surplus.
        wrong_words = [s["word"] for s in details.get("status", [])
                       if s["state"] in ("missed", "wrong")]
        extra_words = details.get("extra_words", [])
        engine.record_attempt(user_text, reference, score,
                              wrong_words=wrong_words,
                              extra_words=extra_words)
        view.reveal_answer(reference)
        view.refresh_progress(last_score=score, details=details)
        if engine.done:
            self.root.after(400, lambda: self._finish_dictation(engine))

    def _finish_dictation(self, engine: DictationEngine) -> None:
        try:
            self.stats_store.record_dictation_session(engine.to_storage_dict())
        except Exception:
            logging.exception("Failed to persist dictation session:")
        self._cancel_dictation_if_active()

    def _cancel_dictation_if_active(self) -> None:
        self._dictation_engine = None
        self._dictation_view = None

    def _get_source_text(self) -> str:
        # The practice text lives in the VIEW (ui_practice), not on the
        # controller; on_generate_phrase reads it via self.view.get_practice_text().
        # Mirror that here so drills draw phrases from the same corpus the
        # user is actually practicing, instead of falling back to synthetic
        # phrases unrelated to the drill target.
        try:
            text = self.view.get_practice_text()
            if isinstance(text, str) and text.strip():
                return text
        except (AttributeError, tk.TclError):
            pass
        for name in ("practice_text", "source_text"):
            v = getattr(self, name, None)
            if isinstance(v, str):
                return v
        provider = getattr(self, "phrase_provider", None)
        if provider is not None and hasattr(provider, "source_text"):
            return provider.source_text
        return ""

    def _play_take_file(self, session_id: str, take_id: str) -> None:
        """History-browser callback: replay one stored take."""
        self.play_take_audio(session_id, take_id)

    def play_take_audio(self, session_id: str, take_id: str) -> None:
        """Replay one saved attempt from the attempt list or the history browser.

        The wav is resolved and decoded in a background thread because the
        playback call must happen on the Tk main thread - play_async stops any
        current playback and installs the talking-face track. A missing file
        or a failed read reports back on the status line instead of raising
        into the button handler. Skipped while a take is being analyzed or
        recorded, like every other playback: a recording never plays into an
        open microphone.
        """
        if self.recorder.is_active():
            return
        with self.processing_lock:
            if self.is_processing_audio:
                return

        def _worker():
            try:
                path = resolve_take_audio(session_id, take_id)
                if path is None:
                    self.root.after(
                        0, self.view.append_error_msg,
                        i18n.tr("The recording for that attempt is no longer on disk."))
                    return
                with wave.open(str(path), "rb") as w:
                    sample_rate = w.getframerate()
                    audio = np.frombuffer(w.readframes(w.getnframes()),
                                          dtype=np.int16
                                          ).astype(np.float32) / 32768.0
            except Exception:
                logging.exception("Failed to read the attempt recording:")
                self.root.after(0, self.view.append_error_msg,
                                i18n.tr("Could not play that recording."))
                return
            self.root.after(0, self.playback.play_async, audio, sample_rate,
                            i18n.tr("Playing your recording..."))

        threading.Thread(target=_worker, daemon=True).start()

    # -- Export ------------------------------------------------------------

    def _stats_export_json(self):
        from tkinter import filedialog
        import shutil
        default = f"mimora-stats-{_date_stamp()}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=default, title=i18n.tr("Export stats as JSON"))
        if not path:
            return None
        try:
            self.stats_store.save()
            shutil.copy2(self.stats_store.path, path)
            return str(path)
        except Exception:
            logging.exception("Export JSON failed:")
            self.view.append_error_msg(i18n.tr("Could not export stats."))
            return None

    def _stats_export_csv(self):
        from tkinter import filedialog
        import csv
        default = f"mimora-stats-{_date_stamp()}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=default, title=i18n.tr("Export takes as CSV"))
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["session_id", "timestamp", "phrase", "score",
                                 "grade", "attempts", "audio_duration_sec",
                                 "transcript", "words_per_minute", "error_words"])
                for session, take in self.stats_store.all_takes():
                    writer.writerow([
                        session.get("id", ""), take.get("timestamp", ""),
                        take.get("phrase", ""), take.get("score", 0),
                        take.get("grade", ""), take.get("attempts", 1),
                        take.get("audio_duration_sec", 0),
                        take.get("transcript", ""),
                        take.get("words_per_minute", 0),
                        ";".join(w["word"] for w in take.get("error_words") or []),
                    ])
            return str(path)
        except Exception:
            logging.exception("Export CSV failed:")
            self.view.append_error_msg(i18n.tr("Could not export stats."))
            return None

    def _stats_export_report_png(self):
        """P2#7: export the progress report as a PNG image."""
        return self._stats_export_report("png")

    def _stats_export_report_pdf(self):
        """P2#7: export the progress report as a PDF document."""
        return self._stats_export_report("pdf")

    def _stats_export_report(self, kind: str):
        """Shared save-as flow for the offline report renderer."""
        from tkinter import filedialog
        import mimora.report as report_mod
        ext = "png" if kind == "png" else "pdf"
        default = f"mimora-report-{_date_stamp()}.{ext}"
        path = filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            filetypes=[(ext.upper(), f"*.{ext}")],
            initialfile=default,
            title=i18n.tr("Export progress report as {ext}", ext=ext.upper()))
        if not path:
            return None
        try:
            sessions = self.stats_store.all_sessions()
            takes = [t for _, t in self.stats_store.all_takes()]
            if kind == "png":
                data = report_mod.render_report_png(sessions, takes)
            else:
                data = report_mod.render_report_pdf(sessions, takes)
            with open(path, "wb") as fh:
                fh.write(data)
            return str(path)
        except Exception:
            logging.exception("Export report (%s) failed:", kind)
            self.view.append_error_msg(i18n.tr("Could not export stats."))
            return None

    def on_setting_changed(self, key: str, value):
        """Apply one settings-window change (Tk main thread).

        The settings window is the sole editor for the phrase-loop settings
        (voice, phrase length, translation language, reference speed, user
        name): each is written to config (the live source of truth) and then
        applied through the matching handler, which persists it and refreshes
        or regenerates as needed. The prosody collapse toggle also has an
        on-main control, and the face is settings-only now; both route through
        their handler so the view and settings stay in sync. Keys the runtime
        re-reads are persisted and applied via
        settings_ctl.persist_and_apply_live; restart-only keys are just
        persisted (the window shows the pending-restart hint).
        """
        if key == "user_name":
            config.USER_NAME = value
            self.on_user_name_changed()
        elif key == "voice":
            if value in config.TTS_VOICES:
                config.TTS_VOICE = value
                self.on_voice_changed()
            else:
                # A voice of the other (not yet active) accent: valid only
                # after restarting into that accent, so only persist it.
                self.settings_ctl.persist("voice", value)
        elif key == "phrase_length":
            config.PHRASE_LENGTH = value
            self.on_length_changed()
        elif key == "translation_language":
            config.TRANSLATION_LANGUAGE = value
            self.on_translation_language_changed()
        elif key == "show_face":
            self.view.set_show_face(value)
            self.on_show_face_toggled()
        elif key == "practice_text_collapsed":
            self.view.set_practice_collapsed(value)
            self.on_practice_collapsed_toggled()
        elif key == "show_prosody":
            self.view.set_show_prosody(value)
            self.on_prosody_toggled()
        elif key == "reference_speed":
            # Apply live so the next Reference playback uses it, then speak
            # the preview phrase at the new speed (current voice) so the
            # change is audible right away without leaving the Settings
            # window - reuses the Listen preview path, which already honors
            # config.REFERENCE_SPEED (see _preview_voice_worker).
            config.REFERENCE_SPEED = float(value)
            self.settings_ctl.persist(key, float(value))
            self.on_preview_voice(config.TTS_VOICE)
        elif key == "practice_text_file":
            self._load_practice_file(value)
        else:
            self.settings_ctl.persist_and_apply_live(key, value)

    def on_preview_voice(self, voice: str):
        """Speak the preview phrase with *voice* at the current reference speed.

        Triggered by the settings-window Listen button, and also by changing
        the Reference speed setting (see on_setting_changed) so a speed change
        is immediately audible. Gated like the other playback actions: never
        into an open microphone, never during generation or analysis. Only
        voices of the active accent can be previewed (the TTS pipeline
        speaks one accent per run); the settings window disables the Listen
        button otherwise, this check is the thread-safe backstop.
        """
        if not self.app_ready or self.is_generating:
            return
        if voice not in config.TTS_VOICES:
            return
        if self.recorder.is_active():
            return
        with self.processing_lock:
            if self.is_processing_audio:
                return
        self.playback.stop()
        stop_event = self.playback.new_event()
        speed = self._selected_speed()
        status = i18n.tr("Previewing {voice}...", voice=voice) if speed == 1.0 \
            else i18n.tr("Previewing {voice} ({speed}×)...", voice=voice, speed=f"{speed:g}")
        self.view.playing_status(status)
        threading.Thread(target=self._preview_voice_worker,
                         args=(voice, stop_event), daemon=True).start()

    def _preview_voice_worker(self, voice: str, stop_event: threading.Event):
        """Synthesize and play the preview phrase. (Background thread.)"""
        try:
            audio = self.tts_mgr.synthesize(PREVIEW_PHRASE, voice=voice)
            if audio.size > 0:
                # Honor the current Reference speed setting, same trick as
                # _play_reference_at: play the backend-rate waveform at a
                # lowered sample rate so the preview actually demonstrates the
                # speed the user just picked, instead of always playing at 1.0x.
                effective_sr = int(self.tts_mgr.sample_rate * self._selected_speed())
                self.playback.play_with_face(audio, effective_sr, stop_event)
        except Exception:
            logging.exception("Voice preview error:")
            self.root.after(0, self.view.append_error_msg,
                            i18n.tr("Could not preview voice {voice}.", voice=voice))
        finally:
            self.root.after(0, self.playback.finished, stop_event)

    def on_generate_phrase(self):
        if not self.app_ready or self.is_generating:
            return
        with self.processing_lock:
            if self.is_processing_audio:
                return  # don't generate mid-analysis

        # Read the editable source text on the main thread (Tk is not thread-safe).
        source_text = self.view.get_practice_text()
        if not source_text:
            self.view.append_error_msg(i18n.tr("Please enter some practice text first."))
            return

        self.is_generating = True
        self.current_phrase = None
        self.current_translation = ""
        self.last_user_audio = None
        self.view.enter_generating()

        # Capture every selector value here, on the main thread (Tk is not
        # thread-safe), and once per generation, so the phrase, its audio and
        # its translation all stay consistent even if the user changes a
        # selector mid-generation. The worker never reads a widget. The playback
        # stop event is installed here too (see PlaybackController.new_event).
        length = self._selected_length()
        language = self._selected_translation_language()
        voice = self._next_reference_voice()
        speed = self._selected_speed()
        self.playback.stop()  # silence any current playback first
        stop_event = self.playback.new_event()

        threading.Thread(target=self._generate_and_prompt,
                         args=(source_text, length, language, voice, speed, stop_event),
                         daemon=True).start()

    def _generate_and_prompt(self, source_text: str, length: str,
                             language: str, voice: str, speed: float,
                             stop_event: threading.Event):
        """Generate one phrase, synthesize the reference, and play it. (Background thread.)

        All selector values (``length``, ``language``, ``voice``, ``speed``) are
        captured by the caller on the Tk main thread and passed in as plain
        values, and ``stop_event`` was installed there as well (see
        PlaybackController.new_event): this worker must never read widgets or mutate the
        shared playback state (Tk is not thread-safe).
        """
        try:
            phrase = self.llm_mgr.generate_phrase(source_text, length=length)
            if not phrase:
                self.root.after(0, self._phrase_generation_failed,
                                i18n.tr("The model returned no phrase. Try again."))
                return

            # Synthesize the reference once; reused for playback and analysis.
            reference_audio = self.tts_mgr.synthesize(phrase, voice=voice)
            if reference_audio.size == 0:
                self.root.after(0, self._phrase_generation_failed,
                                i18n.tr("Could not synthesize the reference audio."))
                return

            # These are plain-data fields (not Tk widgets), so - unlike widget
            # access - they are intentionally written here on the worker thread.
            # There is no lock: the is_generating / is_processing_audio guards
            # serialize generation against analysis and the next generation, so
            # only one worker ever writes them at a time, and _translate_into_panel
            # re-reads current_phrase purely as a staleness check.
            self.current_phrase = phrase
            # Translation is filled later by _translate_into_panel (after the
            # reference plays); until then the panel shows its "-" placeholder
            # whenever a language is selected.
            self.current_translation = ""
            self.current_voice = voice
            self.reference_audio = reference_audio
            if config.SAVE_RECORDINGS:
                dump_record_wav(reference_audio, RECORD_MODEL_FILE,
                                self.tts_mgr.sample_rate)
                dump_record_text(phrase, RECORD_PHRASE_FILE)
            # Show the phrase and play the reference for the user to hear
            # (stop_event was installed by the caller; see PlaybackController.new_event).
            self.root.after(0, self._show_new_phrase, phrase)
            # Auto-play the reference only when the setting is on. When off,
            # the user can press "Reference ▶" to hear it manually. Shadowing
            # mode forces auto-play: there is nothing to shadow without the
            # reference being heard.
            if config.AUTO_PLAY_PHRASE or config.SHADOWING_ENABLED:
                # Honor the selected reference speed (see play_reference for the
                # lowered-sample-rate slowing approach) instead of always 1.0×.
                effective_sr = int(self.tts_mgr.sample_rate * speed)
                # Shadowing: start the take at the same moment the reference
                # begins, so the user speaks *along with* it (no prompt pause).
                # Scheduled first so the recorder is live before the speaker.
                if config.SHADOWING_ENABLED:
                    self.root.after(0, self._start_shadowing_take)
                self.playback.play_with_face(self.reference_audio, effective_sr,
                                             stop_event)

            # Re-enable the controls now, before translating: NLLB translation is
            # latency-tolerant and (on its first call) pays a one-time model load,
            # so it must not gate the app's readiness. The panel stays at its "-"
            # placeholder until the translation arrives.
            self.root.after(0, self._phrase_ready)
            self._translate_into_panel(phrase, language)

        except Exception as e:
            logging.exception("Phrase generation / prompt error:")
            self.root.after(0, self._phrase_generation_failed,
                            i18n.tr("Error: {msg}", msg=e))

    def _translate_into_panel(self, phrase: str, language: str):
        """Translate *phrase* and push it to the panel. (Background thread.)

        Translates both full sentences and "Few words" fragments; only the
        "translation off" choice is skipped. Guards against a stale result: if a
        newer phrase has replaced this one while the (CPU) translation ran, the
        result is dropped, so the panel never shows a translation that does not
        match the phrase on screen.

        An empty result is reported rather than left as a placeholder, because
        the two reasons for it are indistinguishable on screen: translate()
        swallows every failure and returns "" exactly as it does when there is
        nothing to translate. is_loaded() tells them apart - the model is on
        disk by the time this runs (see mimora/translator.py), so a load that
        did not happen is a real fault worth naming, while a loaded model that
        returned nothing is a per-phrase miss not worth a message.
        """
        if not language:
            return
        if not self.translator_mgr.is_loaded() \
                and not self._translator_failure_reported:
            # The lazy load reads 2.5 GB off disk on the first translated
            # phrase of a session that enabled translation after startup. The
            # download is gone from this path, the wait is not: it measured
            # 1.3 s on an SSD with a warm cache, and a slow disk turns that
            # into a silent window again, which is the finding this whole
            # change came from. Announced only while a load still looks
            # possible - after a reported failure every phrase would repeat it.
            self.root.after(0, self.view.append_system_msg,
                            i18n.tr("Loading translator (NLLB) from disk..."))
        translated = self.translator_mgr.translate(phrase, language)
        if not translated and not self.translator_mgr.is_loaded():
            # Once per session: the load is retried on every phrase (the
            # manager stays retryable by design), and a machine that is out of
            # RAM would otherwise repeat this line for the rest of the run.
            if not self._translator_failure_reported:
                self._translator_failure_reported = True
                self.root.after(0, self.view.append_system_msg,
                                i18n.tr("Translation unavailable: the translator model "
                                        "could not be loaded (see logs/main.log)."))
            return
        # current_phrase is replaced by a fresh generation; if it no longer
        # matches, this translation is for a superseded phrase - drop it.
        if not translated or self.current_phrase != phrase:
            return
        self.current_translation = translated
        self.root.after(0, self.view.set_translation, translated)

    def _show_new_phrase(self, phrase: str):
        self.view.enter_reference_playing(phrase, self.current_translation)
        self.view.append_system_msg(i18n.tr("New phrase: {phrase}", phrase=phrase))

    def _phrase_ready(self):
        self.is_generating = False
        # Enables replay + self-test now that a reference exists.
        self.view.enter_phrase_ready()

    def _phrase_generation_failed(self, message: str):
        self.is_generating = False
        self.view.generation_failed(message)

    # ------------------------------------------------------------------
    # Recording controls (shared recording path)
    # ------------------------------------------------------------------
    def _toggle_recording(self):
        """One press toggles capture: start a take, or stop the running one.

        A take starts on a single press and stops on its own after silence
        (recorder VAD); pressing again while it is running is the manual stop.
        There is no hold-to-talk path. trigger_recording_start /
        trigger_recording_stop keep their own guards, so this only routes.
        """
        if self.recorder.is_active():
            self.trigger_recording_stop()
        else:
            self.trigger_recording_start()

    def on_gui_btn_press(self):
        self._toggle_recording()

    def on_gui_btn_release(self):
        # No-op: recording is press-to-toggle now, not hold-to-talk. Kept so the
        # view's button-release binding (ViewCallbacks.on_gui_btn_release) still
        # has a target.
        pass

    def _on_global_click(self, event):
        """Give keyboard focus back to the window on clicks outside text inputs.

        Tk moves focus into Entry/Text widgets on click but never moves it back
        out when empty space is clicked, so the hotkeys would stay disabled
        (_typing_in_text_field) until some button handler called focus_set().
        Text inputs, comboboxes, and scrollbars are left alone so clicking them
        keeps normal editing/selection behavior. Widgets created inside Tk
        itself (e.g. the combobox dropdown list) reach a bind_all handler as
        path strings rather than instances - those are skipped too. bind_all
        fires for every toplevel of the app, so clicks in other windows (the
        settings dialog) are ignored - stealing their keyboard focus would
        break Tab navigation there and re-arm the main-window hotkeys while
        the dialog looks active.
        """
        if isinstance(event.widget, str):
            return
        if isinstance(event.widget, (tk.Entry, tk.Text, ttk.Combobox, tk.Scrollbar)):
            return
        if event.widget.winfo_toplevel() is not self.root:
            return
        self.root.focus_set()

    def _typing_in_text_field(self) -> bool:
        """True when a text-input widget owns focus - spacebar should type, not record.

        Disabled Text widgets do not count: nothing can be typed into them, yet
        on Windows Tk focuses a Text on click even when it is disabled (so a
        selection can be shown). Without this exception, clicking the read-only
        hero phrase or the feedback panel would silently kill the space/arrow
        hotkeys until something else took focus.
        """
        widget = self.root.focus_get()
        if isinstance(widget, tk.Text):
            return str(widget.cget("state")) != tk.DISABLED
        return isinstance(widget, tk.Entry)

    def on_keyboard_press(self, event):
        # Holding a record key (space or Down) makes Tk fire KeyPress repeatedly
        # (auto-repeat). _record_key_held gates those out so one physical press is
        # one toggle; it is cleared on the matching KeyRelease. Returning "break"
        # when handled stops Tk from also applying the key's default behavior
        # (e.g. the Down arrow moving focus).
        if event.keysym in ("space", "Down") and not self._record_key_held \
                and not self._typing_in_text_field():
            self._record_key_held = True
            self._toggle_recording()
            return "break"
        return None

    def on_keyboard_release(self, event):
        # Only clears the auto-repeat guard; the take keeps recording until it
        # auto-stops on silence or the user presses a record key / clicks the mic
        # again.
        if event.keysym in ("space", "Down"):
            self._record_key_held = False

    def _can_record(self) -> bool:
        """Recording is only allowed once a phrase is ready and nothing else is busy."""
        if not self.app_ready or self.is_generating or self.current_phrase is None:
            return False
        with self.processing_lock:
            return not self.is_processing_audio

    def trigger_recording_start(self):
        if not self._can_record():
            return
        if self.recorder.is_active():
            return
        self.playback.stop()  # silence any reference playback before recording
        if not self.recorder.start():
            return
        # Lock out every playback/diagnostic action for the duration of the
        # take: playing the reference (or the previous attempt) into an open
        # microphone would end up inside the recording and corrupt analysis.
        # Re-enabled in show_feedback / _reset_to_retry.
        self.view.enter_recording()

    def _start_shadowing_take(self):
        """Start a take *while* the reference keeps playing (shadowing mode).

        Unlike trigger_recording_start, this does NOT stop the current
        playback: the whole point of shadowing is that the user speaks along
        with the reference, so the speaker must stay live while the mic opens.
        The reference may or may not be playing yet (the worker schedules this
        just before play_with_face); if it is not, the recorder simply captures
        the tail of the silence, which the VAD trims. Runs on the Tk thread.
        """
        if not self._can_record():
            return
        if self.recorder.is_active():
            return
        if not self.recorder.start():
            return
        self.view.enter_recording()

    def trigger_recording_stop(self):
        if not self.recorder.stop():
            return

        # Claim the analysis slot *synchronously, before* spawning the worker.
        # Claiming it inside the worker left a gap in which a 'New phrase' click
        # could pass the is_processing_audio check and replace current_phrase /
        # reference_audio mid-analysis (feedback against the wrong phrase).
        with self.processing_lock:
            if self.is_processing_audio:
                logging.warning("Analysis already running, skipping duplicate take.")
                return
            self.is_processing_audio = True

        self.root.after(0, self.view.enter_analyzing)
        # The stop event for the take's playback is installed here, on the main
        # thread (see PlaybackController.new_event); the worker only receives it.
        stop_event = self.playback.new_event()
        threading.Thread(target=self._finalize_recording, args=(stop_event,),
                         daemon=True).start()

    def _finalize_recording(self, stop_event: threading.Event):
        """Join the record thread, then run analysis - off the main thread.

        The is_processing_audio slot was claimed by trigger_recording_stop and
        is always released here, whatever happens. ``stop_event`` (installed by
        the caller) governs the playback of the just-recorded take.
        """
        # The start of the take preparation (join, read, resample, normalize),
        # logged by analyze_recording as "Take ready in ...".
        prep_start = time.perf_counter()
        try:
            if not self.recorder.join():
                # The capture thread is stuck (e.g. the device hangs on close)
                # and its callback may still be appending chunks. Reading them
                # now would race the writer - drop the take.
                self.root.after(0, self.view.append_error_msg,
                                i18n.tr("Audio device did not stop in time - take discarded, please try again."))
                self.root.after(0, self._reset_to_retry)
                return
            self.analyze_recording(stop_event, prep_start)
        finally:
            with self.processing_lock:
                self.is_processing_audio = False

    def _on_record_max_duration(self):
        """The take hit MAX_RECORD_SECONDS (called on the capture thread).

        Route through the normal stop path on the main thread so the take is
        finalized and analyzed exactly like a manual stop.
        """
        self.root.after(0, self.view.append_error_msg,
                        i18n.tr("Reached maximum record limit - take cut off."))
        self.root.after(0, self.trigger_recording_stop)

    def _on_record_silence_stop(self):
        """The take auto-stopped after silence (called on the capture thread).

        Route through the normal stop path on the main thread so the take is
        finalized and analyzed exactly like a manual stop. Unlike the max-
        duration cutoff this is the expected, designed ending, so no error is
        shown.
        """
        self.root.after(0, self.trigger_recording_stop)

    def _on_record_level(self, level: float):
        """Live mic level during a take (called on the capture thread).

        Forwarded to the recording indicator on the Tk main thread so the user
        can see the mic is hearing them while the silence auto-stop runs.
        """
        self.root.after(0, self._apply_record_level, level)

    def _apply_record_level(self, level: float):
        """Repaint the mic level indicator, but only while still recording. (Tk thread.)

        A level frame can be queued just before the take stops; applying it after
        the stop (enter_analyzing / idle already repainted the mic) would draw the
        red "recording" level disc back on top of the processing/idle glyph. The
        capture thread's stop path clears is_recording before enter_analyzing runs,
        so gating on recorder.is_active() drops these stale late frames.
        """
        if self.recorder.is_active():
            self.view.set_record_level(level)

    def _on_record_stream_error(self):
        """The input stream failed (called on the capture thread).

        Re-enable the buttons disabled at recording start (without this a
        failed stream open would leave the whole UI locked), then show the
        error status on top of the reset's default "Ready".
        """
        self.root.after(0, self._reset_to_retry)
        self.root.after(0, self.view.recording_failed)

    # ------------------------------------------------------------------
    # Analyze phase
    # ------------------------------------------------------------------
    def _compute_prosody_safe(self, user_audio: np.ndarray, user_sr: int) -> dict:
        """Prosody contours for the feedback charts, or ``{}`` when skipped/failed.

        Prosody is the engine-agnostic audio layer: it is computed here, from the
        same waveforms the engine scored, so the charts work identically across
        engines. Two cases degrade to empty contours instead:
          * Both prosody charts are hidden: the pitch tracking (librosa.pyin)
            costs seconds of CPU per take on slow machines, so hidden charts
            must not pay for it. Re-enabling a chart shows data again from the
            next take (the skipped take has nothing cached to draw).
          * The computation failed: a prosody failure must not discard the
            completed analysis - the score is already valid, so the feedback is
            shown without charts.
        Runs on analysis worker threads; reads only plain values (never widgets).
        """
        if not self._prosody_wanted:
            return {}
        try:
            return prosody.compute_prosody(
                user_audio=user_audio,
                user_sr=user_sr,
                reference_audio=self.reference_audio,
                # The reference plays at the active backend's native rate, so
                # the pitch/energy contours must be extracted at that rate too
                # (24 kHz Kokoro, 44.1 kHz Supertonic) - never a constant.
                reference_sr=self.tts_mgr.sample_rate,
            )
        except Exception:
            logging.exception("Prosody computation failed; showing the result without charts:")
            return {}

    def on_test_reference(self):
        """Diagnostic: feed the reference audio through analysis instead of a
        recording. Since the reference is compared against itself it should score
        near 100 - a quick way to sanity-check the pipeline without speaking.
        """
        if not self.app_ready or self.is_generating:
            return
        if self.current_phrase is None or self.reference_audio is None:
            return
        if self.recorder.is_active():
            return  # never play the reference into an open microphone
        with self.processing_lock:
            if self.is_processing_audio:
                return
            self.is_processing_audio = True

        self.playback.stop()  # silence any current playback first
        # The playback stop event is installed here, on the main thread (see
        # PlaybackController.new_event); the worker only receives it.
        stop_event = self.playback.new_event()
        threading.Thread(target=self._run_reference_test, args=(stop_event,),
                         daemon=True).start()

    def _run_reference_test(self, stop_event: threading.Event):
        """Play the reference, then analyze it against itself (off the main thread)."""
        try:
            # Play the reference back first (stop_event was installed by the
            # caller; see PlaybackController.new_event).
            self.root.after(0, self.view.enter_playing, i18n.tr("Playing reference..."))
            self.playback.play_with_face(self.reference_audio,
                                         self.tts_mgr.sample_rate, stop_event)

            # Then run analysis with the reference as both inputs.
            self.root.after(0, self.view.enter_analyzing, i18n.tr("Testing with reference..."))
            result = engine.analyze(
                user_audio=self.reference_audio,
                expected_text=self.current_phrase,
                reference_audio=self.reference_audio,
                # The self-test feeds the reference as the "user" take, so both
                # rates are the active TTS backend's native output rate.
                user_sr=self.tts_mgr.sample_rate,
                reference_sr=self.tts_mgr.sample_rate,
                voice=self.current_voice,
                is_reference=True,                # self-test: excluded from GOOD calibration
            )
            result.prosody = self._compute_prosody_safe(self.reference_audio,
                                                         self.tts_mgr.sample_rate)
            self.root.after(0, self.view.show_feedback, result, self.current_phrase,
                            self._has_user_recording(), True)  # is_self_test
        except Exception:
            logging.exception("Reference self-test error:")
            self.root.after(0, self.view.append_error_msg,
                            i18n.tr("Reference test failed."))
            self.root.after(0, self._reset_to_retry)
        finally:
            with self.processing_lock:
                self.is_processing_audio = False

    def analyze_recording(self, stop_event: threading.Event,
                          prep_start: float):
        """Play the take back, then score it.

        ``prep_start`` is the ``time.perf_counter()`` value from the start of
        _finalize_recording, for the "Take ready in ..." log line.
        """
        try:
            audio = self.recorder.get_audio()
            if audio is None or len(audio) < config.AUDIO_SAMPLE_RATE * 0.2:
                logging.warning("Captured audio too short or empty.")
                self.root.after(0, self.view.append_error_msg,
                                i18n.tr("Audio is too short. Speak a little longer and try again."))
                self.root.after(0, self._reset_to_retry)
                return

            if config.SAVE_RECORDINGS:
                dump_record_wav(audio, RECORD_RAW_FILE, config.AUDIO_SAMPLE_RATE)

            raw_audio = audio
            audio = normalize_audio(audio)
            self.last_user_audio = audio

            if config.SAVE_RECORDINGS:
                dump_record_wav(audio, RECORD_NORMALIZED_FILE, config.AUDIO_SAMPLE_RATE)

            # Persist this take's recording for replay from the attempt list -
            # every take, scored or unscored, since recording audio does not
            # depend on the engine being able to grade it. Best effort: a write
            # failure only costs the replay button on this take's row.
            self._save_attempt_audio(raw_audio, audio)

            if self.current_phrase is None or self.reference_audio is None:
                self.root.after(0, self.view.append_error_msg,
                                i18n.tr("No active phrase to compare against."))
                self.root.after(0, self._reset_to_retry)
                return

            # Play the just-recorded audio back to the user right away, before the
            # (slower) pronunciation analysis runs (stop_event was installed by
            # trigger_recording_stop; see PlaybackController.new_event). Skipped
            # when self-playback is turned off in Settings - go straight to
            # analysis then.
            #
            # Logged on both sides, and that is not decoration: play_with_face
            # blocks for the length of the take while the analysis timer below
            # starts only after it, so without these two lines the log jumps
            # from "Stopping audio recording" to "analysis done in 277ms" with
            # a silent multi-second hole that reads as an unexplained stall.
            #
            # The preparation time is in the same line. On the first take in
            # a fresh environment it is about 10 s (the first librosa.resample),
            # and without its own number that time was read as playback.
            prep_seconds = time.perf_counter() - prep_start
            if config.PLAYBACK_OWN_RECORDING:
                playback_seconds = len(self.last_user_audio) / config.AUDIO_SAMPLE_RATE
                logging.info("Take ready in %.1fs; playing it back to the user "
                             "(%.1fs) before analysis.", prep_seconds,
                             playback_seconds)
                self.root.after(0, self.view.enter_playing,
                                i18n.tr("Playing your recording..."))
                self.playback.play_with_face(self.last_user_audio,
                                             config.AUDIO_SAMPLE_RATE, stop_event)
                logging.info("Playback finished; starting analysis.")
            else:
                logging.info("Take ready in %.1fs; starting analysis.",
                             prep_seconds)
            self.root.after(0, self.view.enter_analyzing)

            analyze_start = time.perf_counter()
            result = engine.analyze(
                user_audio=audio,
                expected_text=self.current_phrase,
                reference_audio=self.reference_audio,
                user_sr=config.AUDIO_SAMPLE_RATE,
                reference_sr=self.tts_mgr.sample_rate,
                voice=self.current_voice,
            )
            elapsed_ms = (time.perf_counter() - analyze_start) * 1000
            logging.info(f"Pronunciation analysis done in {elapsed_ms:.0f}ms. Score={result.score}")

            result.prosody = self._compute_prosody_safe(audio,
                                                         config.AUDIO_SAMPLE_RATE)

            # Fluency axis: blend the take's tempo + intonation shape into the
            # engine score when enabled. Computed here (after the engine's
            # segmental score) so the blended score drives the same bucket/grade
            # path the engine already calibrated.
            if config.FLUENCY_ENABLED:
                self._apply_fluency(result, audio, config.AUDIO_SAMPLE_RATE)

            # Shadowing axis: blend how well the take kept up with the reference
            # (delay/overlap alignment) when shadowing mode is enabled. Same
            # re-bucket semantics as fluency: the blended score drives the same
            # verdict the engine would have produced.
            if config.SHADOWING_ENABLED:
                self._apply_shadowing(result, audio, config.AUDIO_SAMPLE_RATE)

            self.root.after(0, self.view.show_feedback, result, self.current_phrase,
                            self._has_user_recording(),
                            False, self._drill_engine is None)

        except Exception:
            logging.exception("Error in analyze_recording:")
            self.root.after(0, self.view.append_error_msg,
                            i18n.tr("Analysis error. Please try again."))
            self.root.after(0, self._reset_to_retry)

    def _apply_fluency(self, result, audio, user_sr) -> None:
        """Compute the fluency axis and blend it into ``result`` (in place).

        The axis (mimora/fluency.py) needs the same waveforms the engine scored
        plus the ASR transcript and the recorded duration; all are available on
        the analysis worker thread. Blending re-derives ``passed``/``bucket``/
        ``grade`` exactly the way the active engine would, so the score the user
        sees and the score the loop gates on stay consistent. Best effort: a
        fluency failure degrades to the engine score unchanged.
        """
        try:
            from mimora import fluency
            transcript = getattr(result, "transcription", "") or ""
            duration = len(audio) / user_sr if user_sr > 0 else 0.0
            fx = fluency.compute_fluency(
                user_audio=audio,
                user_sr=user_sr,
                reference_audio=self.reference_audio,
                reference_sr=self.tts_mgr.sample_rate,
                transcript=transcript,
                audio_duration_sec=duration,
                reference_wpm=config.FLUENCY_REFERENCE_WPM,
            )
        except Exception:
            logging.exception("Fluency computation failed; keeping engine score:")
            return
        result.fluency_axis = float(fx["score"])
        result.speed_label = str(fx.get("speed_label", ""))
        result.wpm = float(fx.get("wpm", 0.0))
        logging.info(
            "[fluency] axis=%.1f (tempo=%.1f prosody=%.1f) wpm=%.1f ratio=%.2f label=%r",
            fx["score"], fx.get("tempo_score", 0.0), fx.get("prosody_score", 0.0),
            fx.get("wpm", 0.0), fx.get("wpm_ratio", 0.0), fx.get("speed_label", ""))

        weight = config.FLUENCY_WEIGHT / 100.0
        if weight <= 0:
            return  # axis computed and logged; no blend requested
        blended = round(result.score * (1.0 - weight) + float(fx["score"]) * weight, 1)
        result.score = blended
        # Re-derive the verdict the way the engine would for the new score. The
        # phoneme/hybrid engines bucketize (bucket >= 0); the acoustic engine
        # uses the raw threshold. bucketize_score is the engine's own public
        # wrapper, so the calibrated cutpoints/pass_bucket stay authoritative.
        if result.bucket >= 0:
            try:
                from pronunciation.phoneme.speech import bucketize_score
                bucket, user_percent, grade, grade_value, passed = bucketize_score(blended)
                result.bucket = bucket
                result.user_percent = user_percent
                result.grade = grade
                result.grade_value = grade_value
                result.passed = passed
            except Exception:
                logging.exception("Re-bucketing the blended score failed:")
        else:
            result.passed = blended >= config.PRONUNCIATION_SCORE_THRESHOLD
        logging.info("[fluency] blended score=%.1f passed=%s", result.score, result.passed)

    def _apply_shadowing(self, result, audio, user_sr) -> None:
        """Compute the shadowing axis and blend it into ``result`` (in place).

        The axis (mimora/shadowing.py) needs the same waveforms the engine
        scored; both are available on the analysis worker thread. Blending
        re-derives ``passed``/``bucket``/``grade`` exactly the way the active
        engine would, so the score the user sees and the score the loop gates
        on stay consistent (the same approach as _apply_fluency). Best effort:
        a shadowing failure degrades to the engine score unchanged.
        """
        try:
            from mimora import shadowing
            sx = shadowing.compute_shadowing(
                user_audio=audio,
                user_sr=user_sr,
                reference_audio=self.reference_audio,
                reference_sr=self.tts_mgr.sample_rate,
            )
        except Exception:
            logging.exception("Shadowing computation failed; keeping engine score:")
            return
        result.shadowing_axis = round(float(sx["shadowing_axis"]) * 100.0, 1)
        result.delay_sec = float(sx["delay_sec"])
        result.overlap = float(sx["overlap"])
        logging.info(
            "[shadowing] axis=%.1f delay=%.3fs overlap=%.3f",
            result.shadowing_axis, result.delay_sec, result.overlap)

        weight = config.SHADOWING_WEIGHT / 100.0
        if weight <= 0:
            return  # axis computed and logged; no blend requested
        blended = round(
            result.score * (1.0 - weight) + result.shadowing_axis * weight, 1)
        result.score = blended
        # Re-derive the verdict the way the engine would for the new score.
        if result.bucket >= 0:
            try:
                from pronunciation.phoneme.speech import bucketize_score
                bucket, user_percent, grade, grade_value, passed = bucketize_score(blended)
                result.bucket = bucket
                result.user_percent = user_percent
                result.grade = grade
                result.grade_value = grade_value
                result.passed = passed
            except Exception:
                logging.exception("Re-bucketing the blended score failed:")
        else:
            result.passed = blended >= config.PRONUNCIATION_SCORE_THRESHOLD
        logging.info("[shadowing] blended score=%.1f passed=%s", result.score, result.passed)

    def _has_user_recording(self) -> bool:
        """True when a user take exists to replay or feed back against.

        The reference self-test reaches show_feedback without recording, so the
        "My recording" button must follow this rather than always being enabled.
        """
        return self.last_user_audio is not None and self.last_user_audio.size > 0

    def _reset_to_retry(self):
        """Return to a state where the user can record the current phrase again.

        Also re-enables the buttons disabled while recording, according to what
        is actually available (phrase / last recording).
        """
        self.view.enter_retry(has_phrase=bool(self.current_phrase),
                              has_recording=self._has_user_recording())

    # ------------------------------------------------------------------
    # Playback (reference / own recording)
    # ------------------------------------------------------------------
    def _selected_speed(self) -> float:
        """The reference-playback speed to use, from the Settings value."""
        return config.REFERENCE_SPEED

    def play_reference(self):
        """Replay the reference at the configured speed (Reference button)."""
        self._play_reference_at(self._selected_speed())

    def _slow_speed(self) -> float:
        """The slow-replay speed: one step below the Settings value."""
        return max(config.REFERENCE_SPEED - config.REFERENCE_SLOW_DELTA,
                   config.REFERENCE_SLOW_MIN)

    def play_reference_slow(self):
        """Replay the reference one step slower than normal (the Slow ▶ button)."""
        self._play_reference_at(self._slow_speed())

    def _speak_word_at(self, word: str, speed: float, status: str):
        """Synthesize a single word and play it at ``speed``.

        Shared by the two single-word playbacks - a clicked hero-card word
        and the phoneme-example badge - which differ only in speed and status
        line. ``status`` is the already-formatted status-bar text. Gated like the
        other playbacks: never into an open microphone or while an analysis
        playback is in flight.
        """
        word = word.strip()
        if not word or not self.app_ready or self.is_generating:
            return
        if self.recorder.is_active():
            return  # never play into an open microphone
        with self.processing_lock:
            if self.is_processing_audio:
                return
        self.playback.stop()  # silence any current playback first
        # The stop event is installed here on the main thread (see
        # PlaybackController.new_event); the worker only receives it.
        stop_event = self.playback.new_event()
        self.view.playing_status(status)

        def _worker():
            try:
                audio = self.tts_mgr.synthesize(word, voice=self.current_voice)
                if audio is None or audio.size == 0:
                    return
                self.playback.play_with_face(
                    audio, int(self.tts_mgr.sample_rate * speed), stop_event)
            except Exception:
                logging.exception("Word playback error:")
            finally:
                self.root.after(0, self.playback.finished, stop_event)

        threading.Thread(target=_worker, daemon=True).start()

    def on_word_clicked(self, word: str):
        """Speak one phrase word slowly (click on any hero-card word).

        Synthesizes just that word and plays it at the slow-replay speed, via
        the same lowered-sample-rate slowing as the Slow ▶ reference button.
        """
        self._speak_word_at(word, self._slow_speed(),
                            i18n.tr("Playing '{word}' slowly...", word=word.strip()))

    def on_sound_example(self, word: str):
        """Speak a phoneme's example word at normal speed (WORK ON badge click).

        The example word (e.g. "put" for /ʊ/) is a natural rendering of the
        target sound, so it plays at 1.0x rather than the slowed single-word
        replay used for the clicked phrase words.
        """
        self._speak_word_at(word, 1.0,
                            i18n.tr("Playing example '{word}'...", word=word.strip()))

    def _on_word_drill_clicked(self, word: str):
        """Right-click a mispronounced word to start a drill on it.

        Drills the word in sentence context (the engine picks source phrases
        that contain the word), so the mispronunciation is practiced where it
        occurred. The double-click path uses
        :meth:`_on_word_single_drill_clicked` to drill the word alone.
        """
        target = {"type": "word", "value": word.strip()}
        self._start_drill(target)

    def _on_word_single_drill_clicked(self, word: str):
        """Double-click a phrase word to start a drill on that word alone.

        The word itself becomes the whole practice phrase (a ``single_word``
        drill), so the user repeats just that word - not a sentence around it.
        """
        target = {"type": "single_word", "value": word.strip()}
        self._start_drill(target)

    def on_take_scored(self, phrase: str, score: float, graded: bool = False,
                       details: Optional[dict] = None):
        """Record a scored take into the session tally and refresh the ring.

        Called by the view once a take has a user-facing score. The tally
        itself (distinct-phrase count, mean of per-phrase best scores, its
        scale and the current phrase's attempt list) lives in SessionState
        (mimora/session.py); this handler only pushes the returned values
        into the hero card's progress ring and its attempt-dot column.

        The optional ``details`` dict carries the engine's
        PronunciationResult (in ``details["result"]``) and the ASR
        transcript (``details["transcript"]``). When present and stats
        persistence is on, we persist the take with error words, phoneme
        scores and the audio length pulled off the just-recorded waveform.
        """
        stats = self.session.record_take(phrase, score, graded)
        if stats is not None:
            self.view.update_session_stats(*stats)
        # Persistence path: only when the store is enabled and we have
        # the full result (unscored takes reach here with details=None).
        if config.STATS_ENABLED and details is not None:
            try:
                self._persist_take(phrase, score, graded, details)
            except Exception:  # noqa: BLE001 - never break the practice loop
                logging.exception("Failed to persist take:")

        # Auto-generate the next phrase after a delay, so the user
        # can see the score and error feedback before the phrase changes.
        # Gated by the pass gate: when on, a take that did not pass does not
        # auto-advance (the view already disabled "Next phrase" for it; this
        # keeps auto-next consistent with the manual button).
        if config.AUTO_NEXT_PHRASE:
            result = details.get("result") if details is not None else None
            if (not config.PASS_GATING_ENABLED) or (result is not None and getattr(result, "passed", True)):
                delay_ms = int(config.AUTO_NEXT_DELAY * 1000)
                self.root.after(delay_ms, self.on_generate_phrase)

    def _persist_take(self, phrase: str, score: float, graded: bool,
                      details: dict) -> None:
        """Write one take to the persistent store.

        Extracts error_words / phoneme_scores / transcript off the engine
        result, computes the audio duration from the last recorded
        waveform, and dumps the audio files if HISTORY_MODE says so. The
        whole thing is best-effort - a persistence error must never
        surface as an exception back into the practice loop.
        """
        if config.HISTORY_MODE == "none":
            return
        result = details.get("result")
        transcript = details.get("transcript") or ""
        audio_duration = 0.0
        audio_files = None
        # Best-effort audio length from the last recorded waveform. If the
        # user pressed Record but the take was empty, duration stays 0.
        audio = self.last_user_audio
        if audio is not None and audio.size > 0:
            audio_duration = audio.size / config.AUDIO_SAMPLE_RATE
        error_words = extract_error_words(result)
        phoneme_scores = extract_phoneme_scores(result)

        # Always use the raw score from the result (0-100 scale).
        raw_score = float(score)
        if result is not None:
            raw_score = float(getattr(result, "score", raw_score))

        # Ask the store to open (or split) the current session first, so we
        # know the session_id to key the audio directory off.
        session = self.stats_store.open_session_if_needed()
        if session is None:
            return
        # Reuse the audio _save_attempt_audio already wrote for this take, so
        # the stats record and the wav files share one directory and nothing
        # is dumped twice. It is only reusable while both still refer to the
        # same session - an idle split in between points at a different one.
        saved = self._last_take_audio
        take_id = (saved or {}).get("take_id") or (
            f"t-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}")
        if saved is not None and saved.get("session_id") == session["id"]:
            audio_files = saved.get("audio_files")
        # No reuse (stats was off when the take was scored, a session split
        # happened, or the earlier write failed): dump here so the persisted
        # take still points at files on disk.
        if audio_files is None and audio_duration > 0:
            audio_files = dump_take_audio(
                session["id"], take_id,
                audio, audio, config.AUDIO_SAMPLE_RATE)
        # Now persist the take using the same id we used for the audio
        # directory, so the file path and the take record stay in sync.
        take = self.stats_store.record_take(
            phrase, raw_score,
            audio_duration_sec=audio_duration,
            transcript=transcript,
            error_words=error_words,
            phoneme_scores=phoneme_scores,
            grade=getattr(result, "grade", "") if result else "",
            grade_value=getattr(result, "grade_value", -1.0) if result else -1.0,
            attempts=self.session._current_attempts.__len__() if getattr(
                self.session, "_current_attempts", None) else 1,
            audio_files=audio_files,
            take_id=take_id,
        )
        if take is None:
            return
        # Write the meta file if audio was saved (metadata_only mode still
        # benefits from a per-take meta.json in the session dir).
        try:
            dump_take_meta(take, session["id"])
        except Exception:  # noqa: BLE001
            logging.exception("Failed to dump take meta:")

    def _take_audio_session(self) -> Dict[str, str]:
        """The session id that owns this run's per-take audio directories.

        Normally the open stats session, so a take's wav and its stats record
        share one directory. With history disabled there is no session to key
        the path off, so a per-run id is generated instead - the recordings
        are still saved and replayable, and the stats file is left untouched.
        """
        if config.STATS_ENABLED:
            session = self.stats_store.open_session_if_needed()
            if session is not None:
                return {"id": session["id"]}
        if self._audio_session_id is None:
            self._audio_session_id = "live-" + time.strftime("%Y%m%d-%H%M%S")
        return {"id": self._audio_session_id}

    def _save_attempt_audio(self, audio_raw: Optional[np.ndarray],
                            audio_normalized: Optional[np.ndarray]) -> None:
        """Save one take's recording so the attempt list can replay it later.

        Runs on the analysis worker, before show_feedback is dispatched, so
        _last_take_audio is already set when the view builds the history row.
        Every take goes through here - scored and unscored alike - so a
        "none" engine still keeps its recordings. A write failure is logged
        and swallowed: persistence must never break the practice loop.
        """
        self._last_take_audio = None
        if config.HISTORY_MODE != "with_audio":
            return
        if audio_normalized is None or audio_normalized.size == 0:
            return
        try:
            session = self._take_audio_session()
            take_id = f"t-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
            written = dump_take_audio(session["id"], take_id,
                                      audio_raw, audio_normalized,
                                      config.AUDIO_SAMPLE_RATE)
            if not written:
                return
            self._last_take_audio = {"session_id": session["id"],
                                     "take_id": take_id,
                                     "audio_files": written}
            logging.info("Saved attempt recording %s/%s",
                         session["id"], take_id)
        except Exception:
            logging.exception("Failed to save the attempt recording:")

    def on_history_entry(self, record: dict):
        """Append an entry to the attempt history and re-render the list.

        ``record`` comes from the view with a ``kind`` of "attempt",
        "unscored" or "error". Trend derivation and the bounded storage live
        in SessionState (mimora/session.py); the view then rebuilds every
        row from the returned full list.
        """
        # Attach the recording written for this take, so the row can offer a
        # replay button. Only real takes have audio: error rows never record,
        # and a take without a saved recording (HISTORY_MODE off) has no ids.
        if record.get("kind") in ("attempt", "unscored") and self._last_take_audio:
            record["audio"] = dict(self._last_take_audio)
        self.view.render_history(self.session.add_history_entry(record))

    def _play_reference_at(self, speed: float):
        """Play the current reference audio at *speed* (1.0 = normal)."""
        if self.reference_audio is None or self.reference_audio.size == 0:
            return
        # Slowing is done by lowering the effective sample rate: playing e.g.
        # 24 kHz audio at 12 kHz (0.5×) makes it twice as long. This is the
        # simple resampling approach - it also shifts the pitch down, no extra
        # deps - and it works with any backend rate (24 kHz Kokoro, 44.1 kHz
        # Supertonic), so slow replay needs no backend-specific code.
        effective_sr = int(self.tts_mgr.sample_rate * speed)
        status = i18n.tr("Playing reference...") if speed == 1.0 \
            else i18n.tr("Playing reference ({speed}×)...", speed=f"{speed:g}")
        self.playback.play_async(self.reference_audio, effective_sr, status)

    def play_user_recording(self):
        if self.last_user_audio is None or self.last_user_audio.size == 0:
            return
        self.playback.play_async(self.last_user_audio, config.AUDIO_SAMPLE_RATE,
                                 i18n.tr("Playing your recording..."))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def _shutdown_runtime(self):
        """Release the external resources before the process goes away.

        Shared by quit_app and restart_app. Stops playback, ends a recording
        in progress (so the capture thread exits its loop and closes the input
        stream), and kills the local LLM server subprocess - the hard exit
        (lifecycle.hard_exit) does NOT terminate children, so without this
        the llama-server process would leak, keep holding VRAM, and (on restart)
        still occupy the server port the new process needs.

        Every step is best-effort and none may prevent the exit. What this
        guards against is specific: an exception here must not propagate into
        the Tk callback that called it, because that skips hard_exit() while
        _closing is already True - the window then stays open and every further
        attempt to close it returns on the first line. One audio device raising
        on the way out makes the app unquittable, and restart_app never reaches
        spawn_replacement(), turning a settings restart into a hang. Leaking a
        subprocess is the lesser failure, and the log says which step leaked it.
        """
        self.shutdown_event.set()
        for name, action in (("playback", self.playback.stop),
                             ("recorder", self.recorder.stop),
                             ("record thread", self.recorder.join),
                             ("LLM server", self.llm_server.shutdown)):
            try:
                action()
            except Exception:  # noqa: BLE001 - see the docstring
                logging.exception("Shutting down %s failed; continuing:", name)
        # Persist the end of the current practice session. Best-effort: a
        # missing stats store (test, STATS_ENABLED=false) is a no-op.
        try:
            self.stats_store.end_current_session()
        except Exception:  # noqa: BLE001
            logging.exception("Closing stats session failed; continuing:")

    def quit_app(self):
        if self._closing:
            return  # Escape and the window-close button can both land here
        self._closing = True
        logging.info("Shutting down Mimora...")
        try:
            self._shutdown_runtime()
        finally:
            # finally, not a plain call: _shutdown_runtime already swallows the
            # failure of any single step, so reaching here with an exception
            # means something unforeseen - and that is exactly when the process
            # must still end rather than leave a half-shut-down window nobody
            # can close.
            logging.info("quit_app: cleanup done, hard-exiting now.")
            lifecycle.hard_exit()

    def restart_app(self):
        """Relaunch the app in a new process (settings-window restart).

        Applies restart-only settings without the user quitting and starting
        the app by hand: the same cleanup as quit_app runs first (crucially
        freeing the LLM server port for the new process), then a detached
        replacement process is spawned (lifecycle.spawn_replacement, which
        logs and swallows a relaunch failure so this process still exits
        cleanly) and this one hard-exits.
        """
        if self._closing:
            return
        self._closing = True
        logging.info("Restarting Mimora to apply changed settings...")
        try:
            self._shutdown_runtime()
        finally:
            # Same reason as in quit_app, with one more consequence: without
            # the finally an exception during cleanup skipped the relaunch as
            # well, so a settings restart left the user with a window that
            # would neither restart nor close.
            lifecycle.spawn_replacement()
            logging.info("restart_app: cleanup done, hard-exiting now.")
            lifecycle.hard_exit()

    def run(self):
        # No code after mainloop(): both exit paths (quit_app, restart_app)
        # end in lifecycle.hard_exit(), so control never returns here. Log
        # handlers are deliberately left to the OS - closing them during
        # shutdown produced 'I/O operation on closed file' noise from daemon
        # threads still logging.
        self.root.mainloop()


def _date_stamp() -> str:
    """Compact date stamp for export file names (2026-10-26)."""
    return datetime.now().strftime("%Y-%m-%d")


def _log_spacy_model_location() -> None:
    """Record where Kokoro's grapheme-to-phoneme model is going to come from.

    The same idea as ``llm_server_ctl.log_compute_devices()`` one layer down,
    and for the same reason: what it guards against is silent. ``config``
    activates the sidecar while it is being imported, which is before
    ``setup_logging`` above installs the file handler, so that line never
    reaches logs/main.log and a bug report would say nothing at all about a
    model the application cannot start without.

    Diagnostic only, and deliberately so - it neither downloads nor refuses.
    Called after ``ensure_ready()``, because a first run may have just fetched
    the model and put its directory on sys.path, which would make an earlier
    answer stale rather than merely early.

    Only asked for Kokoro: Spanish synthesises through Supertonic, which never
    touches spaCy, and a warning about a model that machine will never load
    would be noise on every start.
    """
    if config.TTS_BACKEND != "kokoro":
        return
    located = spacy_model_fetch.resolved_location()
    if located is not None:
        logging.info("spaCy pipeline %s resolves from %s.",
                     spacy_model_fetch.MODEL.name, located)
        return
    # Reachable and worth shouting about: misaki reacts to a missing model by
    # shelling out to pip, which an installed tool's environment does not have.
    # The loader thread then dies on the SystemExit that follows - visibly now
    # (load_components catches BaseException), but a line here says which model
    # and how to fix it before anything has gone wrong.
    logging.warning(
        "spaCy pipeline %s was not found. Kokoro's phonemiser will try to "
        "install it itself, which fails in an environment without pip. Run "
        "`python -m mimora.spacy_model_fetch` to put it in place.",
        spacy_model_fetch.MODEL.name)


def run(append_log: bool = False):
    """Start the application: logging, the first-run window, then the GUI.

    Called by mimora/cli.py once the arguments are parsed, which is also why
    there is no ``if __name__ == "__main__"`` block here: this module is the
    application, not an entry point, and it is imported by three different
    launch forms (console script, ``python -m mimora``, ``python main.py``).

    ``append_log`` carries the --append-log switch through to the logging
    setup; the default is what a plain launch does (a fresh main.log). It is
    a parameter rather than something read from config because logging is
    configured from the command line, which config knows nothing about.

    Nothing follows the GUI's own run(): both exit paths end in
    lifecycle.hard_exit(), so control never returns.
    """
    # Root logging (console + logs/main.log). Deliberately here rather than at
    # module level: it must come after the heavy imports above (see
    # bootstrap.setup_logging for the force=True rationale), and importing a
    # module should not reconfigure the whole process's logging as a side
    # effect. Nothing logs between the import and this call.
    bootstrap.setup_logging(config.LOG_FILE, append=append_log)

    # First run on a machine where nothing has been downloaded: ask, fetch, and
    # only then build the app. It happens BEFORE the constructor because a
    # refusal of the optional level changes what the constructor builds - it
    # picks LLMManager or SourceTextPhraseProvider from config.LLM_BACKEND, and
    # ensure_ready has to have rewritten that first. Returns immediately when
    # nothing is missing, which is every run after the first.
    first_run_outcome = first_run_window.ensure_ready()

    if first_run_outcome == first_run_window.CANCELLED:
        # "cancelled", not "declined": this branch is the user leaving - the
        # window's Quit button, its close box, or Escape, including mid-
        # download. Declining the optional level is the opposite outcome, it
        # starts the app with llm_backend "off", and ensure_ready logs that
        # itself. The two must not share this line: it would make a log read as
        # a refusal when the download was merely interrupted.
        logging.info("First-run setup cancelled; exiting before startup.")
        # hard_exit rather than SystemExit, for the same reason quit_app uses
        # it: the download runs on a daemon thread that may be mid-transfer,
        # and normal interpreter finalization kills such a thread at whatever
        # line it happens to be on, which surfaces as an "Exception ignored in"
        # traceback after the user has already chosen to leave. Nothing is lost
        # by skipping cleanup - the fetchers are built to survive being killed
        # (staged install, .incomplete files), and no model or CUDA context has
        # been loaded yet at this point.
        lifecycle.hard_exit()

    if first_run_outcome == first_run_window.RESTART:
        # The first-run window ran, so the machine no longer matches what
        # config read while it was being imported: models arrived, a refusal
        # rewrote a setting, the hardware probe wrote its file. Above all, the
        # offline gate was decided before the download and would keep this
        # session on the network for models that are now on disk (see the tail
        # of ensure_ready). Starting over is what applies all of it, and it is
        # cheap exactly here: unlike restart_app there is no runtime to shut
        # down first (no models, no recorder, no LLM subprocess, no Tk root of
        # the app), so the pair below is the whole procedure. It cannot loop:
        # the next process finds nothing missing and returns before the window.
        logging.info("Restarting to apply what the first-run window changed.")
        lifecycle.spawn_replacement()
        lifecycle.hard_exit()

    # After the first run and its possible restart, so the device this reports
    # on is the one this process will actually use, and so a machine that
    # relaunches does not say it twice. config.DEVICE is passed in rather than
    # read there: detect_hardware must not import config (it rewrites the file
    # config reads at import).
    detect_hardware.warn_if_gpu_unused(config.DEVICE)
    _log_spacy_model_location()

    # Select the UI language before any widget is built. The settings window
    # marks ui_language as restart-only, so this is the one place it is read.
    i18n.set_language(config.UI_LANGUAGE)

    # Named gui, not app: inside this module "app" is the module itself.
    gui = PronunciationTrainerGUI()
    # The main window exists now and takes over from the splash that
    # cli.main() started.
    splash.close()
    gui.run()
