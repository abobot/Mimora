# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""FastAPI HTTP server for the Mimora web companion.

Serves the static frontend and the REST API that drives the practice loop.
The frontend records audio in the browser and uploads it here; the backend
scores it with the same engine/TTS/LLM modules the desktop app uses.

Run: ``mimora-web``, ``python -m mimora.web.server``, or
``python -m uvicorn mimora.web.server:app --host 127.0.0.1 --port 8788``.

The static frontend lives in ``mimora/web/static/`` so it ships inside the
package (a wheel only carries non-Python files that sit inside a package -
see ``mimora/paths.py``).
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import threading
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mimora import config
from mimora.web.runtime import WebRuntime

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788

# One runtime per server process, created lazily and initialized from the
# lifespan so model loading happens before the first request is answered.
_runtime_lock = threading.Lock()
_runtime: Optional[WebRuntime] = None


def get_runtime() -> WebRuntime:
    """The process-wide runtime, creating it on first call."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = WebRuntime()
    return _runtime


# -- request/response models -------------------------------------------------

class PhraseRequest(BaseModel):
    """Body for POST /api/phrase."""
    source_text: str = ""
    length: str = "full"
    phrase_source: Optional[str] = None


class PhraseResponse(BaseModel):
    """A generated phrase plus its synthesized reference audio (base64 WAV)."""
    phrase: str
    reference_wav_base64: str  # 16 kHz mono 16-bit PCM WAV
    reference_sr: int
    translation: str = ""
    voice: str = ""
    review: bool = False


class ScoreResponse(BaseModel):
    """The scored take, flattened for JSON."""
    phrase: str
    score: float
    passed: bool
    grade: str
    grade_value: float
    transcription: str
    feedback: str
    word_errors: list[Any]
    weak_phonemes: list[Any]
    prosody: dict[str, Any]
    persisted_take_id: Optional[str]
    # Frontend convenience
    session_takes: int = 0
    session_avg: float = 0.0
    session_best: float = 0.0


# -- app ---------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize the runtime at startup, tear it down at shutdown.

    Model loading runs in a background thread so the HTTP server answers
    immediately; the frontend polls ``/api/health`` until ``ready`` is true.
    """
    def load() -> None:
        try:
            get_runtime().initialize()
        except Exception:  # noqa: BLE001 - already logged by the runtime
            pass

    threading.Thread(target=load, daemon=True, name="web-runtime-init").start()
    yield
    try:
        get_runtime().shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("Runtime shutdown error:")


app = FastAPI(
    title="Mimora Web",
    description="Local, offline pronunciation trainer served over HTTP. "
                "Record with your browser mic, score with the local engine.",
    version="1.2.0",
    lifespan=_lifespan,
)


def _ready_or_raise() -> WebRuntime:
    """Return the runtime if initialized, else raise HTTP 503 with progress."""
    rt = get_runtime()
    if not rt.ready():
        err = rt.load_error()
        if err:
            raise HTTPException(status_code=500, detail=f"Initialization failed: {err}")
        raise HTTPException(status_code=503, detail="Models are still loading - try again shortly")
    return rt


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness plus a short readiness summary for the frontend banner."""
    rt = get_runtime()
    return {
        "ok": True,
        "ready": rt.ready(),
        "engine": config.ENGINE,
        "llm_backend": config.LLM_BACKEND,
        "load_error": rt.load_error(),
        "settings": rt.settings(),
    }


@app.get("/api/settings")
def settings() -> dict[str, Any]:
    return get_runtime().settings()


@app.post("/api/phrase")
def phrase(req: PhraseRequest) -> dict[str, Any]:
    """Generate the next phrase and synthesize its reference audio.

    The reference WAV is returned base64-encoded so the frontend can play it
    immediately (the same waveform is then used as the scoring reference - one
    synthesis, exactly like the desktop app).
    """
    rt = _ready_or_raise()
    source_text = req.source_text.strip() or _default_source_text()
    phrase_text = rt.generate_phrase(
        source_text, length=req.length, phrase_source=req.phrase_source)
    if not phrase_text:
        raise HTTPException(status_code=503, detail="Could not generate a phrase")
    audio, sr = rt.synthesize_reference(phrase_text)
    translation = rt.translate(phrase_text)
    return {
        "phrase": phrase_text,
        "reference_wav_base64": _wav_to_base64(audio, sr),
        "reference_sr": sr,
        "translation": translation,
        "voice": rt.current_voice,
        "review": bool(rt.review_enabled),
    }


@app.post("/api/score")
async def score(
    file: UploadFile = File(...),
    phrase: str = Form(...),
    sample_rate: int = Form(16000),
) -> dict[str, Any]:
    """Score one recorded take.

    ``file`` is the WAV (or raw PCM) the browser recorded. It is decoded to a
    16 kHz mono float32 array and scored against the current reference phrase.
    """
    rt = _ready_or_raise()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        audio, actual_sr = _decode_wav(data, sample_rate)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Cannot decode audio: {exc}") from exc
    try:
        result = rt.score_recording(audio, actual_sr, phrase)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Scoring failed:")
        raise HTTPException(status_code=500, detail=f"Scoring failed: {exc}") from exc

    session = rt.session_stats()
    result.update({
        "session_takes": session.get("takes", 0),
        "session_avg": session.get("avg", 0.0),
        "session_best": session.get("best", 0.0),
    })
    return result


@app.get("/api/stats/session")
def session_stats() -> dict[str, Any]:
    """The current session's tally (takes, avg, best, last grade)."""
    rt = get_runtime()
    return rt.session_stats()


