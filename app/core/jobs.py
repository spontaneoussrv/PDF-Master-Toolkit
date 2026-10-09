"""
Background job framework.

Every long-running operation in the app is a plain Python function with the
signature ``fn(ctx: JobContext, **kwargs)``. It reports progress through
``ctx`` and polls ``ctx.check_cancel()``. The UI never calls such a function
directly — it wraps it in a :class:`Job` and hands it to a QThreadPool, so the
window stays responsive and every operation is cancellable.

The core modules import nothing from PySide6 except through this file, which
keeps the PDF engines testable headlessly.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class Cancelled(Exception):
    """Raised inside a worker when the user cancels. Never shown as an error."""


class JobError(Exception):
    """A failure with a message already written for a human to read."""


# ---------------------------------------------------------------------------
# context handed to worker functions
# ---------------------------------------------------------------------------

class JobContext:
    """
    Progress + cancellation channel passed to every worker function.

    Worker code should call :meth:`progress` regularly (which also polls for
    cancellation) rather than sprinkling explicit ``check_cancel`` calls.
    """

    def __init__(self, emitter: "JobSignals", cancel_event: threading.Event,
                 pause_event: threading.Event | None = None):
        self._emitter = emitter
        self._cancel = cancel_event
        self._pause = pause_event
        self._last_emit = 0.0
        self._total = 0
        self._done = 0
        self._stage = ""

    # -- cancellation ------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        """Block while paused; raise :class:`Cancelled` if the user cancelled."""
        if self._pause is not None:
            while not self._pause.is_set():
                if self._cancel.is_set():
                    raise Cancelled()
                time.sleep(0.08)
        if self._cancel.is_set():
            raise Cancelled()

    # -- progress ----------------------------------------------------------
    def set_total(self, total: int, stage: str = "") -> None:
        self._total = max(0, int(total))
        self._done = 0
        if stage:
            self._stage = stage

    def stage(self, text: str) -> None:
        self._stage = text
        self._emit(force=True)

    def step(self, n: int = 1, detail: str = "") -> None:
        self._done += n
        self._emit(detail=detail)

    def progress(self, value: int | float, message: str = "") -> None:
        """Report an absolute 0-100 percentage."""
        self.check_cancel()
        pct = int(max(0, min(100, value)))
        self._emitter.progress.emit(pct, message or self._stage)
        self._last_emit = time.monotonic()

    def _emit(self, detail: str = "", force: bool = False) -> None:
        self.check_cancel()
        now = time.monotonic()
        # throttle to ~25 updates/sec so a 5000-page batch cannot flood the queue
        if not force and (now - self._last_emit) < 0.04:
            return
        self._last_emit = now
        pct = int(self._done * 100 / self._total) if self._total else 0
        msg = detail or self._stage
        if self._total:
            counter = f"Page {self._done} / {self._total}" if self._stage.lower().startswith(
                ("recognis", "recogniz", "render", "process", "ocr")) else f"{self._done} / {self._total}"
            msg = f"{msg} — {counter}" if msg else counter
        self._emitter.progress.emit(min(100, pct), msg)

    # -- logging -----------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self._emitter.log.emit(str(message), level)

    def warn(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def success(self, message: str) -> None:
        self.log(message, "success")

    # -- partial results ---------------------------------------------------
    def emit_item(self, payload: Any) -> None:
        """Push an intermediate result (e.g. one finished file in a batch)."""
        self._emitter.item.emit(payload)


# ---------------------------------------------------------------------------
# signals + runnable
# ---------------------------------------------------------------------------

class JobSignals(QObject):
    started = Signal()
    progress = Signal(int, str)          # percent, message
    log = Signal(str, str)               # message, level
    item = Signal(object)                # intermediate payload
    finished = Signal(object)            # result
    failed = Signal(str, str)            # message, traceback
    cancelled = Signal()
    done = Signal()                      # always fires last, success or not


class Job(QRunnable):
    """Runs ``fn(ctx, *args, **kwargs)`` on a worker thread."""

    def __init__(self, fn: Callable[..., Any], *args, name: str = "", **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.name = name or getattr(fn, "__name__", "job")
        self.signals = JobSignals()
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._pause.set()                # not paused
        self.ctx = JobContext(self.signals, self._cancel, self._pause)
        self.setAutoDelete(True)
        self.started_at = 0.0
        self.finished_at = 0.0

    # -- control -----------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()                # release a paused worker so it can exit

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause.is_set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at or time.monotonic()
        return int((end - self.started_at) * 1000) if self.started_at else 0

    # -- execution ---------------------------------------------------------
    @Slot()
    def run(self) -> None:
        self.started_at = time.monotonic()
        self.signals.started.emit()
        try:
            result = self.fn(self.ctx, *self.args, **self.kwargs)
        except Cancelled:
            self.finished_at = time.monotonic()
            self.signals.cancelled.emit()
        except JobError as e:
            self.finished_at = time.monotonic()
            self.signals.failed.emit(str(e), "")
        except Exception as e:                        # noqa: BLE001 - reported to UI
            self.finished_at = time.monotonic()
            tb = traceback.format_exc()
            self.signals.failed.emit(_friendly_error(e), tb)
        else:
            self.finished_at = time.monotonic()
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()


def _friendly_error(e: Exception) -> str:
    """Turn common library exceptions into something a user can act on."""
    text = str(e).strip() or e.__class__.__name__
    lowered = text.lower()
    if isinstance(e, PermissionError) or "permission denied" in lowered or "being used by another" in lowered:
        return ("The file is locked by another program. Close it in your PDF reader "
                "or Office and try again.")
    if isinstance(e, FileNotFoundError):
        return f"File not found: {text}"
    if "password" in lowered and ("encrypt" in lowered or "protect" in lowered or "authenticate" in lowered):
        return "This PDF is password protected. Enter the password and try again."
    if "no space left" in lowered or "disk full" in lowered:
        return "Not enough free disk space to write the output file."
    if "cannot open broken document" in lowered or "format error" in lowered:
        return "This PDF appears to be damaged. Try the Repair PDF tool first."
    if isinstance(e, MemoryError):
        return "Ran out of memory. Try processing fewer pages or lowering the DPI."
    return text


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------

@dataclass
class _Tracked:
    job: Job
    name: str
    started: float = field(default_factory=time.monotonic)


class JobManager(QObject):
    """
    App-wide thread pool. Owns every running Job so the window can refuse to
    close (or warn) while work is in flight, and can cancel everything on exit.
    """

    busy_changed = Signal(bool)
    active_changed = Signal(int)

    def __init__(self, max_threads: int = 0, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        if max_threads > 0:
            self.pool.setMaxThreadCount(max_threads)
        self._active: dict[int, _Tracked] = {}
        self._lock = threading.Lock()

    # -- submission --------------------------------------------------------
    def submit(self, job: Job) -> Job:
        key = id(job)
        with self._lock:
            self._active[key] = _Tracked(job, job.name)
        job.signals.done.connect(lambda k=key: self._retire(k))
        self._announce()
        self.pool.start(job)
        return job

    def run(self, fn: Callable, *args, name: str = "", **kwargs) -> Job:
        return self.submit(Job(fn, *args, name=name, **kwargs))

    def _retire(self, key: int) -> None:
        with self._lock:
            self._active.pop(key, None)
        self._announce()

    def _announce(self) -> None:
        n = self.active_count
        self.active_changed.emit(n)
        self.busy_changed.emit(n > 0)

    # -- state -------------------------------------------------------------
    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def is_busy(self) -> bool:
        return self.active_count > 0

    def active_names(self) -> list[str]:
        with self._lock:
            return [t.name for t in self._active.values()]

    # -- shutdown ----------------------------------------------------------
    def cancel_all(self) -> None:
        with self._lock:
            jobs = [t.job for t in self._active.values()]
        for j in jobs:
            j.cancel()

    def shutdown(self, wait_ms: int = 4000) -> None:
        self.cancel_all()
        self.pool.waitForDone(wait_ms)


# ---------------------------------------------------------------------------
# headless context for tests / CLI
# ---------------------------------------------------------------------------

class NullContext(JobContext):
    """A JobContext that needs no Qt event loop (used by tests and batch CLI)."""

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._cancel = threading.Event()
        self._pause = None
        self._last_emit = 0.0
        self._total = 0
        self._done = 0
        self._stage = ""
        self.messages: list[tuple[str, str]] = []

    def progress(self, value, message: str = "") -> None:  # type: ignore[override]
        if self._verbose:
            print(f"[{int(value):3d}%] {message}")

    def _emit(self, detail: str = "", force: bool = False) -> None:  # type: ignore[override]
        if self._verbose and detail:
            print(f"       {detail}")

    def log(self, message: str, level: str = "info") -> None:  # type: ignore[override]
        self.messages.append((level, str(message)))
        if self._verbose:
            print(f"  {level}: {message}")

    def emit_item(self, payload) -> None:  # type: ignore[override]
        pass
