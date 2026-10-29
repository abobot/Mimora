# AGENTS.md

This file provides guidance to agents when working with code in this repository.

It is a map, not a manual: each entry says what a module is for and what would
break if it were changed carelessly. The reasoning behind a specific line lives
in the comment next to that line.

## Project Overview

Mimora is a local, offline **pronunciation trainer** (Python 3.11/3.12, Tkinter
GUI). It speaks an LLM-generated phrase aloud, records the user repeating it,
then scores the attempt against the reference with the active pronunciation
engine plus prosody. The user repeats the same phrase until satisfied, then
generates the next one.

The engine is selected by `config.ENGINE` (settings.json `"engine"`, dispatched
by [`mimora/engine.py`](mimora/engine.py)):

- **`phoneme`** (default) - espeak reference phonemes vs a wav2vec2 phoneme
  recognizer, feature-weighted edit distance, calibrated 0-5 grade.
- `acoustic` - Wav2Vec2-embedding cosine-DTW plus phoneme/word error rates.
  English-only.
- `hybrid` - runs BOTH the phoneme and acoustic engines on the same take and
  blends their axis-level scores into one 0-100 grade. Needs the reference
  audio, English-only; without either it degrades to the pure phoneme score.
  Loads two ~1.2 GB recognizers, so it is the heaviest option.
- `none` - scoring off. No recognizer is loaded, every take is accepted, the
  GUI shows a neutral read-out. For slow machines.

