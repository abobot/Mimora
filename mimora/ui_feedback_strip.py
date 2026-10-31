# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""The shared coloured word-cell feedback strip.

One widget, three callers: the hero card's ``set_word_feedback``, the dictation
window's ``_update_feedback`` and the drill window's ``_update_feedback`` all
built the same strip (a tk.Label cell per word, coloured by quality, centered
and wrapping through ``layout_cells_centered``) with three slightly different
copies of the same 40 lines. This class is that copy.

The strip is a plain widget, not a panel: it owns its cells and their layout
but nothing about the window around it. ``render``/``clear`` are the whole
public surface; the click callback is attached at construction (``None`` makes
the strip purely visual, as in the drill and dictation windows) and the strip
owns a ``frame`` the caller packs into its own layout.
"""

import tkinter as tk
from typing import Callable, List, Optional, Tuple

from mimora import i18n
from mimora.ui_tokens import level_color
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_SUBTITLE,
    THEME,
    Tooltip,
)


def layout_cells_centered(frame, cells, row_gap: int = 6) -> int:
    """Place ``cells`` (Tk widgets) in centered, wrapping rows inside ``frame``.

    A row of side-packed cells would overflow a narrow frame and clip every
    cell past the right edge. This places each cell by hand in rows, centering
    each row as a whole and wrapping to the next row when the running width
    would exceed the frame width. Returns the total laid-out height (0 when the
    frame is not yet sized or there is nothing to place), so the caller can set
    the frame's height explicitly (placed children do not contribute to a
    frame's requested height). ``row_gap`` is the vertical gap between rows.

    Used by the FeedbackStrip (hero card, dictation window, drill window) so
    all three render identically. Lives here - not in ``ui_hero`` - because the
    strip is the only remaining consumer and putting it in ``ui_hero`` would
    create an import cycle (``ui_hero`` imports ``FeedbackStrip``).
    """
    width = frame.winfo_width()
    if width <= 1 or not cells:
        return 0
    row = []          # cells in the current row
    row_width = 0     # running width of the current row (incl. padding)
    rows = []
    # First pass: measure and split into rows. Each cell's requested width
    # includes its internal padding; the 6px right pad is added per cell (the
    # last in a row gets it too, which the centering absorbs).
    for cell in cells:
        cw = cell.winfo_reqwidth() + 6
        if row and row_width + cw > width:
            rows.append((row, row_width - 6))  # unwind last cell's pad
            row, row_width = [], 0
        row.append(cell)
        row_width += cw
    if row:
        rows.append((row, row_width - 6))
    # Second pass: place each row centered on its own line. The gap between
    # rows is explicit (each cell's requested height includes its border and
    # internal padding, so a zero advance makes two rows' borders touch and
    # the wrapped lines look cramped).
    y = 0
    for row_cells, rw in rows:
        x = max(0, (width - rw) // 2)
        for cell in row_cells:
            cell.place(x=x, y=y, anchor=tk.NW)
            x += cell.winfo_reqwidth() + 6
        y += max(cell.winfo_reqheight() for cell in row_cells) + row_gap
    return y


class FeedbackStrip:
    """A centred, wrapping row of per-word coloured cells.

    Input is a list of ``(word, level)`` pairs; the colour comes from
    :func:`mimora.ui_tokens.level_color`, so hero / drill / dictation always
    agree. The strip is packed/forgotten by the caller's own pack order - the
    widget only manages its own children and its own frame height.
    """

    def __init__(self, parent, on_word_clicked: Optional[Callable[[str], None]] = None,
                 bg: Optional[str] = None):
        self.frame = tk.Frame(parent, bg=bg or THEME["bg_main"])
        self._on_word_clicked = on_word_clicked
        self._bg = bg or THEME["bg_main"]
        self.cells: List[tk.Label] = []
        self._layout_in_progress = False
        # Reflow the strip when the host window is resized.
        self.frame.bind("<Configure>", self._layout)

    def render(self, words: List[Tuple[str, str]]) -> None:
        """Rebuild the strip from ``(word, level)`` pairs."""
        # Mutate in place (never rebind): the host panel may hold a live alias
        # to ``cells`` (the hero card exposes it as ``_word_feedback_cells``).
        for cell in self.cells:
            cell.destroy()
        self.cells.clear()
        for word, level in words:
            color = level_color(level)
            cell = tk.Label(
                self.frame, text=word, fg=color, bg=self._bg,
                font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                padx=6, pady=2, highlightthickness=1, highlightbackground=color)
            if self._on_word_clicked is not None:
                cell.configure(cursor="hand2")
                cell.bind("<Button-1>",
                          lambda e, w=word: self._on_word_clicked(w))
                quality = (i18n.tr("good") if level == "good" else
                           i18n.tr("acceptable") if level == "ok" else
                           i18n.tr("needs work"))
                Tooltip(cell, f"{word} - {quality}")
            cell.place_forget()
            self.cells.append(cell)
        self._layout()

    def clear(self) -> None:
        """Destroy all cells and collapse the strip to its empty height."""
        for cell in self.cells:
            cell.destroy()
        self.cells.clear()
        # Collapse to 1 (not 0): 0 is Tk's "auto" sentinel, and once a frame has
        # had a positive height, setting 0 back leaves winfo_reqheight() stuck at
        # the last positive value. 1 is the empty-frame floor and invisible here
        # because the strip's bg matches its host region (same color, no border).
        self.frame.configure(height=1)

    def _layout(self, event=None) -> None:
        """Center/wrap the cells, resizing the frame to the row heights."""
        if self._layout_in_progress:
            return
        self._layout_in_progress = True
        try:
            height = layout_cells_centered(self.frame, self.cells)
            # Collapse to 1 when there is nothing to show (same reason as in
            # clear() - the 0 sentinel lies once a positive height was set).
            self.frame.configure(height=height if height else 1)
        finally:
            self._layout_in_progress = False
