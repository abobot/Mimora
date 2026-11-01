# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Model weights are not part of a release. They are downloaded on the first start
and stay in the user data directory across upgrades.

## [Unreleased]

### Added

- **Stats dashboard: dictation analysis tab.** The stats dashboard gains a
  fifth Dictation tab with four charts over the dictation sessions: the
  wrong-vs-extra word balance, the most-misspelled words, the first-to-last
  score slope per session and the mastery outcome donut. The aggregation lives
  in a new pure `mimora/dictation_analysis.py` that re-derives the per-word
  states from the take texts via the same `compare_sentences` alignment the
  live scoring uses, so older sessions that predate the stored word
  diagnostics are counted exactly as the user saw them.
- **Stats dashboard: full dictation analysis.** The dictation tab is expanded
  from four charts to the full analysis set: a KPI row (mastery rate, average
  attempts, average first-to-last gain, session count), the attempts histogram
  with the pass-rate curve, the three error kinds (missed / extra / spelling)
  stacked by week, the top-N wrong words, the first-to-last score dumbbell, the
  score distribution split by pass/fail, the word-by-week heatmap, the
  per-session attempt series and the mastery donut. `mimora/dictation_analysis.py`
  gains the corresponding pure aggregation functions, and the chart primitives
  gain stacked-bar, horizontal-bar, stacked-histogram, line-series, heatmap
  matrix, dumbbell and KPI-card painters. The error-type categories are the
  three distinct learning gaps (漏写 / 多写 / 拼错), and the analysis module
  documents the two data-semantics limits that shape every time-based chart
  (no per-take timestamps, so `ended_at` is the only time axis; outcome has
  exactly two categories).
- **Stats dashboard: drill analysis tab.** The stats dashboard gains a sixth
  Drill tab with the full drill analysis set: a KPI row (session count, mastery
  rate, phoneme average, stubborn targets), mastery by target type stacked by
  week, the progress dumbbell (first-to-last per session, coloured by type), the
  phoneme averages as horizontal bars, the attempts histogram and pass-rate
  curve per type, the investment-vs-outcome scatter, same-target progress over
  time, per-session score trajectories, the target-by-week heatmap, the audio
  coverage split and the mastery outcome donut. `mimora/drill_analysis.py` is a
  new pure aggregation module; it documents the data-semantics limits that
  shape the charts (no stored duration, so investment is measured in attempts;
  `word` and `single_word` targets are the same semantic target and are merged
  in per-target charts; `free_form` targets are excluded from them).
- **Stats dashboard: advance (cross-file) analysis tab.** The stats dashboard
  gains a seventh Advance tab that joins the four activity stores — practice
  takes, review pool, drill sessions and dictation sessions — into one view: a
  KPI row (practice takes, distinct error words, review pool size, drill and
  dictation session counts, audio coverage, mastered drills), the
  error-word → drill → mastery sankey, the weak-phoneme coverage bars, the
  drill-take origin split, the practice/drill/dictation activity-by-day stacked
  timeline, the audio-coverage matrix, the phrase-level funnel
  (practice → with errors → in review → drilled → passed) and the day-level
  activity co-occurrence correlations. A new pure
  `mimora/advance_analysis.py` holds the aggregation plus the phrase/word/IPA
  normalizers the cross-file joins need, and it documents the join gotchas
  (review.json is keyed by phrase text, drill take audio refs point to the
  practice take that produced the phrase, so a drill take is the practice take
  itself and there is no independent grade to compare). The chart primitives
  gain a sankey and a y=x-reference scatter painter.
- **UI redesign: semantic design tokens and theme contrast audit.** A new
  `mimora/ui_tokens.py` centralises every colour *meaning* that panels used to
  interpret independently (score bands, word quality levels, weakness statuses),
  so a score reads the same colour everywhere. It also gains
  `assert_contrast()`, a WCAG 2.x contrast audit that `app.run()` logs at
  startup; a theme whose muted text drops below AA is now a warning in the run
  instead of a silent regression. `mimora/ui_theme.py` adds the
  `FONT_SIZE_DISPLAY` step to the type scale, and a new test enforces the rule
  that every `font=(...)` tuple references a `FONT_SIZE_*` constant rather than
  a bare number.