The **practice language is data, not an assumption**: every language is one
entry in `config.LANGUAGE_PROFILES`, assembled from the pure-data modules in
[`mimora/languages/`](mimora/languages/) (the profile format is documented in
that package's `__init__.py`). The active language/variant comes from
settings.json `"practice_language"` / `"accent"` and applies after a restart.
English (`american`/`british`) is fully calibrated; Spanish (`castilian`) runs
the phoneme engine as **experimental** until
`pronunciation/phoneme/es_model_calibration.json` lands. Mandarin
(`standard`) does the same until `cmn_model_calibration.json` lands. Adding a
language is a profile module plus an engine calibration, never a new
`if language == ...`.

The pronunciation-scoring core in `pronunciation/acoustic/` is adapted from
[OpenPronounce](https://github.com/Halleck45/OpenPronounce) (MIT) and reused as
a GUI-agnostic library.

The dependency files are located in the `%APPDATA%\uv\tools\mimora` directory.

## Running the App

```bash
pip install -e .
python main.py
```

Editable on purpose: a plain `pip install .` from a clone leaves a second copy
in site-packages that your edits never reach, and switches `paths.py` out of
source-tree mode. Dependencies come from `[project.dependencies]` and cover both
engines; the two `requirements.txt` under `pronunciation/` remain because those
subpackages are installable on their own.

Three launch forms, differing in nothing after `mimora/cli.py`'s `main()`: the
root `main.py` shim for a checkout, `python -m mimora`, and the `mimora` console
script.

Also requires a GGUF chat model at `config.EXTERNAL_MODEL_PATH`. **espeak-ng is
not a separate install**: the pinned `espeakng-loader` wheel ships the library
and [`pronunciation/common/espeak.py`](pronunciation/common/espeak.py) registers
it with `phonemizer`. On Linux add **PortAudio** (`libportaudio2`) - the
`sounddevice` wheel bundles it on Windows and macOS but not there.

**Default LLM backend**: `llama-server`, the official llama.cpp binary launched
as a subprocess ([`mimora/llm_server_ctl.py`](mimora/llm_server_ctl.py)).
`install.py` installs it (`step_llama_server`); the app fetches whatever is
missing on first run, asking first. **Alternatives**: `"llm_backend":
"lm-studio"` (LM Studio on `http://localhost:1234`, or `"lm_studio_host"`
elsewhere on the network), or `"off"` - no LLM at all, phrases are the source
text's own sentences taken verbatim in order
([`mimora/phrase_source.py`](mimora/phrase_source.py)). Nothing in the project
installs or imports llama-cpp-python.

## Architecture

### Controller and view

- [`mimora/app.py`](mimora/app.py) - `PronunciationTrainerGUI`: the Tkinter
  controller, recording, the Prompt→Record→Analyze→Feedback→Loop state machine,
  threading orchestration. Module-level `run()` is the startup sequence (root
  logging, first-run window, GUI) and the only thing `cli.py` calls.
- [`mimora/cli.py`](mimora/cli.py) - the console-script entry point. Exists
  purely for ordering: `bootstrap.early_init()` only works before the libraries
  it configures are imported, and `--version` must answer without waiting for
  torch, so neither can live in `app.py`. The function-local `from mimora import
  app` is load-bearing, not a style choice. **Stdlib-only at module level.**
  [`mimora/__main__.py`](mimora/__main__.py) is a shim over the same `main()`.
  On Windows `main()` also holds the named mutex `RUNNING_MUTEX_NAME` for the
  life of the process, so that the installers refuse to replace the files of
  a running Mimora. The name is repeated in `installer/mimora.iss`
  (`AppMutex`) and `installer/install.ps1`; change all three or none
  (`tests/test_cli.py` checks that they agree).
- [`mimora/splash.py`](mimora/splash.py) - the startup splash, shown by
  `cli.main()` before the heavy import and closed by `app.run()` once the
  main window exists (and by the first-run window, which it would cover).
  It runs in **its own process**: the main thread is busy importing and a
  window created there stops answering. The child ends when the pipe to
  the parent closes, so no exit path can leave it on the screen.
  **Stdlib-only at module level.**
- [`mimora/window_icon.py`](mimora/window_icon.py) - the application icon.
  `set_app_user_model_id()` must run **before the first `tk.Tk()`** of the
  process (the main window and the first-run window both call it), or Windows
  groups the window with python.exe and shows the Python icon.
  `APP_USER_MODEL_ID` is repeated in `installer/mimora.iss` and
  `installer/install.ps1`; change all three or none
  (`tests/test_window_icon.py` checks that they agree). A failure to load the icon is logged and never stops a window.
  `WM_CLASS` is the `className` of every `tk.Tk()` (main window, first-run
  window, splash) and the `StartupWMClass` of the Linux menu entry in
  `installer/install.sh`; `mimora/splash.py` repeats it as `_WM_CLASS`.
  Change all three or none (the same test file checks them).
  On Windows each new `Toplevel` calls `sharpen_window_icon()`: the icon
  that it gets from the root is the small one and is blurred on a scaled
  display (the process is not DPI-aware).
- [`mimora/bootstrap.py`](mimora/bootstrap.py) - early process setup,
  stdlib-only. Two phases that **must not be merged or reordered**:
  `early_init()` runs before the heavy imports (from `cli.py`),
  `setup_logging()` after them (from `app.run()`, `force=True` replacing
  handlers installed during the imports). Owns log continuity across an
  in-session restart (`--append-log` / `APPEND_LOG_FLAG`) and the
  `DISABLE_SAFETENSORS_CONVERSION` switch. `install_log()` belongs to neither
  phase: a root handler on an **append-only** second file for the length of a
  `with` block, so that what the first-run window downloaded outlives the next
  launch truncating `main.log`. It writes the same `logs/install.log` that
  `install.py` writes, which is the point - that script is not in the wheel,
  so on a wheel install nothing else records what was installed. What that file
  does NOT keep is named in `_INSTALL_LOG_MUTED` (`httpx`, one INFO line per
  request), by logger name and on that handler only - `main.log` keeps them.
  `open_log_section()` writes a run header into one handler's file, blank-line
  separated from what is already there; `detect_hardware` uses it as well.
- [`mimora/ui.py`](mimora/ui.py) - `TrainerView`: the view facade `app.py`
  composes. Window chrome, control row, `enter_*` intent methods, feedback
  orchestration; delegates panel-local work to
  [`ui_practice.py`](mimora/ui_practice.py) (source-text editor),
  [`ui_hero.py`](mimora/ui_hero.py) (phrase, translation, score row),
  [`ui_prosody.py`](mimora/ui_prosody.py) (sparklines) and
  [`ui_history.py`](mimora/ui_history.py) (attempt list). Shared palette, fonts
  and tooltip live in [`ui_theme.py`](mimora/ui_theme.py); importing it also
  disables ttkbootstrap's classic-widget autostyle hook.
- [`mimora/face_widget.py`](mimora/face_widget.py) - `FaceWidget`: cartoon
  articulation face on a Tk Canvas, driven by a pre-computed loudness track
  (`play_levels`) rather than a live audio callback. Frames rendered by Pillow
  at 4x supersampling and cached per quantized state.
- [`mimora/progress_widget.py`](mimora/progress_widget.py) - `ProgressRing`:
  session-average gauge with a per-attempt dot column. Named generically on
  purpose so a redesign swaps the class without moving the import.
- [`mimora/session.py`](mimora/session.py) - `SessionState`: score tally and
  bounded attempt history. Pure data, no Tk.
- [`mimora/playback.py`](mimora/playback.py) - `PlaybackController`: per-playback
  stop-event lifecycle plus the talking-mouth coupling. `play_with_face()` is
  the blocking chokepoint around every `play_array` call.
- [`mimora/settings_window.py`](mimora/settings_window.py) - the settings
  dialog. Declarative: every editable key is one `Field` grouped into
  `Section`s, and the window stays passive - changes go to `app.py`.
- [`mimora/settings_ctl.py`](mimora/settings_ctl.py) - `SettingsGlue`: the
  persistence mechanics around settings.json (persist, live-apply tables,
  window sync, reset-to-defaults). The dispatch itself stays in `app.py`.

### Engines and audio

- [`mimora/engine.py`](mimora/engine.py) - dispatcher: binds the backend chosen
  by `config.ENGINE` and exposes one `analyze(...)`, so `app.py` is
  engine-agnostic and only the selected engine's weights load.
- [`pronunciation/phoneme/speech.py`](pronunciation/phoneme/speech.py) -
  **default** engine, text-only reference. Model calibration ships in
  `pronunciation/phoneme/<lang>_model_calibration.json`; the per-user
  `phoneme_good` override lives in `config/calibration_phoneme.json`. The two
  are different in KIND, which is why they sit in different directories: one
  ships with the code, the other is state this machine produced, and an
  installed package's own directory is no place to write state to.
- [`pronunciation/acoustic/speech.py`](pronunciation/acoustic/speech.py) -
  alternative engine (adapted from OpenPronounce). Wav2Vec2 embeddings +
  per-step cosine DTW. Calibratable floor in `config/calibration_acoustic.json`;
  fitted on request by
  [`pronunciation/acoustic/calibrate.py`](pronunciation/acoustic/calibrate.py).
  No GUI dependency. Prosody is no longer computed here.
- [`pronunciation/hybrid/speech.py`](pronunciation/hybrid/speech.py) -
  combined engine: runs the phoneme and acoustic analyzers on the same take and
  blends their axis scores (default 0.5/0.5) into one 0-100 grade, then applies
  the phoneme calibrated bucket. Without the reference audio, or for non-English
  text, it degrades to the pure phoneme result plus a suffix in
  `result.feedback`. Its config lives in
  [`pronunciation/hybrid/config.py`](pronunciation/hybrid/config.py) and
  `configure()` configures both sub-engines. English-only (Spanish exposes no
  hybrid in its profile). Loads two recognizers, so it is the heaviest engine.
- [`mimora/prosody.py`](mimora/prosody.py) - engine-agnostic prosody layer,
  F0/energy contours (librosa/sklearn, no torch). Called by `app.py` after
  `analyze`, skipped entirely while the prosody block is collapsed. Plotting
  helpers in [`prosody_utils.py`](mimora/prosody_utils.py).
- [`mimora/fluency.py`](mimora/fluency.py) - the fluency axis blended into the
  score when `fluency_enabled`: half tempo (WPM vs
  `FLUENCY_REFERENCE_WPM`, `_speed_label` too slow/natural/too fast), half
  prosody similarity (cosine of resampled F0/energy contours). `app.py`
  `_apply_fluency` blends it into `result.score` at `FLUENCY_WEIGHT`% and
  re-buckets, so a take is graded on pronunciation + fluency together.
- [`mimora/shadowing.py`](mimora/shadowing.py) - the shadowing (跟读) axis
  blended into the score when `shadowing_enabled`: cross-correlates the
  user's amplitude envelope against the reference's to find the time offset
  (`delay_sec`, positive = the user trails) and the peak overlap, then
  penalizes lag beyond `LAG_TOLERANCE_SEC`. `app.py` `_apply_shadowing`
  blends it at `SHADOWING_WEIGHT`% and re-buckets, and `_start_shadowing_take`
  opens the recorder without stopping the reference playback so the user can
  speak along with it.
- [`mimora/tts.py`](mimora/tts.py) - `TTSManager`: facade over per-variant
  backends selected by profile data - `KokoroBackend` (torch, 24 kHz, English)
  and `SupertonicBackend` (ONNX, 44.1 kHz, Spanish). `synthesize()` returns the
  waveform at `TTSManager.sample_rate`, the backend's native rate, which callers
  read as a property and never as a constant.
- [`mimora/audio_io.py`](mimora/audio_io.py) - shared device infrastructure both
  the mic and speaker paths depend on, so neither depends on the other.
- [`mimora/recorder.py`](mimora/recorder.py) - `AudioRecorder`: capture thread
  with silence-based auto-stop, device selection, normalization, diagnostic WAV
  dumps. Returns one 16 kHz mono array.
- [`mimora/phoneme_examples.py`](mimora/phoneme_examples.py) - static IPA
  phoneme → example word table behind the "WORK ON" badges.

### Practice modes, analysis and persistence

- [`mimora/languages/`](mimora/languages/) - per-language profile data. Each
  module exposes a single `PROFILE` dict, pure data with no imports or side
  effects; `config.py` assembles them into `LANGUAGE_PROFILES` and derives
  every per-run constant from there. Adding a language = add a module here plus
  an engine calibration, then register it in that assembly - never an
  `if language == ...` branch. The format is documented in this package's
  `__init__.py`.
- [`mimora/dictation/engine.py`](mimora/dictation/engine.py) - dictation-mode
  logic: phrase selection, attempt tracking, session summary. Pure Python, no
  Tk, no engine imports; the controller owns one `DictationEngine` per active
  session. Phrases come from the user's source text (cycled with reshuffling),
  with a small fallback pool when the source is empty.
- [`mimora/dictation/scoring.py`](mimora/dictation/scoring.py) - word-level
  comparison of the typed text against the spoken reference, 0-100 score plus
  per-word diagnostics. An edit-distance alignment (`difflib`) rather than an
  index-by-index loop, so an inserted/dropped word does not shift later words
  into "wrong".
- [`mimora/drill/engine.py`](mimora/drill/engine.py) - drill-mode logic:
  progress tracking, mastery detection, phrase selection. Pure Python. A "word
  drill" picks phrases from the source text containing the target word; a
  "phoneme drill" drills one IPA symbol. Falls back to synthetic phrases when
  the source has none.
- [`mimora/persistence/stats.py`](mimora/persistence/stats.py) - persistent
  stats storage (`config/stats.json`): sessions, takes, roll-up totals, atomic
  writes, a rolling `.bak` snapshot and per-day archives, plus a bump-on-write
  schema version. `sessions[].totals` is a cached roll-up recomputed lazily
  after a take is deleted; every `save()` is one `os.replace`.
- [`mimora/persistence/recorder_store.py`](mimora/persistence/recorder_store.py)
  - per-take audio storage under `logs/records/<session_id>/<HHMMSS>-<hex>/`
  (`raw.wav` + `normalized.wav` + `meta.json`), replacing the old
  fixed-name `logs/records/*.wav`. Only writes when
  `config.HISTORY_MODE == "with_audio"`; the metadata path is always written
  because the aggregate stats are built from it.
- [`mimora/analysis/error_words.py`](mimora/analysis/error_words.py) -
  aggregates per-take `error_words[]` into a per-word leader board with a
  status (`need_practice` / `watch` / `improving`) that drives the weakness
  UI colour and the suggestion engine.
- [`mimora/analysis/phonemes.py`](mimora/analysis/phonemes.py) - phoneme
  weakness aggregation plus the ZH-interference table (which phonemes a Chinese
  speaker finds hard and why). A new language adds a sibling module
  (`phonemes_es.py`, `phonemes_ja.py` ...) with its own table and
  `interference_priority()` wrapper, wired in at the package level.
- [`mimora/analysis/ipa_map.py`](mimora/analysis/ipa_map.py) - English word to
  IPA, routed through the engine's own espeak-ng G2P
  (`pronunciation.phoneme.speech.reference_word_phonemes`) so the symbols are
  exactly the folded inventory the scorer uses - never a second, drifting
  transcription. `word_to_ipa()` is the single seam a future G2P swap plugs
  into; a hand-curated table plus a character heuristic remain as the soft
  fallback.
- [`mimora/analysis/suggestions.py`](mimora/analysis/suggestions.py) -
  rule-based suggestion generator for the weakness panel (word / phoneme /
  speed cards), each with a `target` dict the Drill engine consumes directly.
  v1 is deliberately LLM-free and <1 ms.
- [`mimora/ui_stats.py`](mimora/ui_stats.py) - stats dashboard Toplevel:
  summary cards, a daily-average score trend chart and a score-distribution bar
  chart, all on `tk.Canvas` (no matplotlib).
- [`mimora/ui_weakness.py`](mimora/ui_weakness.py) - weakness panel Toplevel:
  Overview (top suggestions), Words (error-word leader board with per-row
  "Start drill") and Phonemes (IPA heatmap with ZH hints).
- [`mimora/ui_dictation.py`](mimora/ui_dictation.py) - dictation-mode UI: hear
  a sentence, type one word per slot, get scored; sentence text deliberately not
  shown.
- [`mimora/ui_drill.py`](mimora/ui_drill.py) - drill-mode UI: repeated practice
  of one target word or phoneme with progress and a finish report. Only one
  drill at a time.
- [`mimora/ui_history_browse.py`](mimora/ui_history_browse.py) - history
  browser Toplevel: every session newest-first with score chips, trend arrows,
  per-take play (only with `HISTORY_MODE == "with_audio"`) and delete, plus a
  two-step "Clear all".

### LLM and translation

- [`mimora/llm.py`](mimora/llm.py) - `LLMManager`: OpenAI-compatible client,
  one phrase per non-streaming request. The model name is a placeholder because
  both supported servers ignore the field; `LLMManager(model=...)` is the escape
  hatch for a server that does route by name. `request_extra_body()` sends
  `chat_template_kwargs.enable_thinking=false` **to llama-server only**: an
  adopted server with a thinking model (Gemma from SpeakLoop) otherwise spends
  the whole token budget on `reasoning_content` and every phrase comes back
  empty. `phrase_sampling()` sends `ADOPTED_PHRASE_GEN_*` (1.0/0.95) to an
  adopted server and `PHRASE_GEN_*` (0.7/0.9) to the own one: a larger model
  repeats itself at the small model's values, and the small model breaks
  phrases at the larger one's. `app.py` sets `llm_mgr.adopted_server`.
- [`mimora/llm_server_ctl.py`](mimora/llm_server_ctl.py) -
  `LLMServerController`: subprocess lifecycle. `llama_server_command()` is a
  pure builder holding the tuning that must never be left to the binary's
  defaults; `start()` blocks until the server answers, `shutdown()` is safe from
  two threads at once. `_use_running_server()` is the fork before the launch:
  a server that already answers on the port is **adopted** as it is and never
  terminated on exit (a crashed session left it, or another local app such as
  SpeakLoop serves a larger model on the same port and key); a port held by
  anything that does not answer the API is refused, with the one sentence in
  `last_error` that `app.py` shows. The served model ids and the per-slot
  context from `/props` (outside `/v1`, so a plain GET) are logged for an
  adopted server, the context for a launched one too; both are diagnostic and
  never refuse a server that answers.
- [`mimora/translator.py`](mimora/translator.py) - `TranslatorManager`: offline
  NLLB-200 translation, loaded lazily, CPU by default. **Loading is not
  downloading here** - the weights are guaranteed present, because selecting a
  translation language while they are missing restarts into the first-run
  window.

### Configuration and paths

- [`mimora/config.py`](mimora/config.py) - all configuration plus the language
  model and the derived per-run constants. User overrides in
  `config/settings.json`; themes from `config/themes/` first, then the schemas
  shipped in `mimora/themes/`. Also home of the **offline gate**:
  `HF_HUB_OFFLINE=1` is set during import, but only once every repo the run
  actually needs is cached. That set is all-or-nothing, so each entry is
  conditional on what this run loads - one unnecessary entry keeps the Hub
  online for every model. The consequence of deciding this once, at import:
  nothing later in the process may start a hub download.
- [`mimora/loader.py`](mimora/loader.py) - pure, stateless config-loading
  helpers. `detect_device` trusts the stored value in one direction only: `cpu`
  short-circuits, `cuda` is re-checked against the installed torch, because
  `hardware_config.json` outlives the environment that wrote it.
- [`mimora/paths.py`](mimora/paths.py) - where every file lives, and the only
  module that knows. **Three roots, deliberately not one**: `data_root()` is
  what this machine writes, `shipped_root()` is read-only files inside this
  package, `resource_root()` the same one package over. In a clone all three are
  the same tree; installed they are not. The split between the last two is
  packaging, not taste: **setuptools puts a non-Python file into the wheel only
  from inside a package**. **Stdlib-only** - `install.py` reads it before the
  requirements exist, which is also what lets the modules forbidden to import
  `config` import this one instead.
- [`mimora/lifecycle.py`](mimora/lifecycle.py) - process-exit helpers, Tk-free:
  `hard_exit()` and `spawn_replacement()`. `relaunch_command()` reconstructs how
  THIS process was started rather than assuming, which is subtler than it looks
  on Windows (see the comment there).
- [`mimora/detect_hardware.py`](mimora/detect_hardware.py) - machine probe
  writing `config/hardware_config.json`, whose `config` section supplies the
  machine-derived overrides `config.py` prefers over its defaults. Lives in the
  package rather than in `tools/` on one criterion: it has to run on the
  **user's** machine, and a packaged install has neither `tools/` nor
  `install.py`. The GPU question it answers is not "is there a card" but "can
  the installed LLM binary use it". Also home of `warn_if_gpu_unused()`. Its
  `logs/hwdetect.log` is **appended**, one header-opened section per probe: the
  file is read to find out why a machine was detected the way it was, which is
  asked about a probe a later one has already replaced.

### Downloads and first run

Four downloaders, all **forbidden to import `config`** (config flips
`HF_HUB_OFFLINE=1` once the models are cached, which would switch the network
off exactly when a download is wanted) and all keeping huggingface_hub out of
their module-level imports so `install.py` can use them before the requirements
step:

- [`mimora/model_fetch.py`](mimora/model_fetch.py) - everything a run always
  needs (both Wav2Vec2 repos, Kokoro, NLLB, Supertonic). Owns the cache layout,
  `prepare_hf_env()` and `progress_kwargs()` - the rule that decides whether
  the installed huggingface_hub may be handed a `tqdm_class`, which
  `gguf_fetch` asks as well. Dropping that check passes the keyword to a hub
  that has no such parameter, and the first run dies on the required GGUF
  component wherever the pin in `pyproject.toml` does not apply.
- [`mimora/gguf_fetch.py`](mimora/gguf_fetch.py) - the GGUF chat model. Split
  from the above by what is skippable: with `lm-studio` or `off`, neither the
  binary nor the GGUF is needed.
- [`mimora/llama_server_fetch.py`](mimora/llama_server_fetch.py) - the pinned
  llama.cpp release into `bin/llama/`: sha256 per asset, staged unpack, then
  `--version` and `--list-devices` probes. `VARIANTS` covers Windows x64 (CUDA
  or CPU), Linux x64 (Vulkan or CPU, with CPU as the Vulkan variant's
  `fallback`) and macOS (Metal on Apple Silicon, CPU on Intel). Also exports
  `detect_driver_cuda()`, which `install.py` uses to pick its torch wheel.
- [`mimora/spacy_model_fetch.py`](mimora/spacy_model_fetch.py) - the one model
  no Mimora code asks for: Kokoro builds misaki, whose English G2P calls
  `spacy.cli.download` when `en_core_web_sm` is missing, which shells out to
  pip - and a `uv tool` environment has no pip. Unpacked under the data root
  and **appended** to `sys.path` (appended, so a model installed on purpose
  keeps winning).

- [`mimora/net.py`](mimora/net.py) - `ssl_context()` for the two downloaders
  that use urllib (`llama_server_fetch`, `spacy_model_fetch`): the system
  certificate store plus certifi. Without it both fail with
  CERTIFICATE_VERIFY_FAILED on a new Windows installation, whose store is
  almost empty. **Stdlib-only at module level**; certifi is imported inside
  the function and may be absent.

None of them aggregates "everything that is missing": which set matters depends
on the active engine and TTS backend, i.e. on `config`, so that question is
answered in `first_run.py` instead.

- [`mimora/models_info.py`](mimora/models_info.py) - the model catalogue, one
  record per model. Pure data importing nothing but `typing` (a unit test
  enforces it), which is what lets `config` and the fetchers share it. The
  single place a repo id is written down. Sizes are hardcoded because the
  first-run dialog must name a volume before the user agrees to it; re-snap with
  [`tools/measure_model_sizes.py`](tools/measure_model_sizes.py).
- [`mimora/first_run.py`](mimora/first_run.py),
  [`first_run_download.py`](mimora/first_run_download.py) and
  [`first_run_window.py`](mimora/first_run_window.py) - the "nothing is
  downloaded yet" path, run from `app.run()` **before** the GUI is constructed,
  because a refusal rewrites `llm_backend` and the constructor branches on it.
  `build_plan()` is the single place the dialog and the progress bar read from.
  **What a refusal means is the criterion** for the three levels: *required*
  (buttons are Download and Quit), *optional* (refusing writes `llm_backend:
  "off"`) and *translator* (refusing writes `translation_language: ""`). Each
  carries its own checkbox and its own flag, so one Skip never switches off a
  feature nobody declined. `ensure_ready()` returns `READY` / `CANCELLED` /
  `RESTART`, and restarts **whenever the plan changed** - see the module
  docstring and the comment at the end of `ensure_ready` for why that is the
  loop guard and why `config` is stale by then. `first_run_window` owns a
  short-lived `tk.Tk()` root and **must not build ttk widgets**. Everything
  past its "nothing is missing" return runs inside `bootstrap.install_log`,
  and that order is the rule: opened any earlier, an ordinary launch would
  append a section to `logs/install.log` for an installation that never
  happened. `first_run.plan_summary()` exists so the same summary can open
  that file without being logged into `main.log` twice.
- [`tools/preview_first_run.py`](tools/preview_first_run.py) - shows the
  first-run window in any of its states without a first run, because the window
  only appears on a machine that is missing something and that is never the
  machine it is being written on.
- [`tools/check_tqdm_hook.py`](tools/check_tqdm_hook.py) - tests
  `model_fetch.progress_kwargs` against the huggingface_hub that this
  environment actually resolved, with a real download of a few kilobytes. The
  fast suite cannot: it downloads nothing and describes the hub with fakes, so
  it says what the code does when `tqdm_class` is absent and never whether it
  is absent here. Run it after raising the huggingface_hub pin, and once on
  every platform the pin's marker excludes.
- [`tools/check_wheel.py`](tools/check_wheel.py) - checks a built wheel before
  it is uploaded; run it after the build and `twine check`. One
  version in the tree, the archive, METADATA and the file name; METADATA and
  the console and GUI scripts against `pyproject.toml`; no top-level module; and the
  archive against the working tree, file by file and in both directions. That
  last pair replaced a list of string searches ("is this fix in the wheel")
  that grew by one line per release and answered the same question - was this
  built from the current code - for one fix at a time. It installs nothing and
  uses no network: a file name in the index is spent once and for all, so the
  artefact is checked before the upload rather than after it.

### Shipped data

- [`mimora/texts/`](mimora/texts/) - default source texts, named by the active
  language profile. Inside the package rather than at the top of the tree so an
  installed copy has them at all (see the packaging note under `paths.py`).
- [`mimora/themes/`](mimora/themes/) - shipped UI color schemas, inside the
  package for the same reason. A user file of the same name in `config/themes/`
  replaces a shipped one; `config._DARK_THEME` stays the last-resort fallback
  and the definition of which colour keys are valid.
- [`mimora/icons/`](mimora/icons/) - the application icon (`mimora.ico` for
  Windows, `mimora-256.png` for Tk on Linux and macOS), inside the package for
  the same reason. The installer takes `mimora.ico` from here too. The vector
  source is `docs/mimora-icon.svg`; the files here are renders of it.

## State Machine (pronunciation loop)

1. **Prompt** - `llm_mgr.generate_phrase(source_text)` → `tts_mgr.synthesize()`.
   The array is stored as `self.reference_audio` and played. Generation, synth
   and playback all run in one daemon thread (`_generate_and_prompt`).
2. **Record** - `AudioRecorder._record_loop` → `get_audio`, 16 kHz mono. Gated
   by `_can_record()`.
3. **Analyze** - `_finalize_recording` → `analyze_recording` (daemon thread)
   calls `engine.analyze(...)`.
4. **Feedback** - `_show_feedback` (via `root.after`) fills the hero card and
   appends the take to the history.
5. **Loop** - the user decides when to move on. With
   `pass_gating_enabled` the next phrase is unlocked only once this take
   passes (`result.passed`); otherwise it is always available.

## Key Patterns & Gotchas

- **Threading**: recording, analysis, model loading, phrase generation and
  playback run in daemon threads. **Always update the GUI via `root.after()`**;
  never touch Tk widgets from a background thread. Source text is read on the
  main thread and passed into the worker.

- **Reference audio is synthesized once**: the same waveform is played to the
  user and passed to `analyze()` as the reference. Only ONE synthesis backend
  runs per session; the backends never mix within a run.

- **Sample rates**: recording 16 kHz; TTS at the backend's native rate (read
  from `TTSManager.sample_rate`, never a constant); Wav2Vec2 needs 16 kHz.
  `engine.analyze` takes `user_sr` and `reference_sr` and resamples internally.

- **Pronunciation model lifecycle**: models load lazily; `load_models()` makes
  loading explicit (call it in a background thread at startup) and `warm_up()`
  removes first-call latency. `speech.py` reads config via `getattr(...,
  default)` so it stays usable without config edits.

- **Pass gating**: `config.PASS_GATING_ENABLED` (settings `pass_gating_enabled`)
  makes `result.passed` live: when on, the Next-phrase button and auto-next
  stay locked until the current take passes. For the phoneme engine the
  calibrated-bucket path decides `passed` (`bucket >= PASS_BUCKET`), not the
  raw `score_threshold`; the acoustic/hybrid engines use the raw threshold.
  The gate lives in `ui.py` (`can_advance`) and `app.py` (`on_take_scored`).

- **GPU contention**: Wav2Vec2, Kokoro and llama.cpp compete for VRAM.
  Mitigations: the LLM runs in a separate process, and the loop's phases run
  sequentially. If VRAM is tight, set `WAV2VEC2_DEVICE = "cpu"`.
  `detect_hardware` only puts a recognizer on the GPU at `>=6 GB` free, because
  one recognizer (`~1.2 GB`) plus the GGUF (`~2 GB`) must fit together. The
  `hybrid` engine needs TWO recognizers resident at once (phoneme + acoustic,
  `~1.2 GB` each), so on a small card it is the first to overflow; prefer
  `WAV2VEC2_DEVICE = "cpu"` there, and note each take costs both inferences
  serially (roughly 1.5-2x a single engine on CPU).

- **Phrase generation**: `generate_phrase()` is stateless and non-streaming,
  with no conversational history. Variety comes from a sliding window over the
  source text plus a random focus word and opening-style hint. A proficiency
  level 0-5 selects per-language constraints from the profile, and the generated
  phrase is validated against them with at most one regeneration, then accepted
  as-is (soft degradation; samples logged for threshold tuning).

- **Windows audio**: TTS/playback uses `winsound` to bypass PortAudio/MME
  issues, with a `sounddevice` fallback elsewhere. A ~150 ms silence lead-in
  avoids clipping the first audio. `config.AUDIO_LOCK` serialises PortAudio
  init/teardown between the mic and speaker paths.

- **Talking mouth without an audio callback**: `winsound.PlaySound` plays the
  whole buffer with no per-frame hook, so the envelope is pre-computed from the
  waveform and replayed on the widget's own `after`-loop. Same path on all
  platforms, no live-RMS branch. Because the envelope uses the playback sample
  rate, the slowed-reference speed stretches the mouth track automatically.

- **Three llama-server flags are passed explicitly and must stay that way**
  (all three measured against the previous llama-cpp-python backend):
  `--ctx-size` (the default inflates the KV cache to the model's own 131072-token
  training context), `--parallel 1` (the default fragments the prefix cache the
  sliding-window prompt depends on) and `--cache-reuse 256`. Also `--api-key`
  (the server allows every CORS origin) and `--no-ui`.

- **Silent CPU fallback is the trap of this backend**: a CUDA build whose
  runtime DLLs are missing or of the wrong major version still logs `offloaded
  N/N layers to GPU`, still answers every request, and is about three times
  slower. `log_compute_devices()` runs a sub-second `--list-devices` probe
  before launch as the only startup evidence. Diagnostic only - a failed probe
  is logged and the server starts anyway.

- **espeak-ng is registered, not installed**: `ensure_espeak()` points
  `phonemizer` at the wheel's library with `set_library` **and**
  `set_data_path` - setting only the first dies inside the C library with an
  access violation. Both engines call it. Do **not** rely on misaki doing it as
  an import side effect: the TTS backends import lazily, so a Spanish run never
  imports Kokoro. `espeakng-loader` is pinned to a minor because its espeak-ng
  build **is** the reference transcription the calibration was fitted against.

## Testing

```bash
python -m unittest discover -s tests -v              # all fast unit tests, no model download
python tests/test_speech.py user.wav [ref.wav]       # optional end-to-end (loads the model)
```

Test files are named after the module they cover (`tests/test_paths.py` for
`mimora/paths.py`), so a module's tests are found by name rather than listed
here. The fast suite stubs torch, the OS, the architecture and every
subprocess where needed, so it downloads nothing and runs anywhere.

## Code Style (Python)

- No linting/formatting config - follow PEP 8.
- **Imports inside `mimora/` are absolute** (`from mimora import config`), never
  relative. `pronunciation/` is the deliberate exception and stays relative:
  those subpackages are reusable libraries that must keep working if the tree is
  vendored or renamed. Consequence for `model_fetch.py` and `gguf_fetch.py`:
  both can be run as plain files, which puts `mimora/` on `sys.path` rather than
  the project root, so each carries a shim that prepends the root.
- Type hints used throughout.
- Logging via `logging`: `%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s`.
- Explicit `RuntimeError` with a descriptive message for runtime validation, not
  `assert`.
- Library deprecation warnings are filtered in `mimora/bootstrap.py`.
- **Comments say what breaks if the code changes, not how the code got here.**
  Dates, release-candidate numbers, rejected hypotheses and step numbers from
  other files belong in git history and in the task notes, not in the source.

---

# AI Agent Collaboration Rules

This section is the behaviour contract for every AI Agent working on this
project. Read it in full before starting any task.

## 0. Project Information

- This project (secondary development): <https://github.com/abobot/Mimora>
- Upstream original project: <https://github.com/vikonix/Mimora/>
- This project does not aim for compatibility with or synchronisation to the
  upstream; it evolves as an independent project.

## 1. Mandatory at Task Start

1. Read `TODO.md` and find the current `-[doing]` task, or the highest-priority
   `-[todo]` task.
2. If no matching task exists, add one to `TODO.md` first (see §2).
3. Before starting work, change that task's status to `-[doing]`.

## 2. TODO.md Operation Rules

- File path: `TODO.md` at the project root.
- Timestamp format: `==YYYY-MM-DD HH:mm==`
- Status markers (must be used strictly):
  - `-[todo]` — to-do
  - `-[doing]` — in progress (only 1 allowed at a time)
  - `-[done]` — done (the line must end with a completion time
    `(YYYY-MM-DD HH:mm)`)
- Ordering: newest timestamp block on top, older below (descending).
- When adding a task, create a new timestamp block on top; when only changing
  status, edit in place.
- Write the file back immediately after any status change; do not batch
  changes up.

Example:

```
==2026-10-29 14:30==
 -[doing] Implement feature XXX
 -[todo] Fix issue YYY

==2026-10-28 09:00==
 -[done] Completed module AAA (2026-10-28 18:00)
```

## 3. CHANGELOG.md Operation Rules

- File path: `CHANGELOG.md` at the project root.
- Follow Keep a Changelog; version numbers follow Semantic Versioning.
- Categories are limited to: Added / Changed / Fixed / Removed / Deprecated /
  Security.
- Trigger: when a `TODO.md` item is marked `-[done]` and the change is visible
  externally, it must be written to the CHANGELOG in the same pass.
- Unreleased content goes under the `## [Unreleased]` section.

Example:

```
## [Unreleased]
### Added
- Added feature XXX

## [1.1.0] - 2026-10-29
### Fixed
- Fixed defect YYY
```

## 4. Documentation Sync Check (before every commit)

After completing code changes, check each item and state the result in the PR
description:

- [ ] Does `AGENT.md` need updating? (when the rules change)
- [ ] Does `readme.md` need updating? (user-visible features / install / usage
      changes)
- [ ] Does `dav_readme.md` need updating? (DAV-related changes)
- [ ] Other related docs (config examples, API docs, etc.)

Rule: for any item that needs no update, write "checked, no update needed" in
the PR description.

## 5. Commits and Branches

- Branches: `feature/<name>` or `fix/<name>`, cut from `main`.
- Commit messages follow Conventional Commits:
  - `feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...` / `chore: ...`
- One feature/fix corresponds to one PR.

## 6. Acceptance and Upload (Important)

1. After the feature/fix branch is complete, **stop** and submit it to the user
   for acceptance.
2. Only after the user explicitly confirms it passes may you:
   - merge into `main`
   - push to <https://github.com/abobot/Mimora>
   - update `TODO.md` (to done) and `CHANGELOG.md`
3. Before the user confirms, do not merge or push. Keep the status `doing`.

## 7. Pre-Merge Checklist (confirm each item)

- [ ] `TODO.md` updated
- [ ] `CHANGELOG.md` updated (if externally visible)
- [ ] `AGENT.md` / `readme.md` / `dav_readme.md` checked
- [ ] Other related docs checked
- [ ] Commit message conforms to the convention
- [ ] Local self-test passed
- [ ] User acceptance passed

## 8. Rollback

- On a serious problem, `git revert` the offending commit.
- Record it in `CHANGELOG.md` under Fixed/Changed.
- Add a new fix task (`-[todo]`) in `TODO.md`.