@app.get("/api/stats/history")
def stats_history(lookback: int = 50) -> dict[str, Any]:
    """Recent takes for a trend view."""
    rt = get_runtime()
    try:
        rt.stats_store.load()
        pairs = rt.stats_store.all_takes(lookback=lookback)
        # all_takes returns (session, take) pairs, oldest first; it already
        # sliced to `lookback`, so plain fields only here.
        takes = [t for (_s, t) in pairs]
        return {
            "takes": [
                {
                    "id": t.get("id"),
                    "timestamp": t.get("timestamp"),
                    "phrase": t.get("phrase"),
                    "score": t.get("score"),
                    "grade": t.get("grade"),
                }
                for t in takes[-lookback:]
            ]
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("History read failed:")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# -- static frontend ---------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# -- helpers -----------------------------------------------------------------

def _default_source_text() -> str:
    """The practice text file contents, or a short fallback."""
    try:
        path = Path(config.PRACTICE_TEXT_FILE)
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        fallback = Path(config.PRACTICE_TEXT_FALLBACK)
        if fallback.exists():
            return fallback.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _wav_to_base64(audio: np.ndarray, sample_rate: int) -> str:
    """Encode a mono float32 array as a 16-bit PCM WAV, base64."""
    import base64

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _decode_wav(data: bytes, fallback_sr: int) -> tuple[np.ndarray, int]:
    """Decode uploaded audio into (float32 mono array, sample rate).

    Accepts a WAV (with header) or raw 16-bit PCM (no header). WAV path uses
    the stdlib ``wave``; raw PCM assumes the caller passed ``sample_rate``.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        with wave.open(io.BytesIO(data), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if ch != 1:
            # Down-mix simple stereo to mono by averaging.
            samples = np.frombuffer(frames, dtype=np.int16)
            if ch == 2:
                samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)
            else:
                raise ValueError(f"Unsupported channel count: {ch}")
        else:
            samples = np.frombuffer(frames, dtype=np.int16)
        if sw == 2:
            audio = samples.astype(np.float32) / 32768.0
        elif sw == 1:
            audio = (samples.astype(np.float32) - 128.0) / 128.0
        elif sw == 4:
            audio = samples.astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"Unsupported sample width: {sw}")
        return audio, sr
    # Raw PCM (16-bit mono) - assume caller's sample rate.
    samples = np.frombuffer(data, dtype=np.int16)
    return (samples.astype(np.float32) / 32768.0), fallback_sr


# -- CLI ---------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    """Entry point for ``mimora-web`` / ``python -m mimora.web.server``."""
    parser = argparse.ArgumentParser(description="Mimora web companion")
    parser.add_argument("--host", default=os.environ.get("MIMORA_WEB_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MIMORA_WEB_PORT", DEFAULT_PORT)))
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload (dev)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
    )

    import uvicorn

    print(f"Mimora web: http://{args.host}:{args.port}")
    uvicorn.run(
        "mimora.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
