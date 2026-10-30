# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Hero card for the Mimora view layer.

THE screen object ("one screen - one task"): the current phrase with its
translation, and the score row beneath them (big score digit, verdict, WORK ON
phoneme badges, the articulation face). Every word in the phrase is clickable
to hear it slowly; badges speak an example word for the phoneme.

The card is passive like the rest of the view: widget callbacks forward to the
two plain callables passed in at construction (``on_word_clicked``,
``on_sound_example``); the facade drives it through the public methods.
"""
import tkinter as tk
import tkinter.ttk as ttk
from typing import Callable, Optional

from mimora import config, i18n
from mimora.face_widget import FaceWidget
from mimora.phoneme_examples import example_for
from mimora.progress_widget import ProgressRing
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_PHRASE,
    FONT_SIZE_SCORE,
    FONT_SIZE_SUBTITLE,
    THEME,
    FlatButton,
    Tooltip,
)

# How long a single click waits before playing the word. A double-click fires
# two single-clicks first, so the playback is deferred just long enough for the
# double-click to cancel it and start a drill instead - without this the word
# would play twice before the drill window opened.
_DOUBLE_CLICK_DELAY_MS = 250

# Maximum wrapped lines the phrase Text grows to before it becomes scrollable.
# A very long generated phrase would otherwise push the score row (and the WORK
# ON badges) out of the card's visible height, clipping them off the bottom of
# the fixed-height window. Capping the Text and letting the user scroll it (the
# Text is disabled, but yview/mousewheel still work - see _on_phrase_wheel)
# keeps the badge row always visible no matter how long the phrase is.
_PHRASE_MAX_LINES = 8


def parse_word_levels(reference_words: list) -> list:
    """Resolve per-word quality from either engine's ``reference_words`` shape.

    Both engines report one entry per target-phrase word, in order, but with
    different keys: the phoneme engine fills a three-level ``level``
    ("good"/"ok"/"bad"); the acoustic engine fills only a boolean ``correct``.
    This normalises both into ``(word, level)`` pairs so the word-feedback strip
    can colour every word identically regardless of engine:

    * level in ("good", "ok", "bad") - taken verbatim (phoneme engine);
    * otherwise - "good" when ``correct`` is true, "bad" when false
      (acoustic engine; there is no "ok" band, a word is simply right or not).

    A word with neither a usable level nor a ``correct`` flag defaults to
    "good" (no evidence of a problem - do not flag it). Punctuation/case are
    kept exactly as the engine reported them.
    """
    resolved = []
    for word in reference_words or []:
        level = word.get("level")
        if level in ("good", "ok", "bad"):
            resolved.append((word.get("word", ""), level))
        else:
            resolved.append((word.get("word", ""),
                             "good" if word.get("correct", True) else "bad"))
    return resolved


class HeroCard:
    """The phrase + score card, including the talking face.

    Owns the phrase Text (word tags, miss underlines, hover), the translation
    label, the score column, the WORK ON badges, the interactive-feedback hint,
    the FaceWidget (left of the score row) and the ProgressRing (right of it:
    session average of per-phrase bests plus the current phrase's attempt
    dots). Built and packed into ``root`` at construction.
    """

    def __init__(self, root,
                 on_word_clicked: Callable[[str], None],
                 on_sound_example: Callable[[str], None],
                 on_word_drill_clicked: Callable[[str], None] = None,
                 on_word_single_drill_clicked: Callable[[str], None] = None):
        self.root = root
        self._on_word_clicked = on_word_clicked
        self._on_sound_example = on_sound_example
        self._on_word_drill_clicked = on_word_drill_clicked
        self._on_word_single_drill_clicked = on_word_single_drill_clicked
        # Pending single-click word playback, so a double-click can cancel it
        # (see _schedule_word_playback / _DOUBLE_CLICK_DELAY_MS).
        self._scheduled_word_after_id = None

        # Drawn on bg_card, the lightest of the three surfaces (bg_main <
        # bg_panel < bg_card), so the card visually leads the whole window.
        self.frame = tk.Frame(root, bg=THEME["bg_card"],
                              highlightthickness=1, highlightbackground=THEME["border"])
        self.frame.pack(side=tk.TOP, fill=tk.X, padx=24, pady=(10, 12))

        # Phrase: a read-only tk.Text (not a Label) so individual words can carry
        # tags - every word is clickable to hear it slowly (the "word" tag) and
        # the mispronounced ones also get the "miss" underline. Disabled +
        # takefocus=0 keeps it from swallowing the spacebar record toggle
        # (app.py gates hotkeys on a focused Text widget).
        # The phrase sits in a sub-frame so a scrollbar can appear beside it when
        # the wrapped content exceeds _PHRASE_MAX_LINES (see _fit_phrase_height).
        self.phrase_frame = tk.Frame(self.frame, bg=THEME["bg_card"])
        self.phrase_frame.pack(fill=tk.X, padx=14, pady=(22, 0))
        self.phrase_text = tk.Text(
            self.phrase_frame, font=(FONT_FAMILY, FONT_SIZE_PHRASE, "bold"),
            fg=THEME["phrase"], bg=THEME["bg_card"], wrap=tk.WORD, bd=0,
            highlightthickness=0, height=1, cursor="arrow", takefocus=0,
            padx=16, pady=0, yscrollcommand=self._on_phrase_yscroll)
        self.phrase_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Shown only while the phrase is scrollable (over the cap); packing it
        # keeps the card's width stable either way.
        self.phrase_scrollbar = ttk.Scrollbar(
            self.phrase_frame, orient=tk.VERTICAL,
            command=self.phrase_text.yview)
        self.phrase_scrollbar.pack_forget()
        # All content is inserted with the "center" tag: tk.Text has no
        # widget-level justify, only per-tag.
        self.phrase_text.tag_configure("center", justify=tk.CENTER)
        # "miss" marks a mispronounced word: a colored underline (kept subtle -
        # the word itself stays in the phrase color). underlinefg needs
        # Tk >= 8.6.6; older Tks fall back to painting the word itself red,
        # which keeps the signal without the separate underline color.
        try:
            self.phrase_text.tag_configure("miss", underline=True,
                                           underlinefg=THEME["bad"])
        except tk.TclError:
            self.phrase_text.tag_configure("miss", underline=True,
                                           foreground=THEME["bad"])
        # Every word carries the "word" tag: click any word (not only the
        # mispronounced ones) to hear it spoken slowly. "hover" highlights the
        # single word under the pointer - applied to just that range by
        # _on_phrase_motion, because tag options are per-tag and a shared
        # background could not target one word without lighting up the rest.
        self.phrase_text.tag_configure("hover", background=THEME["bg_accent_active"])
        self.phrase_text.tag_bind("word", "<Button-1>", self._on_phrase_word_clicked)
        # Double-click any word to start a single-word drill on it (bound on the
        # same "word" tag as the single-click playback). The single click is
        # deferred (see _DOUBLE_CLICK_DELAY_MS) so the double-click cancels the
        # playback instead of playing the word twice before the drill opens.
        if self._on_word_single_drill_clicked:
            self.phrase_text.tag_bind("word", "<Double-Button-1>",
                                       self._on_phrase_word_double_clicked)
        # Right-click a mispronounced word to start a drill on it.
        if self._on_word_drill_clicked:
            self.phrase_text.tag_bind("miss", "<Button-3>",
                                       self._on_miss_word_right_clicked)
        self.phrase_text.bind("<Motion>", self._on_phrase_motion)
        self.phrase_text.bind("<Leave>", self._clear_phrase_hover)
        # Native mouse selection stays enabled on purpose: dragging (or a double-
        # click) selects part of the phrase so it can be copied - the blue
        # partial-word highlight is that selection, not a bug. If it ever needs to
        # be suppressed (e.g. it interferes with the click-to-hear affordance),
        # block the selection-forming events without touching the single click:
        #     for seq in ("<B1-Motion>", "<Double-Button-1>", "<Triple-Button-1>"):
        #         self.phrase_text.bind(seq, lambda e: "break")
        # The Text is disabled between set_phrase calls; its height follows the
        # wrapped content (recomputed on every set_phrase and on resize), capped
        # at _PHRASE_MAX_LINES (see _fit_phrase_height). The mousewheel scrolls a
        # capped phrase: the disabled Text still honours yview, so the user can
        # read the rest of a long phrase without the score row moving. (Keyboard
        # Up/Down are deliberately not bound - the disabled, takefocus=0 Text can
        # never receive keyboard focus, so they would be dead code.)
        self.phrase_text.configure(state=tk.DISABLED)
        self.phrase_text.bind("<Configure>", lambda e: self._fit_phrase_height())
        self.phrase_text.bind("<MouseWheel>", self._on_phrase_wheel)
        # Right-click the phrase to copy it (whole phrase - a tk.Text has no
        # single "text" option like a Label).
        self._bind_copy_menu(self.phrase_text,
                             getter=lambda: self.phrase_text.get("1.0", "end-1c"))

        # Word-feedback strip: one coloured cell per target-phrase word, laid out
        # under the phrase (above the translation) so the user sees, in one glance,
        # which words were judged right (green), acceptable (grey) or wrong (red).
        # Rebuilt per scored take by set_word_feedback; hidden in the empty and
        # unscored states (reset_word_feedback). Each cell is clickable to hear
        # that word slowly (same on_word_clicked the phrase words use). Created
        # before set_phrase("-") below, which clears it via reset_word_feedback.
        self.word_feedback = tk.Frame(self.frame, bg=THEME["bg_card"])
        self._word_feedback_cells = []
        # Re-lay the cells out when the card resizes (a narrow card wraps the
        # cells onto more centered rows instead of clipping them - see
        # _layout_word_feedback). Guarded against re-entry by the flag.
        self._word_feedback_layout_in_progress = False
        self.word_feedback.bind("<Configure>", self._layout_word_feedback)

        # Placeholder until the first phrase arrives.
        self.set_phrase("-")

        # Translation inside the same card, right under the phrase: just dimmer,
        # smaller text - no separator needed. Packed/unpacked by
        # refresh_translation_ui() when a translation language is (de)selected;
        # "-" stands in until a translation arrives with the next phrase.
        self.translation_label = tk.Label(
            self.frame, text="-", font=(FONT_FAMILY, FONT_SIZE_SUBTITLE),
            fg=THEME["text_dim"], bg=THEME["bg_card"], wraplength=560,
            justify=tk.CENTER)
        # Wrap the translation to the label's real width so a long translation
        # wraps onto more lines instead of being clipped on both sides. A fixed
        # wraplength did not track the card width, so once the card was narrower
        # than that value the centred line overflowed and got cut off.
        self.translation_label.bind("<Configure>", self._wrap_translation)
        # Right-click the translation to copy it (independently of the phrase).
        self._bind_copy_menu(self.translation_label)
        self.refresh_translation_ui()

        # Bottom padding of the phrase block, then a 1px divider above the
        # score row (the card's only internal separator).
        tk.Frame(self.frame, height=18, bg=THEME["bg_card"]).pack(fill=tk.X)
        tk.Frame(self.frame, height=1, bg=THEME["border"]).pack(fill=tk.X)

        # Score row, left to right: [face] [SCORE column] [WORK ON badges]
        # [progress ring]. The face (verdict indicator) and the session-average
        # ring bracket the textual middle. Filled by the facade's show_feedback;
        # reset_score_row shows the empty state ("--" and "record to get a
        # score") until the first take.
        score_row = tk.Frame(self.frame, bg=THEME["bg_card"])
        score_row.pack(fill=tk.X, padx=20, pady=(12, 16))

        # Kept as an attribute so toggle_face can pack the face *before* it -
        # i.e. as the leftmost child of the row (see toggle_face).
        self.score_col = tk.Frame(score_row, bg=THEME["bg_card"])
        score_col = self.score_col
        score_col.pack(side=tk.LEFT)
        tk.Label(score_col, text=i18n.tr("SCORE"), font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
                 fg=THEME["text_dim"], bg=THEME["bg_card"]).pack()
        self.score_num_label = tk.Label(
            score_col, text="--", font=(FONT_FAMILY, FONT_SIZE_SCORE, "bold"),
            fg=THEME["text_dim"], bg=THEME["bg_card"])
        self.score_num_label.pack()
        self.score_verdict_label = tk.Label(
            score_col, text=i18n.tr("record to get a score"),
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=THEME["text_dim"], bg=THEME["bg_card"])
        self.score_verdict_label.pack()

        # Fluency axis line under the verdict: the blended fluency/stress score
        # with its tempo verdict ("natural" / "too slow" / "too fast"). Hidden
        # until a take with fluency scoring arrives (set_fluency); reset_score_row
        # clears it. Off by default - only filled when config.FLUENCY_ENABLED.
        self.fluency_label = tk.Label(
            score_col, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=THEME["text_dim"], bg=THEME["bg_card"])
        self.fluency_label.pack()
        self._fluency_shown = False

        # Shadowing axis line under the fluency line: the blended alignment score
        # with its delay verdict (how far ahead/behind the user tracked the
        # reference). Hidden until a take with shadowing arrives (set_shadowing);
        # reset_score_row clears it. Off by default - only filled when
        # config.SHADOWING_ENABLED.
        self.shadowing_label = tk.Label(
            score_col, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=THEME["text_dim"], bg=THEME["bg_card"])
        self.shadowing_label.pack()
        self._shadowing_shown = False

        # Session-average ring on the right of the row (mirrors the face on the
        # left), with the current phrase's attempt-dot column on its left flank.
        # Packed here - before the expanding WORK ON column below - so the
        # right edge is claimed first; otherwise the expand=True middle column
        # would swallow all the leftover space and leave the ring no room. Driven
        # by set_progress / clear_attempts; starts in the empty state
        # ("0 phrases", no fill, no dots).
        self.progress = ProgressRing(score_row, size=82, bg=THEME["bg_card"])
        self.progress.pack(side=tk.RIGHT, padx=(8, 0))

        # WORK ON: caption + a row of flat phoneme badges (top problem sounds).
        # The badges are rebuilt per result (set_badges); clicking one speaks an
        # example word.
        workon_col = tk.Frame(score_row, bg=THEME["bg_card"])
        workon_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(24, 10))
        self.workon_caption = tk.Label(
            workon_col, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
            fg=THEME["warn"], bg=THEME["bg_card"])
        self.workon_caption.pack(anchor=tk.W)
        self.badges_frame = tk.Frame(workon_col, bg=THEME["bg_card"])
        self.badges_frame.pack(anchor=tk.W, pady=(4, 0))
        # Hint under the badges: how to use the two interactive feedback
        # affordances. Packed only while a scored take is on the card (see
        # set_hint), so the empty/unscored states stay quiet.
        self.hint_label = tk.Label(
            workon_col,
            text=i18n.tr("Click a word or sound to hear it. Double-click a word to drill it."),
            font=(FONT_FAMILY, FONT_SIZE_CAPTION), fg=THEME["text_muted"],
            bg=THEME["bg_card"], justify=tk.LEFT, wraplength=320)

        # The face lives in the score row (verdict indicator + talking mouth).
        # Same single FaceWidget instance the controller drives via
        # face_play_levels/face_rest; ~78px as mocked up.
        self.face = FaceWidget(score_row, size=78, bg=THEME["bg_card"],
                               face_color=THEME["face"], face_outline=THEME["border"],
                               eye_color=THEME["eyes"], mouth_color=THEME["mouth"],
                               face_offset=0.05)
        self.face.set_expression(":)")  # waiting state
        # The face has no on-main control: its visibility is a settings value
        # ("Show articulation face"), mirrored into this var and applied by
        # toggle_face(). Not packed yet - the facade calls toggle_face() once
        # the whole window is built.
        self.show_face = tk.BooleanVar(value=config.SHOW_FACE)

    # ------------------------------------------------------------------
    # Phrase text
    # ------------------------------------------------------------------
    def set_phrase(self, text: str):
        """Replace the hero phrase and refit its height (every word clickable).

        The phrase is split into words so each carries the "word" tag and can be
        clicked to hear it spoken; no word is a miss yet. The "miss" underlines
        are added later per analysis result (see mark_miss_words). A new phrase
        also clears any previous word-feedback strip (it described the old take).
        """
        words = (text or "-").split() or ["-"]
        self.reset_word_feedback()
        self._render_phrase([(word, False) for word in words])

    def _render_phrase(self, tokens: list):
        """Render the hero phrase from ``(word, is_miss)`` pairs.

        Each word gets the "word" tag (clickable to hear it) and is centered; a
        miss word additionally gets the "miss" underline. The single spaces
        between words carry only "center", so gaps are neither clickable nor
        highlighted. The Text is disabled afterwards so it never steals the
        spacebar record toggle, and any stale hover highlight is dropped first.
        """
        self.phrase_text.configure(state=tk.NORMAL)
        self.phrase_text.tag_remove("hover", "1.0", tk.END)
        self.phrase_text.delete("1.0", tk.END)
        # Drop any pending single-click playback: the word it was scheduled for
        # no longer exists in the rendered phrase.
        self._cancel_scheduled_word_playback()
        last = len(tokens) - 1
        for i, (word, is_miss) in enumerate(tokens):
            tags = ("center", "word", "miss") if is_miss else ("center", "word")
            self.phrase_text.insert(tk.END, word, tags)
            if i < last:
                self.phrase_text.insert(tk.END, " ", ("center",))
        self.phrase_text.configure(state=tk.DISABLED)
        self._fit_phrase_height()

    def _fit_phrase_height(self):
        """Size the phrase Text (in lines) to its wrapped content, capped.

        A tk.Text does not auto-grow like a Label: its height is a fixed line
        count, so it is recomputed after every set_phrase and on width changes
        (the <Configure> binding). The height is capped at _PHRASE_MAX_LINES: a
        long phrase would otherwise push the score row (with the WORK ON badges)
        out of the card's visible height and get clipped by the fixed window.
        Over the cap the scrollbar is packed and the Text scrolls (see
        _on_phrase_wheel). No-op before the widget is laid out.
        """
        if self.phrase_text.winfo_width() <= 1:
            return
        # "update" makes Tk compute the line metrics first, so the display-line
        # count is accurate right after an insert; without it the answer can
        # lag one layout pass behind.
        lines = self.phrase_text.count("1.0", "end", "update", "displaylines")
        # Tkinter's Text.count may return the value tuple-wrapped.
        if isinstance(lines, tuple):
            lines = lines[0]
        lines = max(1, int(lines or 1))
        scrollable = lines > _PHRASE_MAX_LINES
        self.phrase_text.configure(height=min(lines, _PHRASE_MAX_LINES))
        if scrollable:
            if not self.phrase_scrollbar.winfo_ismapped():
                # Packed BEFORE the Text so the Text's expand=True cannot steal
                # the vertical space the scrollbar needs (pack allocates in
                # order; an expand=True fill=BOTH Text packed first leaves the
                # later-packed scrollbar 1px tall).
                self.phrase_scrollbar.pack(side=tk.RIGHT, fill=tk.Y,
                                           before=self.phrase_text)
        elif self.phrase_scrollbar.winfo_ismapped():
            self.phrase_scrollbar.pack_forget()
            # Restore the top of the phrase when it shrinks back under the cap,
            # so the next short phrase is not left scrolled down.
            self.phrase_text.yview_moveto(0.0)

    def _on_phrase_yscroll(self, first, last):
        """Text.yscrollcommand callback: drive the scrollbar's position.

        The Scrollbar needs an explicit set (instead of the default
        yscrollcommand=scrollbar.set pair) so it appears only when the phrase
        actually overflows the cap; while under the cap the fraction is always
        (0.0, 1.0) and the bar is hidden by _fit_phrase_height.
        """
        if self.phrase_scrollbar.winfo_exists():
            self.phrase_scrollbar.set(first, last)

    def _on_phrase_wheel(self, event):
        """Scroll the capped phrase with the mousewheel.

        The Text is disabled (it must never swallow the spacebar), but a
        disabled Text still honours yview, so scrolling works. Returns "break"
        so the event does not fall through to the window (and so a wheel over
        the phrase never moves a scrollable panel behind it).
        """
        # Only scroll when there is something to scroll (over the cap).
        if self.phrase_text.count("1.0", "end", "displaylines")[0] <= _PHRASE_MAX_LINES:
            return "break"
        delta = getattr(event, "delta", 0)
        if delta:
            # Windows/macOS: delta is ±120 per notch; Linux gives ±1 per line.
            direction = -1 if delta > 0 else 1
            if abs(delta) >= 120:
                # One mousewheel notch on Windows/macOS scrolls ~3 lines.
                direction *= 3
            self.phrase_text.yview_scroll(direction, "units")
        return "break"

    def mark_miss_words(self, reference_words: list) -> int:
        """Re-render the hero phrase, underlining the mispronounced words.

        Rebuilds the phrase from the engine's per-word breakdown so the "miss"
        tag lands on exactly the target tokens the engine judged: a word is a
        miss when its three-level ``level`` is "bad" (the phoneme engine) or,
        without a level, when ``correct`` is False (the acoustic engine) - "ok"
        words stay unmarked. With no breakdown the phrase is left as set (no
        underlines). The Text is disabled again afterwards so it never steals the
        spacebar record toggle. Returns the number of underlined words, so the
        caller can decide whether the interactive-feedback hint applies.
        """
        if not reference_words:
            return 0
        tokens = []
        misses = 0
        for word in reference_words:
            level = word.get("level")
            if level in ("good", "ok", "bad"):
                is_miss = level == "bad"
            else:
                is_miss = not word.get("correct", True)
            if is_miss:
                misses += 1
            tokens.append((word.get("word", ""), is_miss))
        self._render_phrase(tokens)
        return misses

    # ------------------------------------------------------------------
    # Word-feedback strip (per-word coloured cells under the phrase)
    # ------------------------------------------------------------------
    # Cell colours per quality level: good -> theme good, bad -> theme bad,
    # ok -> a muted grey between them (the phoneme engine's "acceptable but
    # not perfect" band). The cell text is the word itself, so the strip is
    # self-labelling even without a legend.
    _LEVEL_COLORS = {
        "good": THEME["good"],
        "ok": THEME["text_dim"],
        "bad": THEME["bad"],
    }

    def set_word_feedback(self, reference_words: list):
        """Rebuild the word-feedback strip from a scored take's breakdown.

        Renders one cell per target-phrase word (in order) coloured by its
        quality level (see :func:`parse_word_levels`), so a long phrase becomes
        an at-a-glance "traffic light" for every word: green = right, grey =
        acceptable, red = wrong. Clicking a cell hears that word slowly (the
        same ``on_word_clicked`` the phrase words use), and a hover tooltip
        names the word and its quality. An empty or absent breakdown hides the
        strip (nothing was judged to show).
        """
        for child in self._word_feedback_cells:
            child.destroy()
        self._word_feedback_cells = []
        resolved = parse_word_levels(reference_words)
        if not resolved:
            self.word_feedback.pack_forget()
            return
        # Packed between the phrase and the translation (translation, when
        # visible, packs itself under the phrase via refresh_translation_ui's
        # after=self.phrase_frame, so the strip must come first in the stacking
        # order - pack it before the translation would move it above the phrase).
        # after=self.phrase_frame keeps it directly under the phrase regardless
        # of whether the translation is currently packed. (The phrase Text now
        # lives in phrase_frame, its own sub-frame for the scrollbar - the strip
        # and translation are siblings of that frame, so "after" must name the
        # frame, not the Text.)
        self.word_feedback.pack(fill=tk.X, padx=26, pady=(6, 0),
                                after=self.phrase_frame)
        for word, level in resolved:
            color = self._LEVEL_COLORS.get(level, THEME["text_dim"])
            cell = tk.Label(
                self.word_feedback, text=word, fg=color, bg=THEME["bg_card"],
                font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                cursor="hand2", padx=6, pady=2,
                highlightthickness=1, highlightbackground=color)
            # Laid out by _layout_word_feedback (centered, wrapping rows), not
            # packed side=LEFT: a fixed single row would overflow a narrow card
            # and clip the trailing cells.
            cell.place_forget()
            # Click to hear this word slowly; bind via a default argument so
            # each cell keeps its own word (not the loop's last one).
            cell.bind("<Button-1>", lambda e, w=word: self._on_feedback_cell_clicked(w))
            quality = i18n.tr("good") if level == "good" else (
                i18n.tr("acceptable") if level == "ok" else i18n.tr("needs work"))
            Tooltip(cell, f"{word} - {quality}")
            self._word_feedback_cells.append(cell)
        # Lay the cells out once now (the <Configure> below fires on the first
        # resize anyway, but the strip may already be at its final width).
        self._layout_word_feedback()

    def _layout_word_feedback(self, event=None):
        """Lay the feedback cells out centered, wrapping onto more rows when the
        card is too narrow for one row.

        The cells are children of ``word_feedback`` (a ``fill=X`` frame), which
        would normally pack them side=LEFT into one fixed row - a narrow card
        then clips every cell past the right edge. Here they are placed by hand
        in centered rows (each row centered as a whole), wrapping to the next
        row when the running width would exceed the frame. This keeps every cell
        visible and centred no matter how wide the card is.
        """
        if self._word_feedback_layout_in_progress:
            return
        self._word_feedback_layout_in_progress = True
        try:
            width = self.word_feedback.winfo_width()
            if width <= 1 or not self._word_feedback_cells:
                return
            cells = self._word_feedback_cells
            row = []          # cells in the current row
            row_width = 0     # running width of the current row (incl. padding)
            rows = []
            # First pass: measure and split into rows. Each cell's requested
            # width includes its internal padding; the 6px right pad is added
            # per cell (the last in a row gets it too, which the centering
            # absorbs).
            for cell in cells:
                cw = cell.winfo_reqwidth() + 6
                if row and row_width + cw > width:
                    rows.append((row, row_width - 6))  # unwind last cell's pad
                    row, row_width = [], 0
                row.append(cell)
                row_width += cw
            if row:
                rows.append((row, row_width - 6))
            # Second pass: place each row centered on its own line.
            y = 0
            for row_cells, rw in rows:
                x = max(0, (width - rw) // 2)
                for cell in row_cells:
                    cell.place(x=x, y=y, anchor=tk.NW)
                    x += cell.winfo_reqwidth() + 6
                y += max(cell.winfo_reqheight() for cell in row_cells)
            # The frame is packed fill=X, which uses the widget's requested
            # height - but placed children do not contribute to it, so set it
            # explicitly to the total laid-out height.
            self.word_feedback.configure(height=y)
        finally:
            self._word_feedback_layout_in_progress = False

    def _on_feedback_cell_clicked(self, word: str):
        """Hear the word behind a feedback cell slowly (same as the phrase words).

        Returns focus to the window so the spacebar record toggle keeps working
        (app.py's global click handler skips Text widgets, but a cell is a
        Label and would otherwise swallow the spacebar via its own focus).
        """
        self.root.focus_set()
        if word:
            self._on_word_clicked(word)

    def reset_word_feedback(self):
        """Hide the word-feedback strip (empty / unscored / new phrase)."""
        self.word_feedback.pack_forget()
        for child in self._word_feedback_cells:
            child.destroy()
        self._word_feedback_cells = []

    def _phrase_word_at(self, event) -> str:
        """The cleaned phrase word under the pointer, or "" over a gap.

        Reads the exact "word" tag range at the event position (so in-word
        punctuation like apostrophes is not split the way a plain
        wordstart/wordend probe would) and strips surrounding punctuation.
        Returns "" when the pointer is on whitespace or outside any word.
        """
        index = self.phrase_text.index(f"@{event.x},{event.y}")
        if "word" not in self.phrase_text.tag_names(index):
            return ""
        tag_range = self.phrase_text.tag_prevrange("word", f"{index}+1c")
        if not tag_range:
            return ""
        return self.phrase_text.get(*tag_range).strip(".,!?;:\"'()").strip()

    def _on_phrase_word_clicked(self, event):
        """Defer speaking the clicked phrase word slowly (any word).

        The playback is delayed by ``_DOUBLE_CLICK_DELAY_MS`` so a double-click
        (which fires two single-clicks first) can cancel it and start a drill
        instead - without the delay the word would play twice before the drill
        opened. A single click therefore hears the word after a short pause; a
        double-click hears nothing and drills the word.
        """
        word = self._phrase_word_at(event)
        # Return focus to the window so the spacebar record toggle keeps
        # working (app.py's global click handler skips Text widgets).
        self.root.focus_set()
        if word:
            self._schedule_word_playback(word)

    def _schedule_word_playback(self, word: str):
        """Arrange for ``word`` to be played after the double-click window.

        Replaces any previously scheduled word (a click on another word within
        the window supersedes it). Stored as an ``after`` id so a double-click
        can cancel it via :meth:`_cancel_scheduled_word_playback`.
        """
        self._cancel_scheduled_word_playback()
        self._scheduled_word_after_id = self.root.after(
            _DOUBLE_CLICK_DELAY_MS, lambda: self._play_scheduled_word(word))

    def _play_scheduled_word(self, word: str):
        """Play a word that survived the double-click window (single click)."""
        self._scheduled_word_after_id = None
        if word:
            self._on_word_clicked(word)

    def _cancel_scheduled_word_playback(self):
        """Drop any pending single-click playback (double-click or new phrase)."""
        if self._scheduled_word_after_id is not None:
            try:
                self.root.after_cancel(self._scheduled_word_after_id)
            except tk.TclError:
                pass
            self._scheduled_word_after_id = None

    def _on_phrase_word_double_clicked(self, event):
        """Double-click a phrase word to start a single-word drill on it.

        Cancels the pending single-click playback (the word does not play) and
        starts a drill on the word alone via ``on_word_single_drill_clicked``
        (the word itself is the whole practice phrase - no surrounding
        sentence). Returning "break" suppresses the native double-click text
        selection, which would otherwise select the word while the drill window
        opens.
        """
        word = self._phrase_word_at(event)
        self.root.focus_set()
        self._cancel_scheduled_word_playback()
        if word:
            self._start_word_single_drill(word)
        return "break"

    def _start_word_single_drill(self, word: str):
        """Start a single-word drill on ``word`` (no-op without the callback)."""
        if word and self._on_word_single_drill_clicked:
            self._on_word_single_drill_clicked(word)

    def _on_miss_word_right_clicked(self, event):
        """Right-click a mispronounced word to start a drill on it."""
        word = self._phrase_word_at(event)
        self.root.focus_set()
        self._cancel_scheduled_word_playback()
        if word and self._on_word_drill_clicked:
            self._on_word_drill_clicked(word)

    def _on_phrase_motion(self, event):
        """Highlight the word under the pointer and show it is clickable.

        Moves the single-range "hover" highlight onto the word at the pointer
        and switches the cursor to a hand over words / an arrow over the gaps.
        """
        index = self.phrase_text.index(f"@{event.x},{event.y}")
        self.phrase_text.tag_remove("hover", "1.0", tk.END)
        if "word" in self.phrase_text.tag_names(index):
            tag_range = self.phrase_text.tag_prevrange("word", f"{index}+1c")
            if tag_range:
                self.phrase_text.tag_add("hover", *tag_range)
            self.phrase_text.configure(cursor="hand2")
        else:
            self.phrase_text.configure(cursor="arrow")

    def _clear_phrase_hover(self, _event=None):
        """Drop the hover highlight and restore the arrow cursor (pointer left)."""
        self.phrase_text.tag_remove("hover", "1.0", tk.END)
        self.phrase_text.configure(cursor="arrow")

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------
    def set_translation(self, text: str):
        """Set the translation panel text, falling back to '-' when empty.

        '-' marks "language is on, but no translation yet" (e.g. right after the
        language was switched - the translation arrives with the next phrase).
        """
        self.translation_label.config(text=text.strip() if text and text.strip() else "-")

    def _wrap_translation(self, event):
        """Keep the translation wraplength equal to the label's current width.

        Guarded against the feedback loop where changing wraplength alters the
        label's height, which fires <Configure> again: only the width matters
        here and it does not change on a height-only event, so re-setting the
        same value is skipped.
        """
        if int(self.translation_label.cget("wraplength")) != event.width:
            self.translation_label.config(wraplength=max(1, event.width))

    def refresh_translation_ui(self):
        """Show or hide the translation panel to match the current setting.

        Shows the panel whenever a language is selected - in both "Full phrase"
        and "Few words" modes (fragments are translated too). Safe to call
        repeatedly - it reconciles to config.TRANSLATION_LANGUAGE without side
        effects.
        """
        show = bool(config.TRANSLATION_LANGUAGE)
        packed = self.translation_label.winfo_manager() == "pack"
        if show and not packed:
            # Inside the hero card, right under the phrase (no separator - the
            # dimmer, smaller type is the whole distinction). Packed after
            # phrase_frame (the strip's and translation's sibling) - the phrase
            # Text itself lives in that frame, so "after" must name the frame.
            self.translation_label.pack(fill=tk.X, padx=26, pady=(10, 0),
                                        after=self.phrase_frame)
        elif not show and packed:
            self.translation_label.pack_forget()

    # ------------------------------------------------------------------
    # Score row
    # ------------------------------------------------------------------
    def set_score_row(self, number: str, verdict: str, color: str):
        """Fill the SCORE column: the big number and its verdict, both in the
        already-resolved verdict color (never green for a "needs work")."""
        self.score_num_label.configure(text=number, fg=color)
        self.score_verdict_label.configure(text=verdict, fg=color)

    def set_fluency(self, axis: float, speed_label: str):
        """Fill the fluency line under the verdict.

        ``axis`` is the 0-100 fluency score (blended by the host when
        ``config.FLUENCY_ENABLED``); ``speed_label`` is the tempo verdict
        ("natural" / "too slow" / "too fast", translated at display time).
        An axis below 0 (not computed) hides the line. The label is shown even
        when the weight is 0 (axis computed, not blended) so the user sees the
        fluency read-out without it affecting the mark.
        """
        if axis is None or axis < 0:
            if self._fluency_shown:
                self.fluency_label.configure(text="")
                self._fluency_shown = False
            return
        verdict = i18n.tr(speed_label) if speed_label else ""
        self.fluency_label.configure(
            text=i18n.tr("Fluency") + f" {axis:.0f}" + (f" · {verdict}" if verdict else ""),
            fg=THEME["accent"])
        self._fluency_shown = True

    def set_shadowing(self, axis: float, delay_sec: float, overlap: float):
        """Fill the shadowing line under the fluency line.

        ``axis`` is the 0-100 shadowing score (blended by the host when
        ``config.SHADOWING_ENABLED``); ``delay_sec`` is the alignment offset
        (seconds, positive = behind) and ``overlap`` the 0-1 tracked fraction.
        An axis below 0 (not computed) hides the line.
        """
        if axis is None or axis < 0:
            if self._shadowing_shown:
                self.shadowing_label.configure(text="")
                self._shadowing_shown = False
            return
        text = i18n.tr("Shadowing") + f" {axis:.0f}"
        if delay_sec:
            text += f" · {delay_sec:+.2f}s"
        if overlap:
            text += f" · {overlap:.0%}"
        self.shadowing_label.configure(text=text, fg=THEME["accent"])
        self._shadowing_shown = True

    def set_progress(self, value: Optional[float], maximum: float, count: int,
                     attempts: Optional[list[float]] = None):
        """Update the session-average ring: ``value`` out of ``maximum`` and the
        distinct-phrase ``count``. ``value=None`` shows the empty state.
        ``attempts`` (scores of the current phrase's takes, oldest first)
        refreshes the dot column beside the ring; None leaves the dots as
        they are."""
        self.progress.set_progress(value, maximum, count)
        if attempts is not None:
            self.progress.set_attempts(attempts)

    def clear_attempts(self):
        """Empty the ring's attempt-dot column (a new phrase has arrived;
        the session numbers on the ring itself are kept)."""
        self.progress.set_attempts([])

    def set_badges(self, phonemes: list[str]):
        """Rebuild the WORK ON badges from the top problem phonemes.

        An empty list clears the row and hides the caption (the empty state and
        clean takes show no badges). Each badge whose phoneme has a known example
        word gets a hover tooltip ("as in 'put'") and speaks that example at
        normal speed on click (via ``on_sound_example``). An unknown phoneme
        stays a plain, non-playing badge.
        """
        for child in self.badges_frame.winfo_children():
            child.destroy()
        self.workon_caption.configure(text=i18n.tr("WORK ON") if phonemes else "")
        for phoneme in phonemes:
            example = example_for(phoneme)
            badge = FlatButton(self.badges_frame, text=f"/{phoneme}/",
                              font=(FONT_FAMILY, FONT_SIZE_BODY),
                              bg=THEME["bg_accent"], fg=THEME["text_accent"],
                              activebackground=THEME["bg_button"],
                              activeforeground=THEME["text_button"],
                              bd=0, padx=12, pady=3, cursor="hand2",
                              highlightthickness=1,
                              highlightbackground=THEME["accent"])
            badge.pack(side=tk.LEFT, padx=(0, 7))
            if example:
                # Bind the current example per button via a default argument so
                # every badge keeps its own word (not the loop's last one).
                badge.configure(
                    command=lambda w=example: self._on_sound_example(w))
                Tooltip(badge, i18n.tr("as in '{example}'", example=example))
            else:
                badge.configure(cursor="arrow")   # nothing to play or name

    def set_hint(self, show: bool):
        """Show or hide the interactive-feedback hint under the badges.

        Shown only while a scored take is on the card and at least one of the
        affordances it describes (underlined words, sound badges) exists.
        """
        if show:
            self.hint_label.pack(anchor=tk.W, pady=(6, 0))
        else:
            self.hint_label.pack_forget()

    def reset_score_row(self):
        """Empty state: no take scored yet (or a new recording just started)."""
        self.score_num_label.configure(text="--", fg=THEME["text_dim"])
        self.score_verdict_label.configure(text=i18n.tr("record to get a score"),
                                           fg=THEME["text_dim"])
        self.set_badges([])
        self.set_hint(False)
        self.reset_word_feedback()
        self.set_fluency(-1.0, "")
        self.set_shadowing(-1.0, 0.0, 0.0)
        # Drop any underlines from the previous take (the phrase itself stays;
        # a new phrase is replaced wholesale by set_phrase).
        self.phrase_text.tag_remove("miss", "1.0", tk.END)

    # ------------------------------------------------------------------
    # Copy-to-clipboard (right-click menu on the phrase / translation)
    # ------------------------------------------------------------------
    def _bind_copy_menu(self, widget, getter: Optional[Callable[[], str]] = None):
        """Attach a right-click 'Copy' menu that copies the widget's text.

        ``getter`` returns the text to copy; it defaults to the widget's own
        "text" option (a Label). The phrase tk.Text passes an explicit getter
        (its whole content), since a Text has no such option. Phrase and
        translation get their own binding, so each is copied independently.
        """
        if getter is None:
            getter = lambda: widget.cget("text")
        widget.bind("<Button-3>", lambda event: self._show_copy_menu(event, getter))
        # Tk/aqua (macOS) reports the right mouse button as Button-2; on the
        # other platforms Button-2 is the middle button and stays unbound.
        if widget.tk.call("tk", "windowingsystem") == "aqua":
            widget.bind("<Button-2>",
                        lambda event: self._show_copy_menu(event, getter))

    def _show_copy_menu(self, event, getter: Callable[[], str]):
        """Pop up a one-item 'Copy' menu for the clicked widget, if it has text."""
        text = getter().strip()
        # Nothing useful to copy from an empty card or the "-" placeholder.
        if not text or text == "-":
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=i18n.tr("Copy"), command=lambda: self._copy_text(text))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_text(self, text: str):
        """Replace the clipboard contents with ``text``."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # ------------------------------------------------------------------
    # Face (visibility + the talking mouth the controller drives)
    # ------------------------------------------------------------------
    def set_show_face(self, flag: bool):
        self.show_face.set(bool(flag))

    def get_show_face(self) -> bool:
        return bool(self.show_face.get())

    def toggle_face(self):
        """Show/hide the face in the score row (the "Face" setting)."""
        self.root.focus_set()
        shown = self.face.winfo_manager() == "pack"
        if self.show_face.get() and not shown:
            # Leftmost child of the score row: packed side=LEFT *before* the
            # SCORE column so it sits to its left (the progress ring holds the
            # right edge). Without before=, packing it after the columns already
            # laid out would drop it to the right of them. anchor="n" pins the
            # face to the top of the row so its circle lines up with the progress
            # ring's circle: the ring frame is the row's tallest child (ring +
            # count label), so top-aligning matches the two discs without a
            # hard-coded offset.
            self.face.pack(side=tk.LEFT, padx=(0, 12), anchor=tk.N,
                           before=self.score_col)
        elif not self.show_face.get() and shown:
            self.face.pack_forget()

    def face_fps(self):
        """Frame rate of the talking-mouth animation, or None if no face is shown.

        Returns None while the face panel is hidden (the "Face" setting), not
        just when no face exists: the caller skips building the loudness track
        then, so a hidden face costs no per-playback work (envelope computation
        plus a 30 fps after-loop animating an invisible mouth).
        """
        if not self.get_show_face():
            return None
        return self.face.fps

    def face_play_levels(self, levels, fps):
        """Drive the talking mouth from a pre-computed loudness track."""
        self.face.play_levels(levels, fps=fps)

    def face_rest(self):
        """Close the talking mouth."""
        self.face.rest()
