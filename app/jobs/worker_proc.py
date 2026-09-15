"""Run a worker in its own runtime and turn its output into Qt signals.

The window never imports torch. Each song or transcription is one
``python.exe workers/<name>_worker.py <job>.json`` process, started with a
clean, portable environment: every cache and temp folder points inside the
Gokuk folder, user site-packages are ignored, and Python runs in UTF-8 mode
(YuE2 writes JSON with the default encoding, which is cp1252 on Windows and
cannot hold Chinese lyrics).

Cancel is a file (``<job>.cancel``) - see workers/_protocol.py for why - with a
hard kill as the fallback if the worker has not stopped a little later.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from app import paths

sys.path.insert(0, str(paths.workers_dir()))
import _protocol as proto  # noqa: E402  (stdlib-only module shared with the workers)

KILL_AFTER_MS = 20000

#: The worker scripts carry WORKER_VERSION. Copying a new Gokuk over an old folder
#: can leave old workers behind (it happened: new window, old worker, so the
#: memory mode the window chose was silently ignored). Refuse to mix versions.
EXPECTED_WORKER_VERSION = 2
_VERSION_RE = re.compile(r"^WORKER_VERSION\s*=\s*(\d+)", re.MULTILINE)


def worker_version(script: Path) -> int | None:
    try:
        match = _VERSION_RE.search(script.read_text(encoding="utf-8"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def portable_environment(extra_path: list[Path] | None = None) -> QProcessEnvironment:
    env = QProcessEnvironment.systemEnvironment()
    for key in list(env.keys()):
        if key.upper().startswith(("PYTHON", "VIRTUAL_ENV", "CONDA", "HF_", "TRANSFORMERS_", "TORCH_")):
            env.remove(key)
    root = paths.root()
    tmp = paths.cache_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    values = {
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "HF_HOME": str(paths.cache_dir() / "hf"),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TORCH_HOME": str(paths.cache_dir() / "torch"),
        "TRITON_CACHE_DIR": str(paths.cache_dir() / "triton"),
        "CUDA_CACHE_PATH": str(paths.cache_dir() / "cuda"),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "YUE2_KIT": str(root),
    }
    for key, value in values.items():
        env.insert(key, value)
    if extra_path:
        env.insert("PATH", os.pathsep.join([*map(str, extra_path), env.value("PATH")]))
    return env


class WorkerProcess(QObject):
    """One job in one process. Emits parsed protocol events."""

    event = Signal(dict)          # every protocol line
    stage = Signal(dict)          # type == stage
    finished_ok = Signal(dict)    # type == done
    failed = Signal(dict)         # type == error, or the process died without one
    log_line = Signal(str)        # stderr, for the log file / details view

    def __init__(self, worker: str, job: dict, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.job = job
        self.proc = QProcess(self)
        self._buffer = b""
        self._ended = False
        self._cancelling = False
        self.started_at = 0.0
        jobs = paths.cache_dir() / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        self.job_file = jobs / f"{time.strftime('%Y%m%d-%H%M%S')}-{worker}-{uuid.uuid4().hex[:6]}.json"
        logs = paths.logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
        self.log_file = logs / (self.job_file.stem + ".log")

    # --- control ---------------------------------------------------------
    def start(self) -> None:
        python = paths.yue2_python() if self.worker == "yue2" else paths.sheetsage_python()
        script = paths.workers_dir() / f"{self.worker}_worker.py"
        found = worker_version(script)
        if found != EXPECTED_WORKER_VERSION:
            self._ended = True
            QTimer.singleShot(0, lambda: self.failed.emit({
                "type": "error", "kind": "mixed_versions",
                "message": f"{script} is version {found}, this Gokuk needs version {EXPECTED_WORKER_VERSION}"}))
            return
        self.job_file.write_text(json.dumps(self.job, ensure_ascii=False, indent=1), encoding="utf-8")
        extra = [paths.ffmpeg_bin()] if self.worker == "sheetsage" else None
        self.proc.setProcessEnvironment(portable_environment(extra))
        self.proc.setWorkingDirectory(str(paths.root()))
        self.proc.setStandardInputFile(QProcess.nullDevice())
        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self.started_at = time.monotonic()
        self.proc.start(str(python), ["-u", str(script), str(self.job_file)])

    def cancel(self) -> None:
        if self._ended or self._cancelling:
            return
        self._cancelling = True
        try:
            proto.cancel_path(self.job_file).write_text("cancel")
        except OSError:
            pass
        QTimer.singleShot(KILL_AFTER_MS, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.proc.state() != QProcess.NotRunning:
            self.proc.kill()

    def running(self) -> bool:
        return self.proc.state() != QProcess.NotRunning

    # --- output ----------------------------------------------------------
    def _read_stdout(self) -> None:
        self._buffer += bytes(self.proc.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            event = proto.parse_line(raw.decode("utf-8", errors="replace"))
            if event is None:
                continue
            self.event.emit(event)
            kind = event["type"]
            if kind == "stage":
                self.stage.emit(event)
            elif kind == "done":
                self._ended = True
                self.finished_ok.emit(event)
            elif kind == "error":
                self._ended = True
                if self._cancelling:
                    event["kind"] = "cancelled"
                self.failed.emit(event)

    def _read_stderr(self) -> None:
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        for line in text.splitlines():
            if line.strip():
                self.log_line.emit(line)

    def _on_finished(self, code: int, _status) -> None:
        self._read_stdout()
        if not self._ended:
            self._ended = True
            kind = "cancelled" if self._cancelling else "crashed"
            self.failed.emit({"type": "error", "kind": kind,
                              "message": f"The engine stopped unexpectedly (exit code {code}).",
                              "log": str(self.log_file)})
        proto.cancel_path(self.job_file).unlink(missing_ok=True)

    def _on_error(self, error) -> None:
        if error == QProcess.FailedToStart and not self._ended:
            self._ended = True
            self.failed.emit({"type": "error", "kind": "missing_files",
                              "message": "The engine could not start - run Gokuk Setup to repair it."})
