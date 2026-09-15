"""Gokuk worker: one YuE2 song per process.

Runs inside ``runtime/yue2/python.exe``. The GUI never imports torch; it starts
this script, sends a job, and reads progress events (see ``_protocol.py``).
The process exits when the song is done, which is the only fully reliable way
to hand every byte of graphics memory back before the next job.

Job fields::

    style, lyrics, cot ("full"|"melody"|"off"), seed, abc (optional text),
    cfg_scale (optional), output_dir, stage ("audio"|"plan"),
    model_dir, vae_dir, memory ("fast"|"balanced"|"low"), backend, verify_hashes
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _protocol as proto  # noqa: E402

#: Must equal EXPECTED_WORKER_VERSION in app/jobs/worker_proc.py. Bump both whenever
#: the job fields or their meaning change (v2: "memory" modes replaced "budget_gib").
WORKER_VERSION = 2

#: Stage labels YuE2 uses, mapped to stable ids the GUI translates. Anything
#: not listed is passed through as ``other`` with its English label.
STAGE_IDS = {
    "Resolving model files": "resolve",
    "Verifying model files": "verify",
    "Loading model": "load",
    "Using provided score": "score",
    "Planning score": "plan",
    "Generating song": "sing",
    "Synthesizing audio": "synth",
    "Loading audio decoder": "decoder",
    "Decoding audio": "decode",
}


def patch_progress() -> None:
    """Send YuE2's own progress to the GUI instead of a terminal.

    ``yue2.progress.Progress`` is the only place stage and count information
    surfaces. The subclass keeps its bookkeeping but renders each change as a
    protocol event, throttled to four a second per stage so a fast token loop
    cannot flood the pipe.
    """
    from yue2 import pipeline, progress

    class EventProgress(progress.Progress):
        def __init__(self, enabled=True, stream=None, refresh_interval=0.25):
            super().__init__(enabled=enabled, stream=stream, refresh_interval=refresh_interval)
            self._tty = False
            self._interval = 0.25

        def _write(self, text, final=False):   # never touch a terminal
            return

        def _render(self, stage, now, status=None, force=False):
            if not self.enabled or (not force and status is None
                                    and now - stage._last_render < self._interval):
                return
            stage._last_render = now
            proto.emit(
                "stage",
                id=STAGE_IDS.get(stage.label, "other"),
                label=stage.label,
                completed=stage.completed,
                total=stage.total,
                unit=stage.unit,
                elapsed=round(max(0.0, now - stage._started), 2),
                status=status,
            )

        def complete(self, audio_seconds, elapsed, *, truncated=False):
            proto.emit("summary", audio_seconds=round(audio_seconds, 2),
                       elapsed=round(elapsed, 2), truncated=bool(truncated))

    pipeline.Progress = EventProgress
    progress.Progress = EventProgress


def flash_attention_works() -> bool:
    """Whether torch's built-in variable-length FlashAttention actually runs.

    YuE2's CUDA-graph decoder picks FlashAttention when the operator merely
    *exists*. PyTorch's Windows wheels declare it but are built without it
    ("USE_FLASH_ATTENTION was not enabled for build"), so the first song fails.
    One tiny call answers the real question.
    """
    import torch

    try:
        q = torch.zeros(1, 1, 8, dtype=torch.bfloat16, device="cuda")
        cu = torch.tensor([0, 1], dtype=torch.int32, device="cuda")
        torch.ops.aten._flash_attention_forward(q, q, q, cu, cu, 1, 1, 0.0, False, False)
        return True
    except Exception:  # noqa: BLE001 - any failure means "not usable here"
        return False


def patch_attention() -> str:
    """Steer GraphAR to cuDNN attention where FlashAttention is not built in."""
    if flash_attention_works():
        return "flash"
    from yue2 import cuda_graph

    original = cuda_graph.GraphAR.__init__

    def init(self, *args, attention_backend="auto", **kwargs):
        if attention_backend in ("auto", "flash"):
            attention_backend = "cudnn"
        original(self, *args, attention_backend=attention_backend, **kwargs)

    cuda_graph.GraphAR.__init__ = init
    return "cudnn"


#: What each memory mode changes. They trade speed for memory, not quality: offload_ar parks the language model in system RAM while the
#: acoustic stage runs, a smaller vae_core_frames decodes audio in shorter tiles,
#: and query_chunk_size splits the acoustic attention (the stage that ran out of
#: memory on a 3-minute song) into blocks.
MEMORY_MODES = {
    "fast": {"offload_ar": False, "vae_core_frames": 1024, "query_chunk_size": None},
    "balanced": {"offload_ar": True, "vae_core_frames": 1024, "query_chunk_size": None},
    "low": {"offload_ar": True, "vae_core_frames": 512, "query_chunk_size": 2048},
}


def patch_nar_chunking(size: int) -> None:
    """Pass query_chunk_size to YuE2's acoustic stage, which the pipeline never does."""
    from yue2 import nar

    original = nar.synthesize

    def synthesize(*args, **kwargs):
        kwargs.setdefault("query_chunk_size", size)
        return original(*args, **kwargs)

    nar.synthesize = synthesize


