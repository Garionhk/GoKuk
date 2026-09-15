"""The protocol between the Gokuk window and a worker process.

Stdlib only: this file is imported by workers running inside the downloaded
Python runtimes, which have torch but not PySide6, and by the GUI, which has
PySide6 but not torch.

* The GUI writes the job to ``<job>.json`` and starts
  ``python.exe workers/<name>_worker.py <job>.json``.
* The worker writes **one JSON object per line** to stdout. Every line has a
  ``type``: ``hello``, ``stage``, ``summary``, ``done`` or ``error``.
* To cancel, the GUI creates ``<job>.cancel``. The worker checks for it at every
  safe point the engine offers, then exits.

Why a file and not stdin: on Windows, a thread blocked reading a pipe makes any
other call on that handle wait too, and libraries ask ``sys.stdin.isatty()``
during import. A stdin watcher thread froze the worker before torch finished
loading. Stdin is not used at all; the GUI gives the worker an empty one.

Anything a library prints on stdout would corrupt the stream, so workers call
:func:`claim_stdout` first: the real stdout is kept for events, and ``print``
is sent to stderr, which the GUI keeps as a log.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

_events = None
_lock = threading.Lock()
_cancel_file: Path | None = None
_cancel_checked = 0.0
_cancel_seen = False


def claim_stdout() -> None:
    """Keep stdout for protocol events only; everything else goes to stderr."""
    global _events
    if _events is not None:
        return
    _events = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def emit(event_type: str, /, **fields) -> None:
    """Write one event line. Thread-safe."""
    line = json.dumps({"type": event_type, **fields}, ensure_ascii=False)
    with _lock:
        stream = _events or sys.stdout
        stream.write(line + "\n")
        stream.flush()


def cancel_path(job_path: str | Path) -> Path:
    return Path(job_path).with_suffix(".cancel")


def read_job(argv: list[str] | None = None) -> dict:
    """Load the job named on the command line and arm the cancel check."""
    global _cancel_file
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        raise ValueError("Usage: worker.py <job.json>")
    path = Path(argv[0])
    _cancel_file = cancel_path(path)
    _cancel_file.unlink(missing_ok=True)
    return json.loads(path.read_text(encoding="utf-8"))


def cancelled() -> bool:
    """True once the GUI has asked to stop. Cheap enough to call per token."""
    global _cancel_checked, _cancel_seen
    if _cancel_seen or _cancel_file is None:
        return _cancel_seen
    now = time.monotonic()
    if now - _cancel_checked >= 0.2:
        _cancel_checked = now
        _cancel_seen = _cancel_file.exists()
    return _cancel_seen


def parse_line(line: str) -> dict | None:
    """GUI side: one stdout line -> event dict, or None for noise."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


def classify_error(message: str) -> str:
    """Map a raw failure to a short kind the GUI can explain in plain words."""
    low = message.lower()
    if "out of memory" in low or "outofmemory" in low:
        return "out_of_memory"
    if "driver" in low and ("cuda" in low or "nvidia" in low):
        return "driver"
    if "cuda is not available" in low or "found no nvidia" in low:
        return "no_gpu"
    if "no such file" in low or "not found" in low or "does not appear to have" in low:
        return "missing_files"
    if "cancel" in low:
        return "cancelled"
    return "unknown"
