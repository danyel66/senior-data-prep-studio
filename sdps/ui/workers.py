"""Run blocking callables on a background QThread and get results back on the UI thread.

    task = run_task(self, fn, on_result=..., on_error=..., on_progress=...)
    task.cancel()   # sets task.cancel_event; fn decides how to honour it

`fn(task)` receives the task, so it can call `task.report(fraction, message)` and check
`task.cancel_event`. Signals are delivered via queued connections, so slots run on the UI thread.
"""
from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class Task(QThread):
    progress = pyqtSignal(float, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)          # (message, traceback)

    def __init__(self, fn: Callable[["Task"], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()
        self._cancel_hooks: list[Callable[[], None]] = []

    def report(self, fraction: float, message: str = "") -> None:
        self.progress.emit(float(fraction), message)

    def on_cancel(self, hook: Callable[[], None]) -> None:
        """Register a callable invoked (from the UI thread) when cancel() is requested."""
        self._cancel_hooks.append(hook)

    def cancel(self) -> None:
        self.cancel_event.set()
        for hook in self._cancel_hooks:
            try:
                hook()
            except Exception:
                pass

    def run(self) -> None:  # executes on the worker thread
        try:
            result = self._fn(self)
        except BaseException as e:  # noqa: BLE001 - report everything to the UI
            self.failed.emit(str(e) or type(e).__name__, traceback.format_exc())
        else:
            self.succeeded.emit(result)


_live: set[Task] = set()   # keep Python refs alive until the thread finishes


def run_task(parent: QObject | None, fn: Callable[[Task], Any], *,
             on_result: Callable[[Any], None] | None = None,
             on_error: Callable[[str, str], None] | None = None,
             on_progress: Callable[[float, str], None] | None = None,
             on_finished: Callable[[], None] | None = None) -> Task:
    task = Task(fn, parent)
    if on_result:
        task.succeeded.connect(on_result)
    if on_error:
        task.failed.connect(on_error)
    if on_progress:
        task.progress.connect(on_progress)
    if on_finished:
        task.finished.connect(on_finished)
    _live.add(task)
    task.finished.connect(lambda: _live.discard(task))
    task.start()
    return task


def wait_for_all(timeout_ms: int = 10_000) -> None:
    """Block until running tasks finish (used on shutdown and in tests)."""
    for task in list(_live):
        task.cancel()
        task.wait(timeout_ms)
