from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    status = Signal(str)
    progress = Signal(int, int)  # current, total (total may be 0 when unknown)
    finished = Signal(object)  # result
    failed = Signal(str)  # error text


@dataclass(frozen=True)
class WorkerRequest:
    fn: Callable[..., Any]
    kwargs: dict[str, Any]


class Worker(QRunnable):
    def __init__(self, req: WorkerRequest):
        super().__init__()
        self.req = req
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.req.fn(
                **self.req.kwargs,
                on_status=self.signals.status.emit,
                on_progress=self.signals.progress.emit,
            )
            self.signals.finished.emit(result)
        except Exception:
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            self.signals.failed.emit(tb)
