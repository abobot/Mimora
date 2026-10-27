# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Model weights are not part of a release. They are downloaded on the first start
and stay in the user data directory across upgrades.

## [Unreleased]

### Added

- **Dictation mode** (the ✍ button in the header): hear a spoken sentence, type
  what you heard, and get scored word by word. One word per input slot (the slot
  count matches the sentence's word count); Enter advances to the next slot and
  submits on the last one. The score is the share of reference words matched,
  and the feedback line marks each word correct / missed / misspelled / extra.
  A session is a set of sentences from a configurable source, with a pass
  threshold and a sentence count (settings: `dictation_pass_threshold`,
  `dictation_max_attempts`); mastering requires the last three attempts to pass.
  Sessions are saved to `stats.json` and listed in the history browser with
  their outcome and words to review.
- A "Hint" button shows only the first letter of each reference word, so the
  user can get unstuck without revealing the whole sentence, and a "Show
  sentence" button reveals the full reference.
- The dictation sentence source can be changed mid-session: the current
  practice text, a custom text you type, or a built-in fallback pool.
- Wrong words (missed or misspelled) and extra words are collected per attempt
  and aggregated per session, so a finished report and the history browser can
  list the words to review.

### Fixed

- Dictation mode buttons no longer take keyboard focus (`takefocus=0`), so a
  space press while typing never re-activates the last clicked control and
  cannot advance or check a sentence unintentionally.
- Pressing Enter again in the last word slot after a sentence is scored no
  longer re-scores the same sentence (which inflated the completed counter
  without changing the sentence); it now advances to the next sentence.
- The session counter now tracks the current sentence position, so **Previous**
  moves it down by one and **Next** up by one (clamped at the first sentence),
  instead of only changing when a sentence is scored.

## [1.1.0] - 2026-08-23

Mimora becomes an installable application: one command to install, one word to
start, and it downloads what it needs on the first start after it asks. The
practice loop, both scoring engines, both languages and both voices are
unchanged.

### Added

- A `mimora` command, and `python -m mimora`. `uv tool install mimora --python
  3.12` is the recommended install.
- A first run window that names the exact volume and asks before downloading.
  Refusing the chat model sets `"llm_backend": "off"`; refusing the translator
  clears the translation language.
- Automatic downloads for every model and binary, each also its own command
  (`python -m mimora.model_fetch`, `.gguf_fetch`, `.llama_server_fetch`). The
  llama.cpp assets are checked against a sha256.
- A user data directory outside the package, so an upgrade keeps your models,
  settings and calibration. `MIMORA_HOME` moves it.
- espeak-ng as part of the install, from the `espeakng-loader` wheel. A system
  install is now only the fallback.
- A hardware probe (`python -m mimora.detect_hardware`) and a startup warning
  when a GPU is present but unused.
- `docs/installation-manual.md`, sixteen test files, and the release tools
  `check_wheel.py`, `check_tqdm_hook.py` and `preview_first_run.py`.

### Changed

- The LLM backend is the official llama.cpp `llama-server` binary, pinned per
  platform and installed by Mimora, with a device check before launch. It
  replaces the Python server in `llm_server/`.
- Dependencies moved into `pyproject.toml`. One list instead of a list and a
  copy.
- Python 3.11 and 3.12 only. Two dependencies publish no wheels above that and
  would need a C++ compiler.
- The application moved into the package (`mimora/app.py`), together with the
  practice texts and theme schemas, so the wheel carries them.
- `install.py` rewritten: native preflight checks, one confirmation per step,
  full log.
- Documentation reorganized into `AGENTS.md` and `docs/installation-manual.md`.

### Fixed

- The first run downloaded nothing on any platform: the progress bar stand-in
  was missing the class level lock huggingface_hub asks for.
- The first run failed on the chat model on Intel macOS, where the resolved
  huggingface_hub takes no progress hook.
- The default scoring engine did not load on Linux, because `torchaudio` was
  declared but never imported and resolved to a different CUDA series than
  torch. It is no longer a dependency.
- Intel macOS: a missing `click`, and an `llvmlite` build failure now avoided by
  capping `numba`.
- A machine with no usable GPU re-downloaded the GPU build of llama-server on
  every install run.
- A Mac below the pinned build's minimum macOS version is told so before the
  download rather than after it.

### Removed

- `llm_server/`, the Python LLM server.
- The root `requirements.txt`.
- `tools/check_wheels.py`, `smoke_test_llama.py` and `sweep_llama_versions.py`.

## [1.0.0] - 2026-07-21

First public release. Phoneme and acoustic scoring engines, English and Spanish,
Kokoro and Supertonic text to speech, offline NLLB translation, and a guided
`install.py` for a clone.

[1.1.0]: https://github.com/vikonix/Mimora/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vikonix/Mimora/releases/tag/v1.0.0
