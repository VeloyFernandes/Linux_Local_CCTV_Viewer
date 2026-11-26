"""Lifetime helper for background QThreads owned by short-lived dialogs.

A dialog that is closed while its worker is still running would destroy a
running QThread when the dialog is garbage-collected, which makes Qt abort
the whole process.  :func:`detach_worker` re-parents such a worker to nothing
and keeps a reference until it has finished on its own.
"""
from __future__ import annotations

import warnings

from PySide6.QtCore import QThread

_DETACHED: set[QThread] = set()


def _forget(thread: QThread) -> None:
    _DETACHED.discard(thread)


def detach_worker(thread: QThread | None,
                  signals: tuple[str, ...] = ()) -> None:
    """Let ``thread`` outlive its parent dialog until it finishes.

    ``signals`` names the worker's result signals; they are disconnected so
    nothing is delivered to the closed dialog.
    """
    if thread is None or not thread.isRunning():
        return
    with warnings.catch_warnings():
        # PySide warns when a signal had no connections — that's fine here
        warnings.simplefilter("ignore", RuntimeWarning)
        for name in signals + ("finished",):
            try:
                getattr(thread, name).disconnect()
            except (RuntimeError, TypeError, AttributeError):
                pass
    thread.setParent(None)
    _DETACHED.add(thread)
    thread.finished.connect(lambda t=thread: _forget(t))


def wait_detached(timeout_ms: int = 5000) -> None:
    """Give detached workers a chance to finish (called at shutdown)."""
    for thread in list(_DETACHED):
        thread.wait(timeout_ms)
    _DETACHED.clear()
