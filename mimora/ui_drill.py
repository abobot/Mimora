# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Drill mode UI: repeated practice of one target word or phoneme.

A Toplevel opened from the Weakness panel (or directly). The controller
hands it a :class:`DrillEngine`; each loop:

1. Ask the engine for the next phrase; show it large in the centre.
2. The user presses "Record" (or Space); the controller scores the
   take and calls back ``on_take_scored(score)``.
3. The engine records the score; progress is redrawn.
4. If the engine reports "done" (mastered or max attempts reached),
   show a report and let the user finish.

Only one drill runs at a time; opening a new drill closes the old one.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, Dict, List

from mimora import config, i18n, window_icon
from mimora.drill.engine import DrillEngine
from mimora.ui_hero import layout_cells_centered, parse_word_levels
from mimora.ui_stats import _CanvasChart
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
    make_icon_button,
)
import logging
import tkinter as tk
import ttkbootstrap as ttk


# Cell colour per word quality level - the same traffic light the hero card's
# word feedback uses, so a drill take reads the same way as the main view:
# green = right, grey = acceptable, red = needs work.
_LEVEL_COLORS = {
    "good": THEME["good"],
    "ok": THEME["text_dim"],
    "bad": THEME["bad"],
}


@dataclass(frozen=True)
class DrillCallbacks:
    """Typed view→controller contract for the drill mode."""
    next_phrase: Callable[[], str]
    previous_phrase: Callable[[], str]
    on_record_clicked: Callable[[], None]
    on_play_reference_clicked: Callable[[], None]
    on_play_recording_clicked: Callable[[], None]
    on_previous_clicked: Callable[[], None]
    on_skip_clicked: Callable[[], None]
    on_scored: Callable[[float], None]
    on_finish: Callable[[], None]
    on_cancel: Callable[[], None]
    is_auto_next: Callable[[], bool]
    on_auto_next_toggled: Callable[[bool], None]