- **UI redesign: shared feedback strip and number popup.** A new
  `mimora/ui_feedback_strip.py` (the traffic-light word strip behind the hero,
  drill and dictation feedback) and `mimora/ui_long_press_popup.py` (the
  -1/+1 number editor for the dictation attempt limit and streak target) replace
  the five per-panel copies of the same code.
- **UI redesign: stats dashboard split into tabs.** The stats dashboard now has
  Overview / Trends / Review pool / Sessions tabs, and its heatmap palette and
  chart grid/axis colours come from the theme schema instead of hardcoded hex.

### Changed

- **UI redesign: main window layout.** The status bar and the tip line are
  merged into one row (status left, tip right); each control in the row gets a
  caption under it; the hero card's word strip and translation live in a fixed
  detail region instead of fighting for position; the practice-text panel stays
  collapsed by default.
- **UI redesign: explicit interaction affordances.** The reference speaker now
  has a small ▾ button that opens the playback-speed menu (instead of a hidden
  long-press), the phrase-source LLM/Text choice is a visible segmented
  switcher in the practice-text panel header (instead of a long-press on Next),
  and a word in the phrase is heard on a single click with a right-click menu
  for drilling it. Button state is centralised in a `UIState` enum.
- **UI redesign: weakness panel.** The Words tab is a sortable, selectable
  `ttk.Treeview`, the phoneme grid reflows responsively to the window width,
  the range selector is a segmented button row, and the Overview shows at most
  three suggestions.

- **Stats dashboard: spaced-repetition pool analysis.** The stats dashboard
  (desktop and web) now analyses your Ebbinghaus review pool from
  config/review.json: KPI cards (total, due today, overdue, average interval
  stage, pass rate, stubborn phrases), the review-stage distribution (with an
  "unscored" column), the due-load curve over the next 60 days, seen-count vs
  last result, pool composition by source with pass rate, span vs interval
  stage, the average-stage history (from daily snapshots under
  config/review_history/) and the overdue list. Enabled only when spaced
  repetition is on and phrases are tracked.
