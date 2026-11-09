"""Lazy access to Tk, so that importing a panel never requires a display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class GuiUnavailable(RuntimeError):
    """Tk is not installed, or no display is reachable."""


@dataclass(frozen=True)
class TkToolkit:
    """The Tk modules a view needs, resolved once and passed down by injection."""

    tk: Any
    ttk: Any
    filedialog: Any


def load_toolkit() -> TkToolkit:
    """Import Tk on demand; raise GuiUnavailable with an actionable message instead."""
    try:
        import tkinter
        from tkinter import filedialog, ttk
    except ImportError as error:  # pragma: no cover - depends on the host packages
        raise GuiUnavailable(
            "tkinter is not available: install python3-tk "
            "(Debian/Ubuntu: sudo apt install python3-tk)"
        ) from error
    return TkToolkit(tk=tkinter, ttk=ttk, filedialog=filedialog)


class Ticker:
    """A self-rescheduling ``after`` loop that can be stopped.

    The panel repaints on a fixed period. Keeping the pending callback id here means
    ``stop()`` can cancel it before the window is destroyed; a bare ``after`` chain
    can otherwise fire once more against dead widgets on the way out.
    """

    def __init__(self, widget: Any, period_ms: int, callback) -> None:
        self.widget = widget
        self.period_ms = period_ms
        self.callback = callback
        self._pending: str | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._schedule()

    def _schedule(self) -> None:
        self._pending = self.widget.after(self.period_ms, self._fire)

    def _fire(self) -> None:
        self._pending = None
        if not self._running:
            return
        self.callback()
        if self._running:
            self._schedule()

    def stop(self) -> None:
        self._running = False
        if self._pending is not None:
            self.widget.after_cancel(self._pending)
            self._pending = None