class DrillView:
    """Toplevel that runs one drill session."""

    def __init__(self, root, callbacks: DrillCallbacks, engine: DrillEngine):
        self.root = root
        self._cb = callbacks
        self.engine = engine
        self._window = tk.Toplevel(root)
        self._window.title(i18n.tr("Drill · {value}", value=engine.target.get('value', '')))
        self._window.geometry("640x540")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        self._build_ui()
        self._advance_phrase()
        # Bring the drill window to the foreground. Tk places a new Toplevel
        # in the default stacking order, which can land behind the main window
        # when the parent is not the focused window (e.g. the user clicked a
        # button in a weakness Toplevel rather than the main window). Without
        # this, a "clicked Drill but nothing happened" symptom is possible.
        self._window.lift()
        self._window.focus_force()

    def exists(self) -> bool:
        try:
            return self._window.winfo_exists()
        except tk.TclError:
            return False

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self._window, bg=THEME["bg_main"])
        header.pack(fill=tk.X, padx=16, pady=10)
        target_type = self.engine.target.get("type", "")
        target_value = self.engine.target.get("value", "")
        if target_type == "word":
            title = i18n.tr("Word drill · {value}", value=target_value)
        elif target_type == "single_word":
            title = i18n.tr("Word drill · {value}", value=target_value)
        elif target_type == "phoneme":
            title = i18n.tr("Phoneme drill · {value}", value=target_value)
        else:
            title = i18n.tr("Custom drill · {value}", value=target_value)
        tk.Label(header, text=title,
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        ttk.Button(header, text=i18n.tr("Finish & report"),
                   command=self._on_finish).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(header, text=i18n.tr("Cancel"),
                   command=self._on_cancel).pack(side=tk.RIGHT)

        # Progress bar + status.
        progress = tk.Frame(self._window, bg=THEME["bg_main"])
        progress.pack(fill=tk.X, padx=16, pady=(0, 8))
        self._progress_var = tk.StringVar(
            value=i18n.tr("{current} / {total}  ·  {passed} passed",
                          current=0, total=10, passed=0))
        tk.Label(progress, textvariable=self._progress_var,
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(side=tk.LEFT)
        self._streak_var = tk.StringVar(value="")
        tk.Label(progress, textvariable=self._streak_var,
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["good"]
                 ).pack(side=tk.RIGHT)

        # Phrase area.
        self._phrase_frame = tk.Frame(self._window, bg=THEME["bg_panel"], bd=1,
                                      highlightbackground=THEME["border"],
                                      highlightthickness=1)
        self._phrase_frame.pack(fill=tk.BOTH, expand=True,
                                padx=16, pady=8)
        self._phrase_label = tk.Label(self._phrase_frame, text="",
                                      font=(FONT_FAMILY, 20, "bold"),
                                      bg=THEME["bg_panel"], fg=THEME["text_emph"],
                                      wraplength=520, justify=tk.CENTER)
        self._phrase_label.pack(pady=(32, 8))
        self._phrase_hint = tk.Label(self._phrase_frame, text="",
                                     font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                                     bg=THEME["bg_panel"], fg=THEME["text_dim"])
        self._phrase_hint.pack()

        # Pronunciation feedback: shows missed words and weak phonemes after
        # each take, similar to the main view's WORK ON section.
        self._feedback_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        self._feedback_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        self._feedback_label = tk.Label(
            self._feedback_frame, text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            bg=THEME["bg_main"], fg=THEME["text_dim"],
            wraplength=600, justify=tk.LEFT)
        self._feedback_label.pack(anchor=tk.W)
        # Colored per-word cells (one per phrase word, coloured by quality
        # level), laid out centered/wrapping like the hero card's word strip.
        # Hidden until a take is scored; rebuilt on every take.
        self._feedback_cells_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        self._feedback_cells: List[tk.Label] = []
        self._feedback_cells_layout_in_progress = False
        self._feedback_cells_frame.bind(
            "<Configure>", self._layout_feedback_cells)

        # Last score + mini chart.
        bottom = tk.Frame(self._window, bg=THEME["bg_main"])
        bottom.pack(fill=tk.X, padx=16, pady=8)
        self._score_label = tk.Label(bottom, text="—",
                                     font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                                     bg=THEME["bg_main"], fg=THEME["text_emph"])
        self._score_label.pack(side=tk.LEFT, padx=(0, 12))
        chart = _CanvasChart(bottom, width=220, height=70)
        chart.canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._mini_chart = chart

        # Buttons.
        controls = tk.Frame(self._window, bg=THEME["bg_main"])
        controls.pack(fill=tk.X, padx=16, pady=(0, 16))
        self._record_btn = make_icon_button(
            controls, i18n.tr("▶"), i18n.tr("Record (Space)"), self._on_record)
        self._record_btn.pack(side=tk.LEFT, padx=4)
        make_icon_button(
            controls, i18n.tr("🔊"), i18n.tr("Example"), self._on_play_reference
        ).pack(side=tk.LEFT, padx=4)
        self._play_rec_btn = make_icon_button(
            controls, i18n.tr("🎙"), i18n.tr("My recording"), self._on_play_recording)
        self._play_rec_btn.pack(side=tk.LEFT, padx=4)
        self._play_rec_btn.config(state=tk.DISABLED)
        make_icon_button(
            controls, i18n.tr("⏮"), i18n.tr("Previous"), self._on_previous
        ).pack(side=tk.LEFT, padx=4)
        make_icon_button(
            controls, i18n.tr("⏭"), i18n.tr("Next"), self._on_skip
        ).pack(side=tk.LEFT, padx=4)
        # Auto-next toggle.
        self._auto_next_var = tk.BooleanVar(value=self._cb.is_auto_next())
        ttk.Checkbutton(controls, text=i18n.tr("Auto-next phrase"),
                        variable=self._auto_next_var,
                        command=self._on_auto_next_toggled).pack(side=tk.LEFT, padx=8)
        # Space key.
        self._window.bind("<space>", lambda e: self._on_record())
        # Bind to the top-level so typing in the label doesn't swallow it.

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _advance_phrase(self) -> None:
        phrase = self._cb.next_phrase()
        self._phrase_label.configure(text=phrase)
        if not phrase:
            self._phrase_hint.configure(text=i18n.tr("(no phrase available)"))
        else:
            fallback = self.engine.fallback_message(tr=i18n.tr)
            if fallback:
                # The engine could not find any sentence containing the target
                # in the practice text, so it fell back to sample phrases. Say
                # so explicitly instead of silently showing unrelated sentences
                # (the "first line matches, rest random" confusion).
                self._phrase_hint.configure(text=fallback)
            else:
                self._phrase_hint.configure(text=i18n.tr("Record your attempt when ready."))

    def _on_record(self) -> None:
        self._cb.on_record_clicked()

    def _on_play_reference(self) -> None:
        """Play the reference audio for the current drill phrase."""
        self._cb.on_play_reference_clicked()

    def _on_play_recording(self) -> None:
        """Play back the most recent take's own recording."""
        self._cb.on_play_recording_clicked()

    def _on_auto_next_toggled(self) -> None:
        """Toggle auto-next phrase setting."""
        self._cb.on_auto_next_toggled(self._auto_next_var.get())

    def _on_skip(self) -> None:
        """Skip to the next phrase without recording (manual advance)."""
        self._advance_phrase()
        self._feedback_label.configure(text="")
        self._clear_feedback_cells()

    def _on_previous(self) -> None:
        """Go back to the previous phrase."""
        phrase = self._cb.previous_phrase()
        self._phrase_label.configure(text=phrase)
        self._phrase_hint.configure(text=i18n.tr("Previous phrase — press Record to retry."))
        self._feedback_label.configure(text="")
        self._clear_feedback_cells()

    def _on_cancel(self) -> None:
        self._cb.on_cancel()
        self._window.destroy()

    def _on_finish(self) -> None:
        outcome = self.engine.finish()
        self._show_report(outcome)
        self._cb.on_finish()

    def _on_close_requested(self) -> None:
        self._on_cancel()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def refresh_progress(self, last_score: float = -1.0,
                         details: Dict[str, Any] = None) -> None:
        """Called by the controller after each take is scored.

        ``details`` carries the engine's PronunciationResult (in ``details["result"]``)
        so we can show missed words and weak phonemes in the feedback area.
        """
        p = self.engine.progress()
        self._progress_var.set(
            i18n.tr("{current} / {total}  ·  {passed} passed",
                    current=p['current'], total=p['total'], passed=p['passed']))
        if p["streak"] >= 1:
            self._streak_var.set(
                i18n.tr("🔥 streak {streak}/{required}",
                        streak=p['streak'],
                        required=self.engine.mastered_streak_required))
        if last_score >= 0:
            color = THEME["good"] if last_score >= self.engine.pass_threshold \
                else THEME["bad"]
            self._score_label.configure(text=f"{last_score:.0f}", fg=color)
        self._mini_chart.draw_line(
            p["scores"], max_value=100.0,
            color=THEME["good"] if p.get("mastered") else THEME["text_emph"],
            label=i18n.tr("target ≥ {threshold:.0f}",
                          threshold=self.engine.pass_threshold))
        self._update_feedback(details)
        if p["current"] >= 1:
            self._play_rec_btn.configure(state=tk.NORMAL)

    def _update_feedback(self, details: Dict[str, Any]) -> None:
        """Render missed words and weak phonemes from the analysis result.

        The text line keeps the "❌ Words to fix ... | 🔊 Practice ..."
        summary; beneath it a strip of colored cells (one per phrase word,
        coloured by quality level) gives the same at-a-glance traffic light
        the hero card's word feedback shows.
        """
        # Clear any previous cells first (the strip is rebuilt every take).
        for child in self._feedback_cells:
            child.destroy()
        self._feedback_cells = []
        self._feedback_cells_frame.pack_forget()
        if not details:
            self._feedback_label.configure(text="")
            return
        result = details.get("result")
        if result is None:
            self._feedback_label.configure(text="")
            return

        parts: List[str] = []

        # Missed words: reference_words with level="bad" or correct=False.
        ref_words = getattr(result, "reference_words", None) or []
        missed: List[str] = []
        for w in ref_words:
            level = w.get("level")
            if level == "bad" or w.get("correct") is False:
                missed.append(w.get("word", ""))
        if missed:
            parts.append(i18n.tr("❌ Words to fix: ") + ", ".join(missed[:5]))

        # Weak phonemes with example words.
        weak = getattr(result, "weak_phonemes", None) or []
        phonemes: List[str] = []
        for entry in weak[:4]:
            ipa = entry.get("phoneme", "")
            from mimora.phoneme_examples import example_for
            example = example_for(ipa)
            if example:
                phonemes.append(i18n.tr("/{ipa}/ (as in '{example}')",
                                        ipa=ipa, example=example))
            else:
                phonemes.append(f"/{ipa}/")
        if phonemes:
            parts.append(i18n.tr("🔊 Practice: ") + ", ".join(phonemes))

        self._feedback_label.configure(text="  |  ".join(parts))

        # Build the colored cells: one per reference word, coloured by level.
        resolved = parse_word_levels(ref_words)
        if not resolved:
            return
        self._feedback_cells_frame.pack(fill=tk.X, padx=16, pady=(2, 4))
        for word, level in resolved:
            color = _LEVEL_COLORS.get(level, THEME["text_dim"])
            cell = tk.Label(
                self._feedback_cells_frame, text=word, fg=color,
                bg=THEME["bg_main"], font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                padx=6, pady=2, highlightthickness=1, highlightbackground=color)
            cell.place_forget()
            self._feedback_cells.append(cell)
        self._layout_feedback_cells()

    def _layout_feedback_cells(self, event=None) -> None:
        """Center/wrap the feedback cells (same layout as the hero strip)."""
        if self._feedback_cells_layout_in_progress:
            return
        self._feedback_cells_layout_in_progress = True
        try:
            height = layout_cells_centered(self._feedback_cells_frame,
                                           self._feedback_cells)
            if height:
                self._feedback_cells_frame.configure(height=height)
        finally:
            self._feedback_cells_layout_in_progress = False

    def _clear_feedback_cells(self) -> None:
        """Destroy the feedback cells and hide the strip (new phrase)."""
        for child in self._feedback_cells:
            child.destroy()
        self._feedback_cells = []
        self._feedback_cells_frame.pack_forget()

    def _show_report(self, outcome) -> None:
        text = (i18n.tr("Drill finished") + "\n\n"
                + i18n.tr("Target: {ttype} = {tvalue}",
                           ttype=outcome.target_type,
                           tvalue=outcome.target_value) + "\n"
                + i18n.tr("Attempts: {n}", n=outcome.total_attempts) + "\n"
                + i18n.tr("Passed: {n}", n=outcome.total_passed) + "\n"
                + i18n.tr("First score: {score:.0f}", score=outcome.first_score) + "\n"
                + i18n.tr("Last score:  {score:.0f}", score=outcome.last_score) + "\n"
                + i18n.tr("Average:     {score:.1f}", score=outcome.avg_score) + "\n\n"
                + (i18n.tr("Outcome: ✅ Mastered") if outcome.mastered
                   else i18n.tr("Outcome: 🔁 Try again next time")))
        messagebox.showinfo(i18n.tr("Drill report"), text, parent=self._window)
        self._window.destroy()
