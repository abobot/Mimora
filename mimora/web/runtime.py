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

import logging
import threading
import time
import uuid
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
from mimora.translator import TranslatorManager

logger = logging.getLogger(__name__)


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
        return {
            "engine": config.ENGINE,
            "engine_choices": list(config.ENGINE_CHOICES),
            "practice_language": config.PRACTICE_LANGUAGE,
            "accent": config.ACCENT,
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
        }

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
