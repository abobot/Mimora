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

from mimora import config, window_icon
from mimora.drill.engine import DrillEngine
from mimora.ui_stats import _CanvasChart
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
)
import ttkbootstrap as ttk


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
        self._window.title(f"Drill · {engine.target.get('value', '')}")
        self._window.geometry("640x540")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        self._build_ui()
        self._advance_phrase()

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
        title = f"{'Word' if target_type == 'word' else 'Phoneme' if target_type == 'phoneme' else 'Custom'} drill · {target_value}"
        tk.Label(header, text=title,
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        ttk.Button(header, text="Finish & report",
                   command=self._on_finish).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(header, text="Cancel",
                   command=self._on_cancel).pack(side=tk.RIGHT)

        # Progress bar + status.
        progress = tk.Frame(self._window, bg=THEME["bg_main"])
        progress.pack(fill=tk.X, padx=16, pady=(0, 8))
        self._progress_var = tk.StringVar(value="0 / 10  ·  0 passed")
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
        self._record_btn = ttk.Button(controls, text="▶  Record (Space)",
                                      command=self._on_record)
        self._record_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(controls, text="🔊 示范读",
                   command=self._on_play_reference).pack(side=tk.LEFT, padx=4)
        self._play_rec_btn = ttk.Button(controls, text="🎙 我的录音",
                                        command=self._on_play_recording,
                                        state="disabled")
        self._play_rec_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(controls, text="⏮  Previous",
                   command=self._on_previous).pack(side=tk.LEFT, padx=4)
        ttk.Button(controls, text="⏭  Next",
                   command=self._on_skip).pack(side=tk.LEFT, padx=4)
        # Auto-next toggle.
        self._auto_next_var = tk.BooleanVar(value=self._cb.is_auto_next())
        ttk.Checkbutton(controls, text="自动下一句",
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
            self._phrase_hint.configure(text="(no phrase available)")
        else:
            self._phrase_hint.configure(text="Record your attempt when ready.")

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

    def _on_previous(self) -> None:
        """Go back to the previous phrase."""
        phrase = self._cb.previous_phrase()
        self._phrase_label.configure(text=phrase)
        self._phrase_hint.configure(text="Previous phrase — press Record to retry.")
        self._feedback_label.configure(text="")

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
            f"{p['current']} / {p['total']}  ·  {p['passed']} passed")
        if p["streak"] >= 1:
            self._streak_var.set(
                f"🔥 streak {p['streak']}/{self.engine.mastered_streak_required}")
        if last_score >= 0:
            color = THEME["good"] if last_score >= self.engine.pass_threshold \
                else THEME["bad"]
            self._score_label.configure(text=f"{last_score:.0f}", fg=color)
        self._mini_chart.draw_line(
            p["scores"], max_value=100.0,
            color=THEME["good"] if p.get("mastered") else THEME["text_emph"],
            label=f"target ≥ {self.engine.pass_threshold:.0f}")
        self._update_feedback(details)
        if p["current"] >= 1:
            self._play_rec_btn.configure(state="normal")

    def _update_feedback(self, details: Dict[str, Any]) -> None:
        """Render missed words and weak phonemes from the analysis result."""
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
            parts.append("❌ Words to fix: " + ", ".join(missed[:5]))

        # Weak phonemes with example words.
        weak = getattr(result, "weak_phonemes", None) or []
        phonemes: List[str] = []
        for entry in weak[:4]:
            ipa = entry.get("phoneme", "")
            from mimora.phoneme_examples import example_for
            example = example_for(ipa)
            if example:
                phonemes.append(f"/{ipa}/ (as in '{example}')")
            else:
                phonemes.append(f"/{ipa}/")
        if phonemes:
            parts.append("🔊 Practice: " + ", ".join(phonemes))

        self._feedback_label.configure(text="  |  ".join(parts))

    def _show_report(self, outcome) -> None:
        text = (f"Drill finished\n\n"
                f"Target: {outcome.target_type} = {outcome.target_value}\n"
                f"Attempts: {outcome.total_attempts}\n"
                f"Passed: {outcome.total_passed}\n"
                f"First score: {outcome.first_score:.0f}\n"
                f"Last score:  {outcome.last_score:.0f}\n"
                f"Average:     {outcome.avg_score:.1f}\n\n"
                f"Outcome: {'✅ Mastered' if outcome.mastered else '🔁 Try again next time'}")
        messagebox.showinfo("Drill report", text, parent=self._window)
        self._window.destroy()
