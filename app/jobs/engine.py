"""One graphics card, one job at a time.

Every tab asks the Engine to run work instead of starting workers itself, so
the Create and Cover tabs cannot both load a 7 GB model at once. A job is a
worker name, a job dict, and callbacks; several jobs (takes) can be queued.

Two automatic recoveries live here because they help every tab. Out of
graphics memory: the same job (same seed) is retried once in Lowest memory
mode. The fast CUDA-graph path failing for another reason: retried once in
eager mode, which is slower but avoids the graph code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, Signal

from app.jobs.worker_proc import WorkerProcess

RETRY_HINTS = ("cuda graph", "cudagraph", "cudnn", "graphar", "flash", "cublas", "_scaled_mm")


@dataclass
class Job:
    worker: str
    data: dict
    on_stage: Callable[[dict], None] | None = None
    on_done: Callable[[dict], None] | None = None
    on_fail: Callable[[dict], None] | None = None
    on_start: Callable[[], None] | None = None
    on_retry: Callable[[str], None] | None = None
    retried: bool = False
    meta: dict = field(default_factory=dict)


class Engine(QObject):
    busy_changed = Signal(bool)
    log_line = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[Job] = []
        self.current: Job | None = None
        self.process: WorkerProcess | None = None
        self.last_log = ""

    @property
    def busy(self) -> bool:
        return self.current is not None

    def submit(self, job: Job) -> None:
        self.queue.append(job)
        if not self.busy:
            self._next()

    def cancel_all(self) -> None:
        """Stop the running job and forget the queued ones."""
        dropped, self.queue = self.queue, []
        for job in dropped:
            if job.on_fail:
                job.on_fail({"type": "error", "kind": "cancelled", "message": "Cancelled", "queued": True})
        if self.process:
            self.process.cancel()

    def _next(self) -> None:
        if not self.queue:
            if self.current is not None:
                self.current = None
                self.busy_changed.emit(False)
            return
        was_busy = self.busy
        self.current = self.queue.pop(0)
        if not was_busy:
            self.busy_changed.emit(True)
        self._launch(self.current)

    def _launch(self, job: Job) -> None:
        proc = WorkerProcess(job.worker, job.data, self)
        self.process = proc
        self.last_log = str(proc.log_file)
        if job.on_stage:
            proc.stage.connect(job.on_stage)
        proc.finished_ok.connect(lambda event, j=job: self._done(j, event))
        proc.failed.connect(lambda event, j=job: self._failed(j, event))
        proc.log_line.connect(self.log_line)
        if job.on_start:
            job.on_start()
        proc.start()

    def _done(self, job: Job, event: dict) -> None:
        if job.on_done:
            job.on_done(event)
        self._after(job)

    def _failed(self, job: Job, event: dict) -> None:
        message = (event.get("message") or "").lower()
        if (job.worker == "yue2" and event.get("kind") == "out_of_memory"
                and job.data.get("memory") != "low" and not job.meta.get("memory_retry")):
            # Same song, same seed - only slower. Worth it rather than failing.
            job.meta["memory_retry"] = True
            job.data = dict(job.data, memory="low")
            self.log_line.emit("Out of graphics memory - retrying in Lowest memory mode")
            if job.on_retry:
                job.on_retry("memory")
            self._relaunch(job)
            return
        if (job.worker == "yue2" and not job.retried and event.get("kind") in ("unknown", "crashed")
                and job.data.get("backend", "torch") == "torch" and any(h in message for h in RETRY_HINTS)):
            job.retried = True
            job.data = dict(job.data, backend="torch-eager")
            self.log_line.emit("Fast mode failed - retrying in safe (eager) mode")
            if job.on_retry:
                job.on_retry("eager")
            self._relaunch(job)
            return
        if job.on_fail:
            job.on_fail(event)
        self._after(job)

    def _relaunch(self, job: Job) -> None:
        _clear_folder(job.data.get("output_dir"))
        if self.process:
            self.process.deleteLater()
        self._launch(job)

    def _after(self, job: Job) -> None:
        if self.process:
            self.process.deleteLater()
            self.process = None
        if self.current is job:
            self._next()


def _clear_folder(folder) -> None:
    """A failed run leaves partial files; the worker wants an empty folder."""
    import shutil
    from pathlib import Path

    if folder and Path(folder).is_dir():
        for child in Path(folder).iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