def run(job: dict) -> int:
    import torch
    from yue2 import YuE2Pipeline

    patch_progress()
    if job.get("backend", "torch") == "torch":
        print(f"attention backend: {patch_attention()}", file=sys.stderr)

    output = Path(job["output_dir"])
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output folder is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available - Gokuk needs an NVIDIA graphics card")
    props = torch.cuda.get_device_properties(0)
    proto.emit("hello", worker="yue2", torch=torch.__version__, gpu=props.name,
               vram_gib=round(props.total_memory / 2**30, 1))

    request = {"style": job["style"], "lyrics": job["lyrics"], "cot": job.get("cot", "full")}
    for key in ("seed", "abc", "cfg_scale"):
        if job.get(key) is not None:
            request[key] = job[key]
    if request.get("abc") is not None and request["cot"] == "off":
        raise ValueError("A supplied score needs full or melody planning")

    # YuE2's memory_budget_gib is a hard ceiling (set_per_process_memory_fraction),
    # not a saving: "12" on a 16 GB card refuses allocations while 7 GB sits free.
    # So the budget is always the whole card, and a mode picks the real savers.
    memory = MEMORY_MODES.get(job.get("memory", "balanced"), MEMORY_MODES["balanced"])
    budget = props.total_memory / 2**30
    if memory["query_chunk_size"]:
        patch_nar_chunking(memory["query_chunk_size"])
    print(f"memory mode: {job.get('memory')} {memory}, card {budget:.1f} GiB", file=sys.stderr)
    started = time.perf_counter()
    pipe = YuE2Pipeline.from_pretrained(
        job["model_dir"], vae=job["vae_dir"], local_files_only=True, device="cuda",
        memory_budget_gib=budget, backend=job.get("backend", "torch"),
        offload_ar=memory["offload_ar"], vae_core_frames=memory["vae_core_frames"],
        verify_hashes=bool(job.get("verify_hashes", False)),
    )
    try:
        (output / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        if job.get("stage") == "plan":
            plan = pipe.plan(**request, cancelled=proto.cancelled)
            plan.save(output)
            proto.emit("done", stage="plan", dir=str(output), truncated=plan.truncated,
                       seconds=round(time.perf_counter() - started, 1))
            return 0

        song = pipe(**request, cancelled=proto.cancelled)
        song.save_artifacts(output)
        write_peaks(output / "audio.flac", output / "peaks.json")
        proto.emit("done", stage="audio", dir=str(output), audio=str(output / "audio.flac"),
                   truncated=song.truncated, seconds=round(time.perf_counter() - started, 1),
                   peak_vram_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2))
        return 0
    finally:
        pipe.close()


def write_peaks(audio: Path, target: Path, points: int = 600) -> None:
    """A tiny waveform summary so the GUI can draw it without numpy."""
    try:
        import numpy as np
        import soundfile as sf

        data, rate = sf.read(str(audio), dtype="float32", always_2d=True)
        mono = np.abs(data).max(axis=1)
        size = max(1, len(mono) // points)
        trimmed = mono[: size * points].reshape(-1, size).max(axis=1)
        target.write_text(json.dumps({"seconds": len(mono) / rate,
                                      "peaks": [round(float(v), 3) for v in trimmed]}))
    except Exception as exc:  # noqa: BLE001 - a missing waveform must not fail the song
        print(f"peaks skipped: {exc}", file=sys.stderr)


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
