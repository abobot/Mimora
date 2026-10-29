# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Dictation mode UI: hear a spoken sentence, type what you heard, get scored.

A Toplevel opened from the header's dictation button. The controller hands it
a :class:`DictationEngine`; each loop:

1. The controller asks the engine for the next sentence, synthesizes it and
   plays it through the TTS backend. The sentence text is deliberately NOT
   shown - the user must write what they heard.
2. The user types one word per slot (the number of slots equals the sentence's
   word count) and presses "Check answer" (or Enter in the last slot).
3. The controller scores the typed text against the reference, records the
   attempt, and calls ``refresh_progress``.
4. If the engine reports "done" (mastered or max attempts reached), a report
   is shown and the session finishes.

Extra affordances:

* A "Hint" button shows the first letter of each reference word, so the user
  can get unstuck without revealing the whole sentence.
* A "Show sentence" button reveals the full reference sentence.
* The sentence source (practice text / custom text / built-in pool) can be
  changed mid-session via the source selector.

Only one dictation session runs at a time; opening a new one closes the old.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional

from mimora import config, i18n, window_icon
from mimora.dictation.engine import DictationEngine
from mimora.dictation.scoring import tokenize_words
from mimora.ui_stats import _CanvasChart
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SUBTITLE,
    THEME,
    make_icon_button,
)
import ttkbootstrap as ttk


# Source choices the dropdown offers. "built-in" maps to an empty source so the
# engine falls back to its pool; the English label is the canonical key, and
# the displayed label is localized at widget-build time (see _source_labels).
_SOURCE_CHOICES = [
    ("Practice text", "practice"),
    ("Custom text", "custom"),
    ("Built-in", "built-in"),
]


def _source_labels() -> List[str]:
    """The localized display labels for the source dropdown."""
    return [i18n.tr(label) for label, _ in _SOURCE_CHOICES]


def _source_key(label: str) -> str:
    """Map a (possibly localized) dropdown label back to its engine key."""
    for en, key in _SOURCE_CHOICES:
        if label == en or label == i18n.tr(en):
            return key
    return "practice"


@dataclass(frozen=True)
class DictationCallbacks:
    """Typed view→controller contract for the dictation mode."""
    next_phrase: Callable[[], str]
    previous_phrase: Callable[[], str]
    on_play_clicked: Callable[[], None]
    on_play_slow_clicked: Callable[[], None]
    on_submit: Callable[[str], None]
    on_previous_clicked: Callable[[], None]
    on_skip_clicked: Callable[[], None]
    on_finish: Callable[[], None]
    on_cancel: Callable[[], None]
    # Returns the current practice text (for the "Practice text" source).
    get_practice_text: Callable[[], str]
    # Apply a new source text ("" means fall back to the built-in pool).
    on_source_changed: Callable[[str], None]


