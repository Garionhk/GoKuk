"""Turn what the user chose into worker jobs, and failures into plain words."""
from __future__ import annotations

import random

from app import gpu, paths
from app.config import Config
from app.i18n import N, t


#: Older settings files stored a GB figure; map them onto the modes.
LEGACY_PERFORMANCE = {"24": "fast", "16": "balanced", "12": "low"}


def memory_mode(cfg: Config) -> str:
    choice = cfg.get("performance", "auto")
    choice = LEGACY_PERFORMANCE.get(choice, choice)
    if choice in gpu.MEMORY_MODES:
        return choice
    return gpu.auto_memory(gpu.detect())


def new_seed() -> int:
    return random.randint(1, 2**31 - 1)


def song_job(cfg: Config, *, style: str, lyrics: str, cot: str, seed: int, output_dir,
             abc: str | None = None, stage: str = "audio", cfg_scale: float | None = None) -> dict:
    decoder = "YuE2-Vae-legacy" if cfg.get("decoder") == "legacy" else "YuE2-Vae"
    return {
        "style": style, "lyrics": lyrics, "cot": cot, "seed": int(seed), "abc": abc,
        "cfg_scale": cfg_scale, "stage": stage, "output_dir": str(output_dir),
        "model_dir": str(paths.models_dir() / "YuE2-3B"),
        "vae_dir": str(paths.models_dir() / decoder),
        "memory": memory_mode(cfg),
        "backend": cfg.get("backend", "torch"), "verify_hashes": False,
    }


def transcribe_job(*, audio: str, output_dir, vocal_only: bool = False) -> dict:
    return {
        "audio": str(audio), "output_dir": str(output_dir), "melody_only": False,
        "vocal_only": vocal_only,
        "model_dir": str(paths.models_dir() / "SheetSage2"),
        "mert_dir": str(paths.models_dir() / "MERT-v2-FullSong"),
    }


#: Worker stage ids -> what a musician is told. Order is the order they happen.
STAGES = [
    ("load", N("Warming up the band")),
    ("plan", N("Writing the melody and chords")),
    ("score", N("Reading your score")),
    ("sing", N("Performing the song")),
    ("synth", N("Mixing the sound")),
    ("decode", N("Rendering the audio")),
]
STAGE_ALIASES = {"resolve": "load", "verify": "load", "decoder": "decode"}

#: Rough share of the whole run each stage takes (measured on an RTX A4000).
STAGE_WEIGHT = {"load": 0.12, "plan": 0.10, "score": 0.02, "sing": 0.30, "synth": 0.38, "decode": 0.08}

TRANSCRIBE_STAGES = [
    ("load", N("Loading the listener")),
    ("transcribe_audio", N("Reading the recording")),
    ("transcribe_encoding", N("Listening")),
    ("transcribe_decoding", N("Writing down the melody")),
    ("transcribe_notation", N("Writing the score")),
]


def friendly_error(event: dict) -> str:
    kind = event.get("kind", "unknown")
    message = event.get("message", "")
    if kind == "cancelled":
        return t("Stopped.")
    if kind == "out_of_memory":
        return t("The graphics card ran out of memory, even in the lowest-memory mode. Close other "
                 "programs that use the GPU (games, browsers, video apps) or try shorter lyrics.")
    if kind == "no_gpu":
        return t("No NVIDIA graphics card is available to the engine. Check the driver is installed.")
    if kind == "driver":
        return t("The graphics driver is too old for the engine. Update it from nvidia.com.")
    if kind == "missing_files":
        return t("Some engine files are missing. Open Settings → Repair to run Gokuk Setup.")
    if kind == "no_score":
        return t("The melody could not be written down from this recording ({reason}). "
                 "Try a clearer recording, or a different part of the song.", reason=message)
    if kind == "mixed_versions":
        return t("This Gokuk folder mixes files from two versions, so the engine could not start. "
                 "Replace Gokuk.exe, Gokuk Setup.exe and the whole _internal folder from the new release "
                 "(keep runtime, models and songs).")
    if kind == "crashed":
        return t("The engine stopped unexpectedly. The details are in the log folder.")
    return t("Something went wrong: {message}", message=message)
