# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Web runtime: wires Mimora's GUI-agnostic modules for the HTTP server.

This is the composition root for the web companion. It mirrors what
``app.py`` builds in ``PronunciationTrainerGUI.__init__`` and
``load_components``, minus Tk: the same TTS manager, the same engine
dispatcher, the same LLM wiring, the same stats/review stores, the same
phrase-selection logic (spaced-repetition review first, then practice or
LLM). The browser records audio; the backend scores it.

Nothing here is a copy of app logic that can be reused instead. Where app.py
has a pure helper (phrase selection, review advance, fluency/shadowing blend,
take persistence), this module calls into the same module-level functions
rather than re-implementing them. The one thing it cannot reuse is ``app.py``
itself, because that module builds a Tk window at import time.

Threading: FastAPI runs handlers on its own thread pool, so every public
method here is safe to call from any thread. Model loading and phrase
generation are serialized with an internal lock where they must be (one
recognizer, one TTS backend, one LLM connection - exactly the GPU/CPU
contention the desktop app guards with its sequential phases).
"""

from __future__ import annotations

import base64
import datetime
import logging
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from mimora import config
from mimora import engine
from mimora import fluency
from mimora import phrase_source as phrase_source_mod
from mimora import prosody
from mimora import shadowing
from mimora import tts as tts_mod
from mimora.llm import LLMManager, phrase_sampling
from mimora.llm_server_ctl import LLMServerController
from mimora.persistence import recorder_store
from mimora.persistence import stats as stats_mod
from mimora.review import ReviewStore
from mimora.settings_ctl import LIVE_CONFIG_ATTRS
from mimora.translator import TranslatorManager

logger = logging.getLogger(__name__)


# -- range filter (mirrors ui_weakness._filter_takes_by_range) ----------------
#
# The weak/strength aggregations are fed a sliced take list. The desktop view
# owns that slicing in ui_weakness; the web runtime replicates it here so the
# HTTP layer stays Tk-free (importing ui_weakness would pull ttkbootstrap).
# Update both if the range semantics change.
WEAKNESS_RANGE_OPTIONS = (
    ("last_7d", "Last 7 days", {"days": 7}),
    ("last_14d", "Last 14 days", {"days": 14}),
    ("last_30d", "Last 30 days", {"days": 30}),
    ("last_60d", "Last 60 days", {"days": 60}),
    ("last_30", "Last 30 takes", {"count": 30}),
    ("last_100", "Last 100 takes", {"count": 100}),
    ("last_300", "Last 300 takes", {"count": 300}),
)


def _phoneme_examples_for_active_language() -> Dict[str, str]:
    """Resolve example words for the active practice language (lazy import).

    The desktop's ``phoneme_examples`` module is pure data and safe to import,
    but pulling it at module load adds a config read; resolve on first settings
    call instead. ``example_for`` already defaults to the active practice
    language, so a per-symbol lookup reproduces the desktop badge behaviour
    (stress/length/tone marks stripped, unknown symbol -> omitted).
    """
    try:
        from mimora import phoneme_examples as _pe
    except Exception:  # noqa: BLE001 - never fail the settings read
        return {}
    out: Dict[str, str] = {}
    # The examples table's own keys are the canonical symbols; resolve each so
    # the web badge can look up the same word the desktop would show.
    for symbol in _pe.PHONEME_EXAMPLES_BY_LANGUAGE.get(
            config.PRACTICE_LANGUAGE, {}):
        word = _pe.example_for(symbol)
        if word:
            out[symbol] = word
    return out


# Populated lazily by _phoneme_examples_for_active_language (per request is
# cheap enough, but a cached dict keeps repeated settings reads stable).
_PHONEME_EXAMPLES: Dict[str, str] = {}


def _filter_takes_by_range(raw_takes: List[Any], range_key: str) -> List[Any]:
    """Slice the raw ``[(session, take), ...]`` list to the chosen range.

    ``days`` windows are applied by the take's ISO timestamp (newer than
    ``now - days``); ``count`` windows take the last N takes. An unknown or
    empty range key returns the list unchanged.
    """
    spec = next((s for k, _l, s in WEAKNESS_RANGE_OPTIONS if k == range_key), None)
    if not spec:
        return raw_takes
    days = spec.get("days")
    if days is not None:
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=days)
        out = []
        for item in raw_takes:
            take = item[1] if isinstance(item, tuple) and len(item) == 2 else item
            ts = take.get("timestamp")
            try:
                if ts:
                    parsed = datetime.datetime.fromisoformat(ts)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(
                            tzinfo=datetime.datetime.now().astimezone().tzinfo)
                    if parsed >= cutoff:
                        out.append(item)
            except (TypeError, ValueError):
                continue
        return out
    count = spec.get("count")
    if count is not None:
        return raw_takes[-count:]
    return raw_takes


# -- dashboard helpers (mirror ui_stats, kept Tk-free) ------------------------

def _fmt_seconds(seconds: int) -> str:
    """1440 → '2h 24m'; 60 → '1m'; 0 → '0m'."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return f"{hours}h {rem}m"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h"


def _avg_score_for_session(session: Dict[str, Any]) -> float:
    totals = session.get("totals") or {}
    if "avg_best_score" in totals:
        return float(totals["avg_best_score"] or 0.0)
    scores = [float(t.get("score") or 0) for t in session.get("takes") or []]
    return sum(scores) / len(scores) if scores else 0.0


def _session_date_key(session: Dict[str, Any]) -> str:
    ts = session.get("started_at") or ""
    if not ts:
        return ""
    try:
        return datetime.datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except ValueError:
        return ts[:10]


