# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""The shared "long-press to change a number" popup.

The dictation window had two near-identical long-press popups (~150 lines
each): one for the per-session sentence limit, one for the consecutive-pass
streak target. They differed only in the trigger widget, the title, the
initial value, the clamp key and the Apply callback - every byte of the
popup/catcher construction was duplicated. This function is that shared copy.

It is a *function*, not a class: the popup is transient, non-modal and has no
identity once dismissed, so a stateless call is the right shape.
"""

import tkinter as tk
from typing import Callable, Optional

from mimora import i18n
from mimora.ui_theme import (
    FONT_FAMILY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SMALL,
    FONT_SIZE_SUBTITLE,
    THEME,
    FlatButton,
)


def open_number_popup(anchor_widget, title: str, value: int,
                      on_apply: Callable[[int], None],
                      minimum: int = 1, maximum: int = 100, step: int = 1,
                      parent: Optional[tk.Toplevel] = None) -> None:
    """Show a non-modal popup to adjust an integer between ``minimum`` and ``maximum``.

    Anchored just below ``anchor_widget``. The value is clamped on every change
    (so the +/- buttons can never walk it out of range) and the Apply button
    calls ``on_apply`` with the final value. Escape or an outside click
    dismisses without applying.

    ``parent`` is the window the popup belongs to (for ``transient``); defaults
    to the anchor widget's own root window.
    """
    if parent is None:
        parent = anchor_widget.winfo_toplevel()

    popup = tk.Toplevel(parent)
    catcher = tk.Toplevel(parent)
    popup.overrideredirect(True)
    catcher.overrideredirect(True)
    catcher.attributes("-alpha", 0.01)
    catcher.geometry(f"{parent.winfo_screenwidth()}x"
                     f"{parent.winfo_screenheight()}+0+0")
    catcher.lower()
    popup.configure(bg=THEME["bg_panel"], highlightthickness=1,
                    highlightbackground=THEME["border"])
    x = anchor_widget.winfo_rootx()
    y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
    popup.geometry(f"+{x}+{y}")
    popup.transient(parent)
    catcher.bind("<ButtonPress-1>", lambda e: popup.destroy())
    popup.bind("<Escape>", lambda e: popup.destroy())

    state = {"value": max(minimum, min(maximum, value))}

    def _clamp(raw: int) -> int:
        return max(minimum, min(maximum, raw))

    def _rebuild_label() -> None:
        value_label.configure(text=f"{state['value']}")

    tk.Label(popup, text=title, font=(FONT_FAMILY, FONT_SIZE_CAPTION),
             bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(
        side=tk.TOP, anchor=tk.W, padx=12, pady=(8, 2))
    value_label = tk.Label(popup, text=f"{state['value']}",
                           font=(FONT_FAMILY, FONT_SIZE_SUBTITLE, "bold"),
                           bg=THEME["bg_panel"], fg=THEME["text_emph"])
    value_label.pack(side=tk.TOP, pady=(0, 4))

    def _bump(delta: int) -> None:
        state["value"] = _clamp(state["value"] + delta)
        _rebuild_label()

    row = tk.Frame(popup, bg=THEME["bg_panel"])
    row.pack(side=tk.TOP, padx=12, pady=(0, 4))
    FlatButton(row, text="-1", command=lambda: _bump(-step),
               font=(FONT_FAMILY, FONT_SIZE_SMALL), bg=THEME["bg_button"],
               fg=THEME["text_button"],
               activebackground=THEME["bg_button_active"],
               activeforeground=THEME["text"],
               bd=0, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=2)
    FlatButton(row, text="+1", command=lambda: _bump(step),
               font=(FONT_FAMILY, FONT_SIZE_SMALL), bg=THEME["bg_button"],
               fg=THEME["text_button"],
               activebackground=THEME["bg_button_active"],
               activeforeground=THEME["text"],
               bd=0, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=2)

    def _apply() -> None:
        popup.destroy()
        on_apply(state["value"])

    FlatButton(popup, text=i18n.tr("Apply"), command=_apply,
               font=(FONT_FAMILY, FONT_SIZE_SMALL, "bold"), bg=THEME["bg_accent"],
               fg=THEME["text_accent"],
               activebackground=THEME["bg_accent_active"],
               activeforeground=THEME["text_bright"],
               bd=0, padx=14, pady=3, cursor="hand2").pack(
        side=tk.TOP, pady=(0, 8))