- **Web companion — full desktop feature set.** The web frontend
  (http://127.0.0.1:8788) now mirrors the desktop app beyond the core practice
  loop: dictation mode (hear a sentence, type one word per slot, get scored),
  drill mode (repeated practice of one target word or phoneme), the weakness
  panel (error words / phonemes / suggestions), history browsing (sessions,
  drill and dictation sessions, per-take and per-session delete, JSON export),
  the stats dashboard (daily average, score distribution, 28-day activity
  heatmap, duration table, streak), an editable source text and a settings view
  (live-applied; restart-required keys reported, including the UI language
  en/zh). The practice read-out mirrors the desktop hero card: per-word
  traffic-light strip, WORK ON phoneme badges (click to hear an example word),
  fluency/shadowing axis lines, clickable phrase word spans (miss words
  underlined), attempt dots for the session, and history rows with trend arrow,
  error-word summary, a Play button for stored recordings and a Re-drill
  shortcut.
- **Web companion (experimental).** An optional FastAPI + Uvicorn web frontend
  (`pip install "mimora[web]"`, run `mimora-web` or
  `python -m mimora.web.server`, open http://127.0.0.1:8788) that reuses the
  existing engine / TTS / LLM / stats modules to expose the core practice loop
  over REST: generate a phrase, hear the reference, record with the browser
  mic, get scored, and see session statistics. Do not run it at the same time
  as the desktop app (they share the pronunciation models and the llama-server).
- **Spaced-repetition phrase review (Ebbinghaus).** Every phrase you practice —
  from the practice text or generated by the LLM — is now recorded in a review
  store (config/review.json). When a phrase's review interval is due, it is
  re-presented before a fresh one is generated, on the classic Ebbinghaus
  forgetting-curve schedule (1, 2, 4, 7, 15, 30, 60 days): a passing take
  lengthens the interval, a failing take brings the phrase back the next day.
  LLM-generated phrases form their own bank, so they are reviewed separately
  from practice-text sentences. Two new Settings toggles control this:
  **Random order from practice text** (pick a random sentence instead of
  cycling in order, when the phrase source is practice) and **Spaced repetition
  (Ebbinghaus)**. Both need a restart to take effect.
- **Drill prioritises weak words.** A drill session now puts sentences
  containing your weakest words (from the per-word error leader board) first,
  so the phrases you need most come before the rest of the practice text.
- **Stats dashboard: practice-activity heatmap and duration table.** The stats
  window now shows a calendar heatmap of your practice minutes over the last 28
  days (colour intensity = minutes that day) and a per-day practice-duration
  table (newest first). Exporting stats as JSON / CSV / PNG / PDF now works:
  the export file-name stamp was broken by a missing `datetime` import.
- **Weakness panel statistics range.** The pronunciation-weakness window has a
  **Range** selector (last 7 days / 2 weeks / 1 month / 2 months / last 30 / 100
  / 300 takes) that re-filters the word and phoneme weakness tables. The
  overview suggestion cards and the phoneme example words are translated for
  non-English UI languages, and the phoneme cards' Play / Drill buttons sit
  side by side.
- **Weakness panel drill left/right.** In the Overview, Words and Phonemes
  tabs, **left-clicking** a drill button starts a sentence drill, while
  **right-clicking** starts a single-word drill on that word (or example word).
- **Settings: Restart button always visible.** The Restart-now button in the
  Settings window is now always shown (greyed out when nothing needs a restart)
  to the left of Cancel, instead of appearing only when a restart-only setting
  changed.
- **Zero-score takes are not recorded.** A take that scores 0 (no recognisable
  audio, or a completely wrong attempt) is no longer saved to the session
  history or the stats — it is discarded with an informational log instead, so
  a silent recording does not pollute your statistics.
- **Drill prioritises weak words.** A drill session now puts sentences
  containing your weakest words (from the per-word error leader board) first,
  so the phrases you need most come before the rest of the practice text.
- **Stats dashboard: practice-activity heatmap and duration table.** The stats
  window now shows a calendar heatmap of your practice minutes over the last 28
  days (colour intensity = minutes that day) and a per-day practice-duration
  table (newest first). Exporting stats as JSON / CSV / PNG / PDF now works:
  the export file-name stamp was broken by a missing datetime import.
- **Weakness panel statistics range.** The pronunciation-weakness window has a
  **Range** selector (last 7 days / 2 weeks / 1 month / 2 months / last 30 / 100
  / 300 takes) that re-filters the word and phoneme weakness tables. The
  overview suggestion cards and the phoneme example words are translated for
  non-English UI languages, and the phoneme cards' Play / Drill buttons sit
  side by side.
- **Weakness panel drill left/right.** In the Overview, Words and Phonemes
  tabs, **left-clicking** a drill button starts a sentence drill, while
  **right-clicking** starts a single-word drill on that word (or example word).
- **Settings: Restart button always visible.** The Restart-now button in the
  Settings window is now always shown (greyed out when nothing needs a restart)
  to the left of Cancel, instead of appearing only when a restart-only setting
  changed.
- **Zero-score takes are not recorded.** A take that scores 0 (no recognisable
  audio, or a completely wrong attempt) is no longer saved to the session
  history or the stats — it is discarded with an informational log instead, so
  a silent recording does not pollute your statistics.
- **Playback speed and phrase source controls.** Long-pressing the 🔊 play
  button opens a popup with a speed slider (0.7×–1.4×, 0.05 steps); choosing a
  speed plays the reference at that rate immediately **and makes it the new
  default speed** (saved to settings, so the next phrase is generated and
  auto-played at it instead of reverting). Long-pressing the 🐢 slow button
  opens a popup that adjusts the slow-playback multiplier in ±0.1 steps and
  plays on confirm. Both use a **WSOLA** time-stretch (waveform-similarity
  overlap-add): it keeps the pitch and, unlike the old lower-sample-rate
  resample, preserves the natural timbre — a phase vocoder smears transients
  into a "dragged"/reverby tail when slowed, which WSOLA avoids. Long-pressing
  the ⏭ Next button opens a popup that switches the phrase source between the
  LLM and the practice text (the text mode cycles the practice sentences in
  order). The chosen source is saved to settings, so it survives a restart
  (there is also a Phrase source setting in the Settings window). These popups
  are non-modal: clicking anywhere outside one dismisses it, so they never
  leave the interface stuck.
- Double-clicking any word in the hero phrase now starts a single-word drill on
  that word: the word itself is the whole practice phrase (no surrounding
  sentence), so you repeat just that word. The drill runs in its own window and
  leaves the main sentence unchanged (scoring the drill take no longer re-renders
  the main hero card with the drilled word). The mispronounced-word right-click
  and the weakness panel still start the sentence-context word drill. A single
  click still hears the word, now deferred briefly so a double-click does not
  play it twice before the drill opens. The interactive hint now names the
  double-click affordance.
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
- **Dictation sentence order and Ebbinghaus review.** Two new Settings toggles
  under History & Stats shape a dictation session: **Random order for dictation
  sentences** (pick a random sentence instead of cycling in order, never
  repeating the previous one) and **Spaced repetition for dictation
  (Ebbinghaus)** (re-present sentences due on the 1/2/4/7/15/30/60-day
  forgetting curve first, and advance a sentence's schedule after each scored
  attempt, so one you wrote correctly appears less often). Both take effect on
  the next dictation session. Each take also records the **misspelled-only**
  subset of the wrong words (`spelling_errors`), so the finished report and the
  history browser can focus on the words the user actually wrote wrong rather
  than the ones they omitted.
- **Consecutive-pass target for dictation is now user-configurable.** The
  number of consecutive passes that marks a dictation session "mastered" (3 by
  default) can be changed by long-pressing the 🔥 streak label inside the
  dictation window, or in the settings window under Dictation. The choice is
  persisted (`dictation_streak_target`) and applied live to the running
  session, so it holds across sessions and restarts.
- **Activity heatmap is now GitHub-contributions style, per month.** The
  practice-activity heatmap in both the desktop stats dashboard and the web
  stats page now uses GitHub's contribution-calendar layout: one column per
  week, rows are the days of the week, and each day's practice time is coloured
  on GitHub's 5-level green scale (quantiles of the non-zero days; a day with
  any practice is never the empty grey). Empty days stay the dark neutral.
  Month labels sit above the columns where a new month starts and weekday
  labels (Mon/Wed/Fri) to the left; the whole block is centred in its area.
  Each cell now shows its day-of-month number like a calendar, and the desktop
  chart area is tall enough that the caption no longer overlaps the grid.
  Instead of a fixed "last 28 days" window the heatmap now shows **one
  calendar month at a time** and the ‹ Prev / Next › arrows (both platforms)
  move between months; the green scale is recomputed per month from that
  month's own practice days. Only the month's own days are drawn — cells that
  spill in from the adjacent month are left blank, so the calendar never shows
  another month's days. The layout now reads **like a wall calendar**: each week
  is one row and the days flow left-to-right Mon-Sun, with the weekday labels
  across the top and the month name above them, instead of GitHub's vertical
  week-columns.

### Fixed

- **Stats dashboard: bottom tables are no longer cut off.** The practice
  duration table and the recent-sessions list sat below the fixed 640px window
  with no way to reach them. The dashboard body now lives on a vertically
  scrollable canvas (header stays fixed), so every section is reachable.

- **Startup crash on Windows: a full OpenBLAS thread pool segfaulted the
  process.** With one OpenBLAS thread per core (16 on a 16-core machine) the
  Wav2Vec2 recognizer crashed `torch_cpu.dll` with a native access violation
  (0xC0000005) when its weights loaded in the same process that already loaded
  Kokoro, so Mimora vanished on launch with no traceback. `early_init()` now
  caps `OPENBLAS_NUM_THREADS` and `OMP_NUM_THREADS` to a modest value before
  numpy is imported (a floor only: a user who already set either variable
  keeps their choice), which makes the same load sequence complete normally.

- Closing a word drill no longer leaves the drill's recording in the main
  view's "play my recording": a take recorded inside the drill no longer
  overwrites the main loop's own last take, and the button state is re-synced
  when the drill closes.
- Dictation mode buttons no longer take keyboard focus (`takefocus=0`), so a
  space press while typing never re-activates the last clicked control and
  cannot advance or check a sentence unintentionally.
- Pressing Enter again in the last word slot after a sentence is scored no
  longer re-scores the same sentence (which inflated the completed counter
  without changing the sentence); it now advances to the next sentence.
- The session counter now tracks the current sentence position, so **Previous**
  moves it down by one and **Next** up by one (clamped at the first sentence),
  instead of only changing when a sentence is scored.
- The phoneme-card hints in the weakness panel now follow the UI language: in
  the English UI they render in English instead of the hardcoded Chinese
  interference note, so the panel no longer shows mixed-language text.
- The weak-word priority in a drill now matches whole words, not substrings: a
  single-letter weak word such as "i" no longer drags every sentence containing
  "this"/"think"/"with" to the front of the drill, which silently defeated the
  "weak words first" ordering.
- The Settings window's Restart button is now always clickable (not greyed out
  when nothing needs a restart), matching the user's expectation that a visible
  button should respond to a click.