class DictationView:
    """Toplevel that runs one dictation session."""

    def __init__(self, root, callbacks: DictationCallbacks,
                 engine: DictationEngine):
        self.root = root
        self._cb = callbacks
        self.engine = engine
        self._window = tk.Toplevel(root)
        self._window.title(i18n.tr("Dictation"))
        self._window.geometry("720x620")
        window_icon.set_app_user_model_id()
        window_icon.apply_window_icon(self._window)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        # Whether the reference answer is currently revealed (after a check).
        self._revealed = False
        # Whether the current sentence has been scored (blocks re-submission).
        self._submitted = False
        # The reference sentence for the current slot (hidden until revealed).
        self._current_reference = ""
        # One Entry per reference word; the slot count equals word count.
        self._slot_entries: List[tk.Entry] = []
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
        tk.Label(header, text=i18n.tr("Dictation"),
                 font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                 bg=THEME["bg_main"], fg=THEME["text_emph"]
                 ).pack(side=tk.LEFT)
        ttk.Button(header, text=i18n.tr("Finish & report"),
                   command=self._on_finish, takefocus=0).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(header, text=i18n.tr("Cancel"),
                   command=self._on_cancel, takefocus=0).pack(side=tk.RIGHT)

        # Source selector row (sentence source can be changed mid-session).
        source_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        source_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        tk.Label(source_frame, text=i18n.tr("Source:"),
                 font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(side=tk.LEFT)
        self._source_var = tk.StringVar(value=_source_labels()[0])
        self._source_cb = ttk.Combobox(source_frame, textvariable=self._source_var,
                                       values=_source_labels(), state="readonly",
                                       width=16, takefocus=0)
        self._source_cb.pack(side=tk.LEFT, padx=4)
        self._source_cb.bind("<<ComboboxSelected>>", self._on_source_selected)

        # Custom-text editor, shown only when "Custom text" is selected.
        self._custom_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        self._custom_text = tk.Text(self._custom_frame, height=3, wrap=tk.WORD,
                                    font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                                    bg=THEME["bg_panel"], fg=THEME["text_emph"],
                                    insertbackground=THEME["text_emph"],
                                    highlightbackground=THEME["border"],
                                    highlightthickness=1, relief=tk.FLAT)
        self._custom_text.pack(fill=tk.X, padx=16, pady=4)

        # Progress line.
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

        # Playback + hint/show area (the reference is never shown until checked).
        play_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        play_frame.pack(fill=tk.X, padx=16, pady=4)
        make_icon_button(
            play_frame, i18n.tr("🔊"), i18n.tr("Play reference"), self._on_play
        ).pack(side=tk.LEFT, padx=4)
        make_icon_button(
            play_frame, i18n.tr("🐢"), i18n.tr("Play reference slowly"), self._on_play_slow
        ).pack(side=tk.LEFT, padx=4)
        make_icon_button(
            play_frame, i18n.tr("💡"), i18n.tr("Hint"), self._on_hint
        ).pack(side=tk.LEFT, padx=4)
        make_icon_button(
            play_frame, i18n.tr("👁"), i18n.tr("Show sentence"), self._on_show_sentence
        ).pack(side=tk.LEFT, padx=4)
        self._listen_label = tk.Label(play_frame, text=i18n.tr("Listen, then type what you hear."),
                                      font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                                      bg=THEME["bg_main"], fg=THEME["text_dim"])
        self._listen_label.pack(side=tk.LEFT, padx=12)

        # Hint line (first letters of each word, shown by the Hint button).
        self._hint_label = tk.Label(self._window, text="",
                                    font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                                    bg=THEME["bg_main"], fg=THEME["text_dim"])
        self._hint_label.pack(fill=tk.X, padx=16)

        # The hidden answer area (revealed after a check or "Show sentence").
        self._answer_frame = tk.Frame(self._window, bg=THEME["bg_panel"], bd=1,
                                      highlightbackground=THEME["border"],
                                      highlightthickness=1)
        self._answer_frame.pack(fill=tk.X, padx=16, pady=6)
        self._answer_label = tk.Label(self._answer_frame, text="",
                                      font=(FONT_FAMILY, FONT_SIZE_SUBTITLE),
                                      bg=THEME["bg_panel"], fg=THEME["text_emph"],
                                      wraplength=640, justify=tk.CENTER)
        self._answer_label.pack(pady=6)

        # Word-slot input area: one Entry per reference word.
        input_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        input_frame.pack(fill=tk.X, padx=16, pady=4)
        tk.Label(input_frame, text=i18n.tr("Your answer (one word per slot):"),
                 font=(FONT_FAMILY, FONT_SIZE_BODY),
                 bg=THEME["bg_main"], fg=THEME["text_dim"]
                 ).pack(anchor=tk.W)
        self._slots_frame = tk.Frame(input_frame, bg=THEME["bg_main"])
        self._slots_frame.pack(fill=tk.X, pady=(4, 4))

        # Feedback area (word-level diagnostics after a check).
        self._feedback_frame = tk.Frame(self._window, bg=THEME["bg_main"])
        self._feedback_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        self._feedback_label = tk.Label(
            self._feedback_frame, text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            bg=THEME["bg_main"], fg=THEME["text_dim"],
            wraplength=660, justify=tk.LEFT)
        self._feedback_label.pack(anchor=tk.W)

        # Last score + mini chart.
        bottom = tk.Frame(self._window, bg=THEME["bg_main"])
        bottom.pack(fill=tk.X, padx=16, pady=8)
        self._score_label = tk.Label(bottom, text="—",
                                     font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                                     bg=THEME["bg_main"], fg=THEME["text_emph"])
        self._score_label.pack(side=tk.LEFT, padx=(0, 12))
        chart = _CanvasChart(bottom, width=260, height=70)
        chart.canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._mini_chart = chart

        # Controls.
        controls = tk.Frame(self._window, bg=THEME["bg_main"])
        controls.pack(fill=tk.X, padx=16, pady=(0, 16))
        self._check_btn = make_icon_button(
            controls, i18n.tr("✓"), i18n.tr("Check answer"), self._on_submit)
        self._check_btn.pack(side=tk.LEFT, padx=4)
        make_icon_button(
            controls, i18n.tr("⏮"), i18n.tr("Previous"), self._on_previous
        ).pack(side=tk.LEFT, padx=4)
        make_icon_button(
            controls, i18n.tr("⏭"), i18n.tr("Next"), self._on_skip
        ).pack(side=tk.LEFT, padx=4)

    # ------------------------------------------------------------------
    # Word slots
    # ------------------------------------------------------------------

    def _build_slots(self, reference: str) -> None:
        """Create one Entry per reference word, sized to the word length."""
        for child in self._slots_frame.winfo_children():
            child.destroy()
        self._slot_entries = []
        words = tokenize_words(reference)
        # Grid in rows of up to 8 slots so a long sentence wraps cleanly.
        cols = 8
        for i, word in enumerate(words):
            width = max(6, min(14, len(word) + 4))
            entry = tk.Entry(self._slots_frame, width=width,
                             font=(FONT_FAMILY, FONT_SIZE_BODY),
                             bg=THEME["bg_panel"], fg=THEME["text_emph"],
                             insertbackground=THEME["text_emph"],
                             highlightbackground=THEME["border"],
                             highlightthickness=1, relief=tk.FLAT)
            entry.grid(row=i // cols, column=i % cols, padx=3, pady=3, sticky="w")
            # Enter moves to the next slot, or submits on the last one.
            entry.bind("<Return>", self._make_slot_return(i, len(words)))
            entry.bind("<KP_Enter>", self._make_slot_return(i, len(words)))
            self._slot_entries.append(entry)
        if self._slot_entries:
            self._slot_entries[0].focus_set()

    def _make_slot_return(self, index: int, total: int):
        def handler(event):
            if self._submitted:
                # Already scored: Enter now advances to the next sentence
                # instead of re-submitting the same one (the counter would
                # increment without the sentence changing).
                self._advance_phrase()
            elif index + 1 < total:
                self._slot_entries[index + 1].focus_set()
            else:
                self._on_submit()
            return "break"
        return handler

    def _collect_slots_text(self) -> str:
        """Join the slot values into a single text string for scoring."""
        return " ".join(e.get().strip() for e in self._slot_entries)

    def _set_hint(self, reference: str) -> None:
        """Show the first letter of each reference word as a hint."""
        words = tokenize_words(reference)
        hint = "  ".join(word[0].upper() if word else "_" for word in words)
        self._hint_label.configure(text=i18n.tr("Hint: {hint}", hint=hint))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _refresh_progress_label(self) -> None:
        """Update the progress read-out from the engine (navigation-aware)."""
        p = self.engine.progress()
        self._progress_var.set(
            i18n.tr("{current} / {total}  ·  {passed} passed",
                    current=p['current'], total=p['total'], passed=p['passed']))

    def _advance_phrase(self) -> None:
        """Advance to the next sentence, clearing input and hiding the answer."""
        phrase = self._cb.next_phrase()
        self._current_reference = phrase
        self._revealed = False
        self._submitted = False
        self._answer_label.configure(text="")
        self._feedback_label.configure(text="")
        self._hint_label.configure(text="")
        self._listen_label.configure(text=i18n.tr("Listen, then type what you hear."))
        self._check_btn.configure(state=tk.NORMAL)
        self._build_slots(phrase)
        self._refresh_progress_label()

    def _on_play(self) -> None:
        self._cb.on_play_clicked()

    def _on_play_slow(self) -> None:
        self._cb.on_play_slow_clicked()

    def _on_hint(self) -> None:
        """Reveal only the first letter of each word (not the full sentence)."""
        if not self._revealed:
            self._set_hint(self._current_reference)

    def _on_show_sentence(self) -> None:
        """Reveal the full reference sentence before checking."""
        self.reveal_answer(self._current_reference)

    def _on_submit(self) -> None:
        if self._submitted:
            # Already scored this sentence; ignore repeat Enter / button
            # presses rather than re-scoring (counter would inflate).
            return
        self._submitted = True
        user_text = self._collect_slots_text()
        self._cb.on_submit(user_text)

    def _on_skip(self) -> None:
        """Skip to the next sentence without checking (manual advance)."""
        self._advance_phrase()

    def _on_previous(self) -> None:
        """Go back to the previous sentence."""
        phrase = self._cb.previous_phrase()
        self._current_reference = phrase
        self._revealed = False
        self._submitted = False
        self._answer_label.configure(text="")
        self._feedback_label.configure(text="")
        self._hint_label.configure(text="")
        self._listen_label.configure(text=i18n.tr("Previous sentence - type what you hear."))
        self._build_slots(phrase)
        self._refresh_progress_label()

    def _on_source_selected(self, event=None) -> None:
        """Apply the chosen sentence source to the engine."""
        label = self._source_var.get()
        key = _source_key(label)
        if key == "custom":
            self._custom_frame.pack(fill=tk.X, padx=16, pady=4)
            text = self._custom_text.get("1.0", tk.END).strip()
            if not text:
                self._listen_label.configure(
                    text=i18n.tr("Type your sentences above, then press Next."))
        else:
            self._custom_frame.pack_forget()
            text = self._cb.get_practice_text() if key == "practice" else ""
        self._cb.on_source_changed(text)

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

    def reveal_answer(self, reference: str) -> None:
        """Show the reference sentence (after a check or "Show sentence")."""
        self._revealed = True
        self._answer_label.configure(text=f"✔  {reference}")

    def refresh_progress(self, last_score: float = -1.0,
                         details: Optional[Dict[str, Any]] = None) -> None:
        """Called by the controller after each attempt is scored.

        ``details`` carries the ``compare_sentences`` result so we can show
        word-level diagnostics (correct / missed / extra / misspelled).
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
        # Lock the check button until the next sentence.
        self._check_btn.configure(state=tk.DISABLED)

    def _update_feedback(self, details: Optional[Dict[str, Any]]) -> None:
        """Render word-level diagnostics from the comparison result."""
        if not details:
            self._feedback_label.configure(text="")
            return
        status = details.get("status") or []
        extra = details.get("extra_words") or []
        parts: List[str] = []
        if status:
            correct = [s["word"] for s in status if s["state"] == "correct"]
            missed = [s["word"] for s in status if s["state"] == "missed"]
            wrong = [s["word"] for s in status if s["state"] == "wrong"]
            if correct:
                parts.append(i18n.tr("✅ ") + ", ".join(correct[:6]))
            if missed:
                parts.append(i18n.tr("❌ Missed: ") + ", ".join(missed[:6]))
            if wrong:
                parts.append(i18n.tr("✏️ Spelling: ") + ", ".join(wrong[:6]))
        if extra:
            parts.append(i18n.tr("➕ Extra: ") + ", ".join(extra[:6]))
        self._feedback_label.configure(text="  |  ".join(parts))

    def _show_report(self, outcome) -> None:
        wrong = outcome.wrong_words or []
        wrong_line = (i18n.tr("\n\nWords to review:\n") + ", ".join(wrong[:20])
                      + (" …" if len(wrong) > 20 else "")) if wrong else ""
        extra = outcome.extra_words or []
        extra_line = (i18n.tr("\nExtra words:\n") + ", ".join(extra[:12])
                      + (" …" if len(extra) > 12 else "")) if extra else ""
        text = (i18n.tr("Dictation finished") + "\n\n"
                + i18n.tr("Attempts: {n}", n=outcome.total_attempts) + "\n"
                + i18n.tr("Passed: {n}", n=outcome.total_passed) + "\n"
                + i18n.tr("First score: {score:.0f}", score=outcome.first_score) + "\n"
                + i18n.tr("Last score:  {score:.0f}", score=outcome.last_score) + "\n"
                + i18n.tr("Average:     {score:.1f}", score=outcome.avg_score) + "\n\n"
                + (i18n.tr("Outcome: ✅ Mastered") if outcome.mastered
                   else i18n.tr("Outcome: 🔁 Try again next time"))
                + wrong_line + extra_line)
        messagebox.showinfo(i18n.tr("Dictation report"), text,
                            parent=self._window)
        self._window.destroy()