def _compute_streak(sessions: List[Dict], target_minutes: int) -> int:
    """Consecutive days (ending today or yesterday) that hit the daily target."""
    if not sessions or target_minutes <= 0:
        return 0
    day_seconds: Dict[str, int] = defaultdict(int)
    for s in sessions:
        try:
            day = datetime.datetime.fromisoformat(
                s.get("started_at") or "").strftime("%Y-%m-%d")
        except ValueError:
            continue
        day_seconds[day] += int(s.get("duration_sec") or 0)
    target_sec = target_minutes * 60
    streak = 0
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor = datetime.datetime.now().date()
    if today not in day_seconds and (cursor - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d") in day_seconds:
        cursor -= datetime.timedelta(days=1)
    while True:
        day_key = cursor.strftime("%Y-%m-%d")
        if day_seconds.get(day_key, 0) >= target_sec:
            streak += 1
            cursor -= datetime.timedelta(days=1)
        else:
            break
    return streak


# Keys the desktop settings window marks as restart-required (Field(restart=True)
# in settings_window.build_sections). The web runtime cannot import that module
# (ttkbootstrap), so the set is repeated here; update both if the marks change.
_RESTART_REQUIRED_KEYS = frozenset({
    "ui_language", "practice_language", "accent", "engine",
    "phoneme_good_mode", "color_theme", "llm_backend", "lm_studio_host",
    "external_model_path", "external_n_ctx", "warm_up",
})


def dictation_scoring_alignment(details: Dict[str, Any]) -> str:
    """A compact alignment summary, mirroring ``alignment_summary``."""
    status = details.get("status") or []
    total = len(details.get("reference_words") or [])
    missed = sum(1 for w in status if w["state"] == "missed")
    wrong = sum(1 for w in status if w["state"] == "wrong")
    extra = len(details.get("extra_words") or [])
    parts = [f"{total} words"]
    if missed:
        parts.append(f"{missed} missed")
    if wrong:
        parts.append(f"{wrong} misspelled")
    if extra:
        parts.append(f"{extra} extra")
    return " · ".join(parts)


class WebRuntime:
    """Composition root for the web API.

    One instance is created at server startup (module-level ``get_runtime``
    holds it, so the FastAPI endpoints and the lifespan both reach the same
    object). It lazily loads the heavy pieces on first use, mirroring the
    desktop app's lazy model loading, and exposes plain-data results that
    the HTTP layer serializes as JSON.
    """

    def __init__(self) -> None:
        self._init_lock = threading.Lock()
        self._ready = False
        self._load_error: Optional[str] = None

        # Mirrors PronunciationTrainerGUI.__init__.
        self.tts_mgr = tts_mod.TTSManager()
        self.llm_mgr = LLMManager()
        self.llm_server = LLMServerController()
        self.translator_mgr = TranslatorManager()
        self.stats_store = stats_mod.StatsStore(
            config.stats_path(),
            idle_timeout_sec=config.IDLE_TIMEOUT,
            daily_backup_dir=config.DAILY_BACKUP_DIR,
        )
        self.review_store = ReviewStore()
        self.review_enabled = config.REVIEW_ENABLED
        self.source_text_provider = phrase_source_mod.SourceTextPhraseProvider(
            random_order=config.PHRASE_SOURCE_RANDOM
        )

        # Current practice state (per connection, but a single practice loop
        # is the useful unit - the desktop app is single-window too).
        self.current_phrase: str = ""
        self.reference_audio: Optional[np.ndarray] = None
        self.reference_sr: int = config.AUDIO_SAMPLE_RATE
        self.current_voice: str = ""
        self.current_translation_language: str = config.TRANSLATION_LANGUAGE
        self.last_user_audio: Optional[np.ndarray] = None
        self._last_take_audio: Optional[Dict[str, Any]] = None

        # Active engine sessions (dictation / drill). One at a time, like the
        # desktop controller; a start replaces any previous session.
        self._dictation_engine: Optional[Any] = None
        self._drill_engine: Optional[Any] = None

        self._generation_lock = threading.Lock()

    # -- startup -------------------------------------------------------------

    def ready(self) -> bool:
        """True once the heavy models have loaded without error."""
        return self._ready

    def load_error(self) -> Optional[str]:
        return self._load_error

    def initialize(self) -> None:
        """Load TTS + engine (+ translator/LLM if configured).

        Mirrors ``app.load_components``. Safe to call more than once - the
        second call is a no-op. Runs synchronously here; the HTTP layer calls
        it from a startup task so the first request does not pay for it.
        """
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            try:
                logger.info("Web runtime: loading TTS model...")
                self.tts_mgr.load_model()
                logger.info("Web runtime: TTS model loaded.")

                if engine.name() != "none":
                    logger.info("Web runtime: loading pronunciation engine (%s)...",
                                engine.name())
                engine.configure()
                engine.load_models()
                logger.info("Web runtime: pronunciation engine ready (%s).",
                            engine.name())

                if config.TRANSLATION_LANGUAGE:
                    logger.info("Web runtime: loading translator (NLLB)...")
                    self.translator_mgr.load_model()

                self._init_llm()

                if config.WARM_UP:
                    logger.info("Web runtime: warming up models...")
                    self.tts_mgr.warm_up()
                    engine.warm_up()
                    if config.TRANSLATION_LANGUAGE:
                        self.translator_mgr.warm_up()

                self._load_review_store()
                self._ready = True
                logger.info("Web runtime: initialization complete.")
            except BaseException as exc:  # noqa: BLE001 - report, do not crash
                # Same rationale as app.py: SystemExit from a spacy download
                # must not kill the server thread silently.
                logger.exception("Web runtime initialization failed:")
                self._load_error = f"{type(exc).__name__}: {exc}"
                raise

    def _init_llm(self) -> None:
        """Configure the LLM backend the same way app.py does."""
        backend = config.LLM_BACKEND
        if backend == "off":
            logger.info("Web runtime: LLM is off - phrases from practice text.")
            return
        if backend == "llama-server":
            model_name = str(config.EXTERNAL_MODEL_PATH).rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            logger.info("Web runtime: starting llama-server with %s...", model_name)
            if not self.llm_server.start(self.llm_mgr):
                last = getattr(self.llm_server, "last_error", None)
                raise RuntimeError(f"LLM server failed to start: {last or 'unknown reason'}")
            logger.info("Web runtime: LLM server is ready.")
            self.llm_mgr.adopted_server = self.llm_server.adopted
            temperature, top_p = phrase_sampling(self.llm_server.adopted)
            logger.info("Web runtime: phrase sampling temperature=%s top_p=%s (%s server).",
                        temperature, top_p,
                        "adopted" if self.llm_server.adopted else "own")
            if self.llm_server.adopted:
                logger.info("Web runtime: using llama-server already running on %s:%s.",
                            config.LLM_SERVER_HOST, config.LLM_SERVER_PORT)
        else:  # lm-studio
            self.llm_mgr.init_client()
            if not self.llm_mgr.check_connection():
                logger.warning("Web runtime: LM Studio is offline - phrases unavailable.")

    def _load_review_store(self) -> None:
        if not self.review_enabled:
            return
        try:
            self.review_store.load()
        except Exception:  # noqa: BLE001 - never break startup
            logger.exception("Could not open review store; review off:")
            self.review_enabled = False

    def shutdown(self) -> None:
        """Stop the LLM subprocess (adopted servers are left running)."""
        try:
            self.llm_server.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("Error shutting down LLM server:")

    # -- config / settings surface ------------------------------------------

    @staticmethod
    def settings() -> Dict[str, Any]:
        """The settings the frontend needs to render its controls."""
        if not _PHONEME_EXAMPLES:
            _PHONEME_EXAMPLES.update(_phoneme_examples_for_active_language())
        return {
            "engine": config.ENGINE,
            "engine_choices": list(config.ENGINE_CHOICES),
            "practice_language": config.PRACTICE_LANGUAGE,
            "accent": config.ACCENT,
            "ui_language": config.UI_LANGUAGE,
            "ui_language_choices": list(config.ui_language_choices()),
            "voice": config.TTS_VOICE,
            "voices": list(config.TTS_VOICES),
            "language_choices": list(config.language_choices()),
            "accent_choices": list(config.accent_choices()),
            "reference_speed": config.REFERENCE_SPEED,
            "fluency_enabled": config.FLUENCY_ENABLED,
            "shadowing_enabled": config.SHADOWING_ENABLED,
            "review_enabled": config.REVIEW_ENABLED,
            "user_name": config.USER_NAME,
            "llm_backend": config.LLM_BACKEND,
            "auto_play_phrase": config.AUTO_PLAY_PHRASE,
            "translation_language": config.TRANSLATION_LANGUAGE,
            "translation_targets": list(config.translation_targets()),
            "score_threshold": config.PRONUNCIATION_SCORE_THRESHOLD,
            "warm_up": config.WARM_UP,
            "history_mode": config.HISTORY_MODE,
            "save_recordings": config.SAVE_RECORDINGS,
            "playback_own_recording": config.PLAYBACK_OWN_RECORDING,
            "stats_enabled": config.STATS_ENABLED,
            "show_prosody": config.SHOW_PROSODY,
            "pass_gating_enabled": config.PASS_GATING_ENABLED,
            # Extended surface (full-feature round).
            "phrase_length": config.PHRASE_LENGTH,
            "phrase_length_choices": list(config.PHRASE_LENGTH_CHOICES),
            "phrase_source": config.PHRASE_SOURCE,
            "phrase_source_choices": ("llm", "practice"),
            "max_record_seconds": config.MAX_RECORD_SECONDS,
            "silence_timeout": config.SILENCE_TIMEOUT,
            "silence_threshold": config.SILENCE_THRESHOLD,
            "random_voice": config.RANDOM_VOICE,
            "show_face": config.SHOW_FACE,
            "practice_text_collapsed": config.PRACTICE_TEXT_COLLAPSED,
            "native_language": config.NATIVE_LANGUAGE,
            "daily_target_minutes": config.DAILY_TARGET_MINUTES,
            "fluency_weight": config.FLUENCY_WEIGHT,
            "fluency_reference_wpm": config.FLUENCY_REFERENCE_WPM,
            "shadowing_weight": config.SHADOWING_WEIGHT,
            "drill_pass_threshold": config.DRILL_PASS_THRESHOLD,
            "drill_max_attempts": config.DRILL_MAX_ATTEMPTS,
            "dictation_pass_threshold": config.DICTATION_PASS_THRESHOLD,
            "dictation_max_attempts": config.DICTATION_MAX_ATTEMPTS,
            "dictation_random_order": config.DICTATION_RANDOM_ORDER,
            "dictation_review_enabled": config.DICTATION_REVIEW_ENABLED,
            "restart_required_keys": sorted(_RESTART_REQUIRED_KEYS),
            # Phoneme example words (WORK ON badges click-to-hear on desktop).
            "phoneme_examples": dict(_PHONEME_EXAMPLES),
        }

    def save_setting(self, key: str, value: Any) -> Dict[str, Any]:
        """Persist one setting, applying it live when the desktop app would.

        Mirrors ``SettingsGlue.persist_and_apply_live``: the value is written
        to settings.json and, for table-driven keys, the matching ``config``
        attribute is updated immediately. Restart-only keys are persisted and
        reported as such. Returns ``{key, value, restart_required}``.
        """
        live = LIVE_CONFIG_ATTRS.get(key)
        try:
            if not config.save_user_setting(key, value):
                raise RuntimeError("Could not save setting to settings.json")
        except Exception as exc:  # noqa: BLE001 - surface as a request error
            logger.exception("Failed to save setting %s:", key)
            raise RuntimeError(f"Could not save setting: {exc}") from exc
        if live is not None:
            attr, cast = live
            try:
                setattr(config, attr, cast(value))
                logger.info("Web runtime: applied live setting config.%s = %r.",
                            attr, value)
            except Exception:  # noqa: BLE001 - a bad cast must not lose the save
                logger.exception("Failed to apply live setting config.%s:", attr)
        return {
            "key": key,
            "value": value,
            "restart_required": key in _RESTART_REQUIRED_KEYS,
        }

    def source_text(self) -> str:
        """The current practice text (file contents or fallback)."""
        path = Path(config.PRACTICE_TEXT_FILE)
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001 - fall through to the shipped default
            logger.exception("Could not read practice text file:")
        fallback = Path(config.PRACTICE_TEXT_FALLBACK)
        try:
            if fallback.exists():
                return fallback.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            logger.exception("Could not read fallback practice text:")
        return ""

    def save_source_text(self, text: str) -> Dict[str, Any]:
        """Persist the practice text to a user-owned file and remember it.

        The desktop app never writes to the shipped practice text: the user
        picks a file in the settings window, which persists its absolute path
        into settings.json. The web companion must not write into the package
        either (an installed wheel's directory is read-only, and in a checkout
        the shipped text is git-tracked). So a save goes to a user file under
        the config dir when the current target is the shipped default, then
        settings.json is pointed at it so the choice survives a restart.
        """
        target = Path(config.PRACTICE_TEXT_FILE)
        shipped = Path(config.SHIPPED_DIR)
        try:
            is_shipped = target.resolve().is_relative_to(shipped.resolve())
        except (OSError, ValueError):
            is_shipped = False
        if is_shipped:
            target = Path(config.CONFIG_DIR) / "practice_text.txt"
        try:
            if not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save practice text:")
            raise RuntimeError(f"Could not save practice text: {exc}") from exc
        # Remember the file for the next launch and keep the running process
        # reading it (the desktop does the same at _load_practice_file).
        saved = str(target)
        config.save_user_setting("practice_text_file", saved)
        config.PRACTICE_TEXT_FILE = saved
        return {"path": saved, "length": len(text)}

    # -- phrase generation ---------------------------------------------------

    def generate_phrase(self, source_text: str, length: str = "full",
                        phrase_source: Optional[str] = None) -> str:
        """Pick the next phrase (review first, then practice/LLM).

        Mirrors ``app._select_phrase``. ``phrase_source`` defaults to the
        current configured mode ("practice" or "llm").
        """
        source = phrase_source or self._phrase_source_mode()
        with self._generation_lock:
            due = self._due_review_phrases(source_text, source)
            if due:
                phrase = due[0]
                try:
                    self.review_store.record_phrase(phrase, source=source)
                except Exception:  # noqa: BLE001 - review is best-effort
                    logger.exception("Failed to touch review phrase:")
                logger.info("Web runtime: review phrase %r", phrase)
                return phrase

            if source == "practice":
                phrase = self.source_text_provider.generate_phrase(
                    source_text, length=length)
            else:
                phrase = self.llm_mgr.generate_phrase(source_text, length=length)
            if phrase and self.review_enabled:
                try:
                    self.review_store.record_phrase(phrase, source=source)
                except Exception:  # noqa: BLE001 - review is best-effort
                    logger.exception("Failed to record review phrase:")
            return phrase

    def _phrase_source_mode(self) -> str:
        return config.PHRASE_SOURCE

    def _due_review_phrases(self, source_text: str, phrase_source: str) -> List[str]:
        if not self.review_enabled:
            return []
        try:
            due = self.review_store.due_phrases(source=phrase_source)
        except Exception:  # noqa: BLE001 - best-effort
            logger.exception("Review lookup failed; skipping review:")
            return []
        if phrase_source == "practice":
            current = set(phrase_source_mod.split_sentences(source_text))
            due = [p for p in due if p in current]
        return due

    def _record_review_result(self, phrase: str, passed: bool) -> None:
        if not self.review_enabled:
            return
        try:
            self.review_store.record_result(phrase, passed)
        except Exception:  # noqa: BLE001 - never break the practice loop
            logger.exception("Failed to record review result:")

    # -- reference synthesis -------------------------------------------------

    def synthesize_reference(self, phrase: str,
                             voice: Optional[str] = None) -> Tuple[np.ndarray, int]:
        """Synthesize the reference audio for *phrase* and cache it.

        Returns ``(waveform, sample_rate)``. The cached reference is what the
        scoring endpoint uses, and what the frontend streams back for
        playback - one synthesis, reused, exactly like the desktop app.
        """
        audio = self.tts_mgr.synthesize(phrase, voice=voice)
        self.reference_audio = audio
        self.reference_sr = self.tts_mgr.sample_rate
        self.current_phrase = phrase
        self.current_voice = voice or config.TTS_VOICE
        return audio, self.reference_sr

    def synthesize_standalone(self, phrase: str,
                              voice: Optional[str] = None) -> Tuple[np.ndarray, int]:
        """Synthesize *phrase* WITHOUT disturbing the practice-loop cache.

        Used by the dictation / drill panels, whose phrases are not the
        practice phrase: the cached ``reference_audio`` must keep matching
        ``current_phrase`` so a later ``/api/score`` scores the right audio.
        """
        audio = self.tts_mgr.synthesize(phrase, voice=voice or config.TTS_VOICE)
        return audio, self.tts_mgr.sample_rate

    def translate(self, phrase: str) -> str:
        """Best-effort translation of *phrase* into the configured language."""
        lang = self.current_translation_language
        if not lang:
            return ""
        try:
            if not self.translator_mgr.is_loaded():
                self.translator_mgr.load_model()
            return self.translator_mgr.translate(phrase, lang)
        except Exception:  # noqa: BLE001 - translation is best-effort
            logger.exception("Translation failed:")
            return ""

    # -- scoring -------------------------------------------------------------

    def score_recording(self, user_audio: np.ndarray, user_sr: int,
                        phrase: str) -> Dict[str, Any]:
        """Score one take and persist it.

        Mirrors ``app.analyze_recording`` + ``on_take_scored`` +
        ``_persist_take``. Returns a plain-data dict the HTTP layer serializes
        directly (all numpy arrays converted to lists).
        """
        if user_audio is None or user_audio.size == 0:
            raise ValueError("Empty recording")

        reference_audio = self.reference_audio
        if reference_audio is None or reference_audio.size == 0:
            # Re-synthesize so a phrase never scores against a stale cache.
            reference_audio, _ = self.synthesize_reference(phrase, self.current_voice)

        from pronunciation.common.audio import prepare_waveform

        user_wav = prepare_waveform(user_audio, user_sr)
        # The engine needs 16 kHz user audio, matching app.py.
        user_for_engine = self._to_16k(user_audio, user_sr)

        result = engine.analyze(
            user_audio=user_for_engine,
            expected_text=phrase,
            reference_audio=reference_audio,
            user_sr=config.AUDIO_SAMPLE_RATE,
            reference_sr=self.reference_sr,
            voice=self.current_voice,
        )

        # Prosody (engine-agnostic layer), same as app.py.
        if config.SHOW_PROSODY:
            try:
                result.prosody = prosody.compute_prosody(
                    user_for_engine, config.AUDIO_SAMPLE_RATE,
                    reference_audio, self.reference_sr)
            except Exception:  # noqa: BLE001
                logger.exception("Prosody computation failed:")
                result.prosody = {}

        # Fluency / shadowing axes, same as app.py.
        if config.FLUENCY_ENABLED:
            self._apply_fluency(result, user_for_engine, config.AUDIO_SAMPLE_RATE)
        if config.SHADOWING_ENABLED:
            self._apply_shadowing(result, user_for_engine, config.AUDIO_SAMPLE_RATE)

        self.last_user_audio = user_audio
        self._record_review_result(phrase, bool(getattr(result, "passed", False)))

        # Persist (best-effort, mirroring _persist_take).
        persisted = self._persist_take(phrase, result, user_wav,
                                       audio_duration=user_wav.size / config.AUDIO_SAMPLE_RATE)

        return self._result_to_dict(result, phrase, persisted)

    @staticmethod
    def _to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
        """Resample to 16 kHz mono float32 (the engine's native rate)."""
        if sr == config.AUDIO_SAMPLE_RATE:
            return np.ascontiguousarray(audio, dtype=np.float32)
        try:
            import librosa

            return librosa.resample(audio.astype(np.float32),
                                    orig_sr=sr, target_sr=config.AUDIO_SAMPLE_RATE)
        except Exception:  # noqa: BLE001 - fall back to naive resample
            logger.exception("librosa resample failed; using naive resample:")
            n = int(round(audio.shape[0] * config.AUDIO_SAMPLE_RATE / sr))
            return np.interp(
                np.linspace(0, audio.shape[0] - 1, n),
                np.arange(audio.shape[0]), audio.astype(np.float32)
            ).astype(np.float32)

    def _apply_fluency(self, result, audio: np.ndarray, user_sr: int) -> None:
        if self.reference_audio is None:
            return
        try:
            transcript = getattr(result, "transcription", "") or ""
            duration = audio.size / user_sr
            f = fluency.compute_fluency(
                user_audio=audio, user_sr=user_sr,
                reference_audio=self.reference_audio, reference_sr=self.reference_sr,
                transcript=transcript, audio_duration_sec=duration,
                reference_wpm=config.FLUENCY_REFERENCE_WPM,
            )
            result.fluency_axis = f.get("score", -1.0)
            result.speed_label = f.get("speed_label", "")
            result.wpm = f.get("wpm", 0.0)
            blended = (result.score * (1.0 - config.FLUENCY_WEIGHT / 100.0)
                       + f.get("score", 0.0) * (config.FLUENCY_WEIGHT / 100.0))
            result.score = blended
            if getattr(result, "bucket", -1) >= 0:
                try:
                    from pronunciation.phoneme.speech import bucketize_score
                    result.bucket = bucketize_score(blended)
                except Exception:  # noqa: BLE001
                    pass
            else:
                result.passed = blended >= config.PRONUNCIATION_SCORE_THRESHOLD
        except Exception:  # noqa: BLE001 - fluency must not break scoring
            logger.exception("Fluency blend failed:")

    def _apply_shadowing(self, result, audio: np.ndarray, user_sr: int) -> None:
        if self.reference_audio is None:
            return
        try:
            s = shadowing.compute_shadowing(
                user_audio=audio, user_sr=user_sr,
                reference_audio=self.reference_audio, reference_sr=self.reference_sr,
            )
            result.shadowing_axis = s.get("shadowing_axis", -1.0)
            result.delay_sec = s.get("delay_sec", 0.0)
            result.overlap = s.get("overlap", 0.0)
            blended = (result.score * (1.0 - config.SHADOWING_WEIGHT / 100.0)
                       + s.get("shadowing_axis", 0.0) * (config.SHADOWING_WEIGHT / 100.0))
            result.score = blended
            if getattr(result, "bucket", -1) >= 0:
                try:
                    from pronunciation.phoneme.speech import bucketize_score
                    result.bucket = bucketize_score(blended)
                except Exception:  # noqa: BLE001
                    pass
            else:
                result.passed = blended >= config.PRONUNCIATION_SCORE_THRESHOLD
        except Exception:  # noqa: BLE001 - shadowing must not break scoring
            logger.exception("Shadowing blend failed:")

    def _persist_take(self, phrase: str, result,
                      audio: np.ndarray, audio_duration: float
                      ) -> Optional[Dict[str, Any]]:
        """Write one take to the persistent stats store (best-effort)."""
        if not config.STATS_ENABLED or config.HISTORY_MODE == "none":
            return None
        try:
            error_words = stats_mod.extract_error_words(result)
            phoneme_scores = stats_mod.extract_phoneme_scores(result)
            transcript = getattr(result, "transcription", "") or ""
            raw_score = float(getattr(result, "score", 0.0))

            session = self.stats_store.open_session_if_needed()
            if session is None:
                return None

            # Reuse audio if already written for this take, else dump it.
            saved = self._last_take_audio
            take_id = (saved or {}).get("take_id") or (
                f"t-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}")
            audio_files = None
            if saved is not None and saved.get("session_id") == session["id"]:
                audio_files = saved.get("audio_files")
            if audio_files is None and audio_duration > 0:
                audio_files = recorder_store.dump_take_audio(
                    session["id"], take_id, audio, audio,
                    config.AUDIO_SAMPLE_RATE)

            take = self.stats_store.record_take(
                phrase, raw_score,
                audio_duration_sec=audio_duration,
                transcript=transcript,
                error_words=error_words,
                phoneme_scores=phoneme_scores,
                grade=getattr(result, "grade", "") or "",
                grade_value=getattr(result, "grade_value", -1.0) or -1.0,
                attempts=1,
                audio_files=audio_files,
                take_id=take_id,
            )
            if take is not None:
                try:
                    recorder_store.dump_take_meta(take, session["id"])
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to dump take meta:")
            return take
        except Exception:  # noqa: BLE001 - never break the practice loop
            logger.exception("Failed to persist take:")
            return None

    # -- stats ---------------------------------------------------------------

    def session_stats(self) -> Dict[str, Any]:
        """The current session's tally, for the frontend's summary row."""
        try:
            self.stats_store.load()
            session = self.stats_store.current_session()
            if session is None:
                return {"takes": 0, "avg": 0.0, "best": 0.0, "grade": ""}
            takes = session.get("takes", [])
            if not takes:
                return {"takes": 0, "avg": 0.0, "best": 0.0, "grade": ""}
            scores = [t.get("score", 0.0) for t in takes]
            avg = sum(scores) / len(scores)
            best = max(scores)
            return {
                "takes": len(takes),
                "avg": round(avg, 1),
                "best": round(best, 1),
                "grade": takes[-1].get("grade", ""),
            }
        except Exception:  # noqa: BLE001 - stats must never fail the request
            logger.exception("Stats read failed:")
            return {"takes": 0, "avg": 0.0, "best": 0.0, "grade": ""}

    # -- weakness / strength / dashboard -------------------------------------

    def weakness(self, range_key: str = "last_30d") -> Dict[str, Any]:
        """Weak-word and phoneme aggregates plus suggestions.

        Mirrors ``WeaknessView.refresh``: takes are sliced by the range key,
        then aggregated by ``analysis``. Native language comes from config, as
        the desktop does. Returns plain data for the frontend.
        """
        from mimora.analysis import (
            aggregate_error_words,
            aggregate_phoneme_scores,
            generate_suggestions,
            sorted_by_error_rate,
            sorted_by_weakness,
        )

        try:
            self.stats_store.load()
            raw = self.stats_store.all_takes(lookback=None)
        except Exception:  # noqa: BLE001
            logger.exception("Weakness: could not load takes:")
            raw = []
        takes = _filter_takes_by_range(raw, range_key)
        word_agg = aggregate_error_words(takes)
        native = config.NATIVE_LANGUAGE
        phoneme_agg = aggregate_phoneme_scores(takes, native_language=native)
        recent = [t.get("words_per_minute", 0) or 0
                  for _s, t in raw[-30:]]
        recent_wpm = (sum(recent) / len(recent)) if recent else 0.0
        suggestions = generate_suggestions(
            word_agg, phoneme_agg, recent_wpm=recent_wpm, native_language=native)
        return {
            "range_key": range_key,
            "range_options": [
                {"key": k, "label": l} for k, l, _s in WEAKNESS_RANGE_OPTIONS],
            "native_language": native,
            "recent_wpm": round(recent_wpm, 1),
            "words": sorted_by_error_rate(word_agg),
            "phonemes": sorted_by_weakness(phoneme_agg),
            "suggestions": suggestions,
        }

    def history(self, lookback: int = 50) -> Dict[str, Any]:
        """Sessions + drill + dictation history for the browse view."""
        try:
            self.stats_store.load()
            sessions = self.stats_store.all_sessions()
            drill = self.stats_store.all_drill_sessions()
            dictation = self.stats_store.all_dictation_sessions()
        except Exception:  # noqa: BLE001
            logger.exception("History read failed:")
            sessions, drill, dictation = [], [], []
        return {
            "sessions": sessions,
            "drill_sessions": drill,
            "dictation_sessions": dictation,
            "history_mode": config.HISTORY_MODE,
        }

    def dashboard(self) -> Dict[str, Any]:
        """Summary cards + chart series, mirroring ``StatsView.refresh``.

        The desktop computes everything in the view from ``sessions`` and the
        flattened takes; this runtime does the same pure-data computation so
        the HTTP layer returns numbers the frontend renders directly.
        """
        try:
            self.stats_store.load()
            sessions = self.stats_store.all_sessions()
            pairs = self.stats_store.all_takes(lookback=None)
        except Exception:  # noqa: BLE001
            logger.exception("Dashboard read failed:")
            sessions, pairs = [], []
        takes = [t for _s, t in pairs]

        total_time = int(sum(t.get("audio_duration_sec") or 0 for t in takes))
        unique = len({t.get("phrase") for t in takes if t.get("phrase")})
        scores = [float(t.get("score") or 0) for t in takes]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        best_score = round(max(scores), 1) if scores else 0.0
        streak = _compute_streak(sessions, config.DAILY_TARGET_MINUTES)

        # Daily average score, last 30 days (line).
        daily: Dict[str, List[float]] = defaultdict(list)
        today = datetime.date.today()
        for t in takes:
            try:
                day = datetime.datetime.fromisoformat(
                    t.get("timestamp") or "").strftime("%Y-%m-%d")
            except ValueError:
                continue
            daily[day].append(float(t.get("score") or 0))
        daily_avg = []
        for i in range(29, -1, -1):
            day = (today - datetime.timedelta(days=i)).isoformat()
            vals = daily.get(day)
            daily_avg.append({
                "date": day,
                "score": round(sum(vals) / len(vals), 1) if vals else None,
            })

        # Score distribution buckets.
        buckets = [0, 0, 0, 0]
        for s in scores:
            buckets[min(int(s // 25), 3)] += 1

        # Per-day activity, last 28 days (heatmap).
        day_sec: Dict[str, int] = defaultdict(int)
        for t in takes:
            try:
                day = datetime.datetime.fromisoformat(
                    t.get("timestamp") or "").strftime("%Y-%m-%d")
            except ValueError:
                continue
            day_sec[day] += int(t.get("audio_duration_sec") or 0)
        activity = []
        for i in range(27, -1, -1):
            day = (today - datetime.timedelta(days=i)).isoformat()
            activity.append({"date": day, "seconds": day_sec.get(day, 0)})

        # Per-day duration table, last 14 days, newest first.
        duration_rows = []
        for i in range(13, -1, -1):
            day = (today - datetime.timedelta(days=i)).isoformat()
            day_takes = [t for t in takes if (t.get("timestamp") or "")[:10] == day]
            duration_rows.append({
                "date": day,
                "seconds": day_sec.get(day, 0),
                "takes": len(day_takes),
            })

        # Recent sessions, last 10, newest first.
        recent_sessions = [
            {
                "id": s.get("id"),
                "started_at": s.get("started_at"),
                "takes": len(s.get("takes") or []),
                "avg_score": round(_avg_score_for_session(s), 1),
                "duration_sec": s.get("duration_sec", 0),
            }
            for s in sessions[-10:][::-1]
        ]

        return {
            "cards": {
                "total_time_sec": total_time,
                "total_time_label": _fmt_seconds(total_time),
                "sessions": len(sessions),
                "takes": len(takes),
                "unique_phrases": unique,
                "avg_score": avg_score,
                "best_score": best_score,
                "streak": streak,
                "daily_target_minutes": config.DAILY_TARGET_MINUTES,
            },
            "daily_avg": daily_avg,
            "distribution": {
                "buckets": ["0-24", "25-49", "50-74", "75-100"],
                "counts": buckets,
            },
            "activity": activity,
            "duration_rows": duration_rows,
            "recent_sessions": recent_sessions,
        }

    def delete_take(self, session_id: str, take_id: str) -> bool:
        try:
            self.stats_store.load()
            return self.stats_store.delete_take(session_id, take_id)
        except Exception:  # noqa: BLE001
            logger.exception("Delete take failed:")
            return False

    def take_audio(self, session_id: str, take_id: str) -> Optional[Dict[str, Any]]:
        """Resolve the recorded WAV for one take (Play button on desktop).

        Mirrors ``ui_history_browse``'s Play ▶ (only shown when
        ``HISTORY_MODE == "with_audio"``): the normalized wav (or raw fallback)
        is read from disk and returned as base64 16-bit PCM so the browser can
        play it back. Returns ``None`` when the mode is off or the file is
        missing, so the frontend can hide the button rather than error.
        """
        if config.HISTORY_MODE != "with_audio":
            return None
        try:
            path = recorder_store.resolve_take_audio(session_id, take_id)
        except Exception:  # noqa: BLE001
            logger.exception("Resolve take audio failed (%s/%s):", session_id, take_id)
            return None
        if path is None:
            return None
        try:
            import wave

            with wave.open(str(path), "rb") as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
                width = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
            if width != 2:
                # _write_wav always writes 16-bit PCM, but guard for safety.
                import io

                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(b"")
                frames = buf.getvalue()
                rate = 16000
                channels = 1
            return {
                "session_id": session_id,
                "take_id": take_id,
                "sample_rate": rate,
                "channels": channels,
                "wav_base64": base64.b64encode(frames).decode("ascii"),
            }
        except Exception:  # noqa: BLE001
            logger.exception("Read take audio failed (%s/%s):", session_id, take_id)
            return None

    def delete_session(self, session_id: str) -> bool:
        try:
            self.stats_store.load()
            return self.stats_store.delete_session(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("Delete session failed:")
            return False

    def clear_history(self) -> bool:
        try:
            self.stats_store.load()
            self.stats_store.clear_all()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Clear history failed:")
            return False

    def export_history(self) -> Dict[str, Any]:
        """Export the stats store as a JSON string (the frontend downloads it)."""
        import json

        try:
            self.stats_store.load()
            data = getattr(self.stats_store, "data", None)
            if data is None:
                data = self.stats_store.all_sessions()
            filename = f"mimora-stats-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
            return {
                "filename": filename,
                "content": json.dumps(data, ensure_ascii=False, indent=2),
            }
        except Exception:  # noqa: BLE001
            logger.exception("Export history failed:")
            raise RuntimeError("Could not export history")

    # -- dictation / drill engine sessions -----------------------------------

    def dictation_start(self, source_text: str = "",
                        random_order: Optional[bool] = None) -> Dict[str, Any]:
        """Start a dictation session and return its first phrase state.

        Mirrors ``app.on_dictation_clicked``: builds a ``DictationEngine``
        from the source text and the configured thresholds, picks the first
        phrase. The engine is held on the runtime so subsequent calls
        (submit / skip / finish) advance the same session.
        """
        from mimora.dictation.engine import DictationEngine

        if not source_text.strip():
            source_text = self.source_text()
        engine_inst = DictationEngine(
            source_text=source_text,
            pass_threshold=config.DICTATION_PASS_THRESHOLD,
            max_attempts=config.DICTATION_MAX_ATTEMPTS,
            random_order=config.DICTATION_RANDOM_ORDER
            if random_order is None else random_order,
            review_store=self._dictation_review_store(),
        )
        self._dictation_engine = engine_inst
        phrase = engine_inst.next_phrase()
        self._dictation_current_phrase = phrase
        return self._dictation_state(phrase)

    def dictation_submit(self, user_text: str) -> Dict[str, Any]:
        """Score one dictation attempt and advance the session."""
        engine_inst = self._require_dictation()
        phrase = getattr(self, "_dictation_current_phrase", "") or ""
        if not phrase:
            # No phrase has been picked yet (e.g. the frontend started and
            # submitted without calling /api/dictation/start's next_phrase).
            phrase = engine_inst.next_phrase()
            self._dictation_current_phrase = phrase
        from mimora.dictation.scoring import score_dictation
        details = score_dictation(user_text, phrase)
        wrong_words = [w["word"] for w in details["status"] if w["state"] in ("missed", "wrong")]
        spelling_errors = [w["word"] for w in details["status"] if w["state"] == "wrong"]
        attempt = engine_inst.record_attempt(
            user_text, phrase, details["score"],
            wrong_words=wrong_words, extra_words=details.get("extra_words", []),
            spelling_errors=spelling_errors)
        state = self._dictation_state(phrase)
        state.update({
            "score": details["score"],
            "passed": attempt.passed,
            "status": details["status"],
            "extra_words": details.get("extra_words", []),
            "alignment": dictation_scoring_alignment(details),
        })
        # The frontend advances to ``data.phrase`` unless the session is done.
        # Move to the next sentence now so that phrase is ready; when done, the
        # current phrase is kept (the frontend calls /api/dictation/finish).
        if not state["progress"].get("done", False):
            phrase = engine_inst.next_phrase()
            self._dictation_current_phrase = phrase
            next_state = self._dictation_state(phrase)
            state.update({
                "phrase": next_state["phrase"],
                "position": next_state["position"],
                "progress": next_state["progress"],
            })
        return state

    def dictation_skip(self) -> Dict[str, Any]:
        engine_inst = self._require_dictation()
        phrase = engine_inst.next_phrase()
        self._dictation_current_phrase = phrase
        return self._dictation_state(phrase)

    def dictation_previous(self) -> Dict[str, Any]:
        engine_inst = self._require_dictation()
        phrase = engine_inst.previous_phrase()
        self._dictation_current_phrase = phrase
        return self._dictation_state(phrase)

    def dictation_finish(self) -> Dict[str, Any]:
        """Finish the session: persist it and return the outcome."""
        engine_inst = self._require_dictation()
        try:
            self.stats_store.record_dictation_session(engine_inst.to_storage_dict())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist dictation session:")
        outcome = engine_inst.finish()
        self._dictation_engine = None
        self._dictation_current_phrase = ""
        return {
            "outcome": {
                "total_attempts": outcome.total_attempts,
                "total_passed": outcome.total_passed,
                "first_score": outcome.first_score,
                "last_score": outcome.last_score,
                "avg_score": round(outcome.avg_score, 1),
                "mastered": outcome.mastered,
                "wrong_words": outcome.wrong_words,
                "extra_words": outcome.extra_words,
                "spelling_errors": outcome.spelling_errors,
            },
        }

    def dictation_cancel(self) -> Dict[str, Any]:
        self._dictation_engine = None
        self._dictation_current_phrase = ""
        return {"active": False}

    def drill_start(self, target: Dict[str, Any],
                    source_text: str = "") -> Dict[str, Any]:
        """Start a drill session for ``target`` and return its first phrase."""
        from mimora.drill.engine import DrillEngine

        if not source_text.strip():
            source_text = self.source_text()
        engine_inst = DrillEngine(
            target=target,
            source_text=source_text,
            pass_threshold=config.DRILL_PASS_THRESHOLD,
            max_attempts=config.DRILL_MAX_ATTEMPTS,
            priority_words=self._weak_words_for_drill(),
        )
        self._drill_engine = engine_inst
        phrase = engine_inst.next_phrase()
        return self._drill_state(phrase)

    def drill_next(self) -> Dict[str, Any]:
        engine_inst = self._require_drill()
        phrase = engine_inst.next_phrase()
        return self._drill_state(phrase)

    def drill_previous(self) -> Dict[str, Any]:
        engine_inst = self._require_drill()
        phrase = engine_inst.previous_phrase()
        return self._drill_state(phrase)

    def drill_record_take(self, phrase: str, score: float) -> Dict[str, Any]:
        """Record a scored take into the active drill session."""
        engine_inst = self._require_drill()
        take = engine_inst.record_take(phrase, score)
        return self._drill_state(phrase)

    def drill_finish(self) -> Dict[str, Any]:
        engine_inst = self._require_drill()
        try:
            self.stats_store.record_drill_session(engine_inst.to_storage_dict())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist drill session:")
        outcome = engine_inst.finish()
        self._drill_engine = None
        return {
            "outcome": {
                "target_type": outcome.target_type,
                "target_value": outcome.target_value,
                "total_attempts": outcome.total_attempts,
                "total_passed": outcome.total_passed,
                "first_score": outcome.first_score,
                "last_score": outcome.last_score,
                "avg_score": round(outcome.avg_score, 1),
                "mastered": outcome.mastered,
                "takes": [
                    {
                        "phrase": t.phrase,
                        "score": t.score,
                        "attempt": t.attempt,
                        "passed": t.passed,
                    } for t in outcome.takes
                ],
            },
        }

    def drill_cancel(self) -> Dict[str, Any]:
        self._drill_engine = None
        return {"active": False}

    def _require_dictation(self):
        if getattr(self, "_dictation_engine", None) is None:
            raise RuntimeError("No active dictation session")
        return self._dictation_engine

    def _require_drill(self):
        if getattr(self, "_drill_engine", None) is None:
            raise RuntimeError("No active drill session")
        return self._drill_engine

    def _dictation_state(self, phrase: str) -> Dict[str, Any]:
        engine_inst = self._require_dictation()
        progress = engine_inst.progress()
        return {
            "phrase": phrase,
            "position": engine_inst.position,
            "progress": {
                "total": progress.get("total", 0),
                "current": progress.get("current", 0),
                "passed": progress.get("passed", 0),
                "streak": progress.get("streak", 0),
                "mastered": progress.get("mastered", False),
                "done": progress.get("done", False),
                "scores": progress.get("scores", []),
                "threshold": progress.get("threshold", 0),
            },
            "active": True,
        }

    def _drill_state(self, phrase: str) -> Dict[str, Any]:
        engine_inst = self._require_drill()
        progress = engine_inst.progress()
        return {
            "phrase": phrase,
            "fallback_reason": engine_inst.fallback_reason,
            "fallback_message": engine_inst.fallback_message(),
            "progress": {
                "total": progress.get("total", 0),
                "current": progress.get("current", 0),
                "passed": progress.get("passed", 0),
                "streak": progress.get("streak", 0),
                "mastered": progress.get("mastered", False),
                "done": progress.get("done", False),
                "scores": progress.get("scores", []),
                "threshold": progress.get("threshold", 0),
            },
            "active": True,
        }

    def _dictation_review_store(self):
        if not config.DICTATION_REVIEW_ENABLED:
            return None
        try:
            store = ReviewStore()
            store.load()
            return store
        except Exception:  # noqa: BLE001
            logger.exception("Could not open dictation review store:")
            return None

    def _weak_words_for_drill(self) -> Tuple[str, ...]:
        try:
            from mimora.analysis import aggregate_error_words, top_words
            self.stats_store.load()
            word_agg = aggregate_error_words(self.stats_store.all_takes(lookback=None))
            return tuple(top_words(word_agg))
        except Exception:  # noqa: BLE001
            logger.exception("Could not compute weak words for drill:")
            return ()

    # -- result serialization ------------------------------------------------

    @staticmethod
    def _result_to_dict(result, phrase: str,
                        persisted: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Flatten a PronunciationResult into JSON-safe plain data."""
        def _opt(obj):
            return None if obj is None else obj.tolist() if hasattr(obj, "tolist") else obj

        return {
            "phrase": phrase,
            "score": round(float(getattr(result, "score", 0.0)), 2),
            "passed": bool(getattr(result, "passed", False)),
            "scored": bool(getattr(result, "scored", True)),
            "bucket": int(getattr(result, "bucket", -1)),
            "grade": getattr(result, "grade", "") or "",
            "grade_value": float(getattr(result, "grade_value", -1.0) or -1.0),
            "transcription": getattr(result, "transcription", "") or "",
            "feedback": getattr(result, "feedback", "") or "",
            "word_errors": list(getattr(result, "word_errors", []) or []),
            "words_with_errors": list(getattr(result, "words_with_errors", []) or []),
            "word_diff": list(getattr(result, "word_diff", []) or []),
            "reference_words": list(getattr(result, "reference_words", []) or []),
            "recognized_units": list(getattr(result, "recognized_units", []) or []),
            "weak_phonemes": list(getattr(result, "weak_phonemes", []) or []),
            "expected_phonemes": list(getattr(result, "expected_phonemes", []) or []),
            "transcribed_phonemes": list(getattr(result, "transcribed_phonemes", []) or []),
            "prosody": dict(getattr(result, "prosody", {}) or {}),
            "fluency_axis": float(getattr(result, "fluency_axis", -1.0) or -1.0),
            "speed_label": getattr(result, "speed_label", "") or "",
            "wpm": round(float(getattr(result, "wpm", 0.0) or 0.0), 1),
            "shadowing_axis": float(getattr(result, "shadowing_axis", -1.0) or -1.0),
            "delay_sec": round(float(getattr(result, "delay_sec", 0.0) or 0.0), 3),
            "overlap": round(float(getattr(result, "overlap", 0.0) or 0.0), 3),
            "per_phone_distance": _opt(getattr(result, "per_phone_distance", None)),
            "phoneme_score": float(getattr(result, "phoneme_score", 0.0) or 0.0),
            "recall": float(getattr(result, "recall", 0.0) or 0.0),
            "good_anchor": float(getattr(result, "good_anchor", 0.0) or 0.0),
            "user_percent": float(getattr(result, "user_percent", 0.0) or 0.0),
            "persisted_take_id": (persisted or {}).get("id"),
        }