- The Overview tab of the weakness panel now names the active statistics range
  in its heading ("Suggested next steps (based on ...)"), so choosing a range
  in the dropdown visibly updates the suggestions it summarises.
- The weakness-panel range dropdown is now bound to its variable, so selecting
  a range actually takes effect: previously the dropdown never updated the
  variable it read on selection, so the Overview heading stayed on the default
  range no matter what was picked.
- Long-pressing the dictation progress counter (e.g. "1 / 10") now opens the
  per-session limit popup again: the popup used a font size constant that was
  never imported, so the Tk callback raised and no popup appeared.
- The fast test suite no longer depends on the order in which Tk-root test
  classes run. A real ``tk.Tk()`` root is torn down at the end of each class and
  rebuilt fresh, dropping ttkbootstrap's process-wide Style singleton and its
  stale theme subscribers first; previously a class after the first Tk-root
  class could fail to build its root (``application has been destroyed``) and
  the whole suite reported spurious errors.
- A very long hero phrase no longer clips the WORK ON phoneme badges off the
  bottom of the card. The phrase Text now grows only up to a fixed number of
  wrapped lines and scrolls with the mousewheel beyond that (a scrollbar
  appears beside it), so the score row with the badges stays visible no matter
  how long the generated phrase is.
- The per-word feedback strip under the hero phrase is visible again. Moving
  the phrase Text into its own scrollable sub-frame broke the "pack after the
  phrase" placement of the strip and the translation (they are siblings of the
  frame, not of the Text), so both collapsed to zero height and disappeared;
  they now pack after the frame and render below the phrase as before.
- The word-feedback cells now wrap onto centred rows instead of a single
  side-packed row, so a narrow card no longer clips the trailing words; every
  cell stays visible and centred no matter how wide the card is.
- Wrapped rows of the word-feedback strip no longer sit flush against each
  other: each cell carries its own internal padding and border, so a zero row
  gap made two rows' borders touch and the wrapped lines looked cramped. The
  layout now advances a small explicit gap per row.
- Dictation mode now shows the same colour-coded word feedback as the hero
  card: after a check, one cell per reference word (green = correct, red =
  missed or spelling error, grey = extra words the user typed) appears under
  the summary line, centred and wrapping like the main view's strip.
- The drill window now shows the same colour-coded word feedback as the hero
  card: after a scored take, one cell per phrase word (green = good, grey =
  acceptable, red = needs work) appears under the "words to fix / practice"
  summary line, centred and wrapping like the main view's strip.

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
