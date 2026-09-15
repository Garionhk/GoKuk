"""Gokuk worker: transcribe one recording to a melody score with SheetSage2.

Runs inside ``runtime/sheetsage/python.exe`` - a separate runtime, because
SheetSage2 pins torch, transformers and numpy versions that conflict with
YuE2's. The score it writes (``score.abc``) is handed to the YuE2 worker with
``cot="melody"`` to make a cover.

Job fields::

    audio, output_dir, model_dir, mert_dir, melody_only (default False: chords too),
    vocal_only (default False), max_seconds (optional)
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _protocol as proto  # noqa: E402

#: Must equal EXPECTED_WORKER_VERSION in app/jobs/worker_proc.py.
WORKER_VERSION = 2

#: Vocal melody only, but still with chords so the score editor can show harmony.
VOCAL_PROMPTS = ("timestamp", "downbeat_meter", "structure", "key", "chord_full", "melody_vocal")
VOCAL_PROMPTS_NO_CHORDS = ("timestamp", "downbeat_meter", "structure", "key", "melody_vocal")


class _Stopped(InterruptedError):
    pass


def run(job: dict) -> int:
    import torch
    from transformers import AutoModel

    started = time.perf_counter()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    proto.emit("hello", worker="sheetsage", torch=torch.__version__,
               gpu=torch.cuda.get_device_name(0) if device == "cuda" else None)

    proto.emit("stage", id="load", label="Loading model", completed=0, total=None, unit=None,
               elapsed=0.0, status=None)
    model = AutoModel.from_pretrained(
        job["model_dir"], base_model_path=job["mert_dir"], trust_remote_code=True,
        local_files_only=True,
    ).eval().to(device)
    proto.emit("stage", id="load", label="Loading model", completed=1, total=1, unit=None,
               elapsed=round(time.perf_counter() - started, 2), status="completed")

    def progress(value: dict) -> None:
        if proto.cancelled():
            raise _Stopped("Cancelled during transcription")
        stage = value.get("stage", "other")
        proto.emit("stage", id=f"transcribe_{stage}", label=stage,
                   completed=value.get("window", 0), total=value.get("windows"),
                   unit="windows", tokens=value.get("tokens"),
                   elapsed=round(time.perf_counter() - started, 2), status=None)

    def transcribe(melody_only: bool):
        options = dict(output_dir=job["output_dir"], dtype="bf16" if device == "cuda" else "fp32",
                       melody_only=melody_only, progress=progress)
        if job.get("vocal_only"):
            options["prompts"] = VOCAL_PROMPTS_NO_CHORDS if melody_only else VOCAL_PROMPTS
        if job.get("max_seconds"):
            options["max_seconds"] = float(job["max_seconds"])
        try:
            result = model.transcribe(job["audio"], **options)
        except RuntimeError as error:
            partial = getattr(error, "result", None)
            if partial is None:
                raise
            result = partial
        return result

    # With chords first, so the score looks the same as a Create score (the chords
    # are removed later, when the cover is sung). SheetSage2's full score can fail
    # to notate where the melody-only one succeeds; then fall back rather than fail.
    melody_only = bool(job.get("melody_only", False))
    result = transcribe(melody_only)
    warnings = list(result.get("warnings", []))
    if not melody_only and (result.get("abc_error") or not result.get("abc")):
        print(f"full score failed ({result.get('abc_error')}); retrying melody only", file=sys.stderr)
        warnings.append("chords could not be written down; the score has the melody only")
        result = transcribe(True)
        warnings += list(result.get("warnings", []))

    score = Path(job["output_dir"]) / "score.abc"
    if result.get("abc_error") or not result.get("abc"):
        proto.emit("error", kind="no_score", message=str(result.get("abc_error") or "No score was produced"),
                   warnings=warnings)
        return 1
    proto.emit("done", dir=job["output_dir"], score=str(score), warnings=warnings,
               with_chords=bool(result.get("abc") and '"' in result["abc"].split("K:", 1)[-1]),
               seconds=round(time.perf_counter() - started, 1),
               peak_gpu_mib=result.get("peak_gpu_mib"))
    return 0


def main() -> int:
    proto.claim_stdout()
    try:
        return run(proto.read_job())
    except BaseException as exc:  # noqa: BLE001 - report everything to the window
        message = f"{type(exc).__name__}: {exc}"
        kind = "cancelled" if isinstance(exc, (InterruptedError, KeyboardInterrupt)) or proto.cancelled() \
            else proto.classify_error(message)
        traceback.print_exc()
        proto.emit("error", kind=kind, message=message)
        return 2 if kind == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
