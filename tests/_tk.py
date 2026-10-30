# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Shared helpers for Tk-root test classes.

The fast suite runs every test module in one process, and many classes build
a real ``tk.Tk()`` root in ``setUpClass``. Tkinter cannot reliably re-create
a root after a previous root was destroyed, and ttkbootstrap compounds it:
its ``Style`` is a process-wide singleton bound to the *first* interpreter it
was created on, so once that root is destroyed the cached ``Style`` still
points at the dead interpreter and any later ttkbootstrap widget build (a
``TrainerView``, a ``Combobox``, ...) raises
``TclError: can't invoke "tk" command: application has been destroyed``.

The single robust remedy is to drop ttkbootstrap's stale singleton before
building on a fresh root, so it rebuilds on the current interpreter. Use
:func:`make_root` / :func:`destroy_root` in every ``setUpClass`` /
``tearDownClass`` pair that owns a real root; the reset makes the ordering of
Tk-root test classes irrelevant instead of order-fragile.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional


def _reset_ttk_style() -> None:
    """Drop ttkbootstrap's process-wide Style singleton and stale subscribers.

    The Style singleton caches the interpreter it was created on, and
    ttkbootstrap's Publisher keeps subscribers bound to that interpreter's
    widgets. After that root is destroyed, a new root must not reuse either:
    the cached Style points at a dead interpreter, and theme_use() publishes
    to stale subscribers whose widgets are gone, raising
    ``TclError: can't invoke "tk" command: application has been destroyed``.
    Reset both unconditionally: rebuilding the style once per test class is
    negligible, and it never leaves a stale interpreter behind.
    """
    try:
        from ttkbootstrap.publisher import Publisher
        Publisher.clear_subscribers()
    except Exception:
        pass
    try:
        from ttkbootstrap.style import Style
        Style.instance = None
    except Exception:
        # ttkbootstrap may not be importable in an environment that does not
        # build ttk widgets; the reset is a best-effort test convenience.
        pass


def make_root() -> tk.Tk:
    """Create a withdrawn Tk root, clearing any stale ttkbootstrap Style."""
    _reset_ttk_style()
    root = tk.Tk()
    root.withdraw()
    return root


def destroy_root(root: Optional[tk.Tk]) -> None:
    """Destroy a root (if any) and clear the stale Style singleton."""
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    _reset_ttk_style()
