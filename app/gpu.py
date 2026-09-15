"""What graphics card this machine has, asked of nvidia-smi.

Neither Gokuk nor Gokuk Setup imports torch, so the card is identified the way
the driver itself reports it. nvidia-smi ships with every NVIDIA driver.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from app.i18n import t

#: torch 2.10 cu128 needs driver R570+, torch 2.8 cu126 needs R560+.
MIN_DRIVER = 570.0


@dataclass
class GPU:
    name: str
    vram_mib: int
    driver: str

    @property
    def vram_gib(self) -> float:
        return self.vram_mib / 1024

    @property
    def driver_number(self) -> float:
        try:
            major, _, minor = self.driver.partition(".")
            return float(f"{int(major)}.{int(minor or 0):02d}")
        except ValueError:
            return 0.0


def detect() -> GPU | None:
    """The largest NVIDIA card, or None if there is none (or no driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cards = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit():
            cards.append(GPU(parts[0], int(parts[1]), parts[2]))
    return max(cards, key=lambda g: g.vram_mib) if cards else None


#: How hard to lean on the card. The engine always gets the whole card; a mode
#: only switches on savers (see MEMORY_MODES in workers/yue2_worker.py). An
#: earlier design passed "12 GB" as YuE2's memory budget, but that budget is a
#: hard ceiling, so on a 16 GB card it caused the out-of-memory it meant to avoid.
MEMORY_MODES = ("fast", "balanced", "low")


def auto_memory(gpu: GPU | None) -> str:
    """Below 24 GB, Lowest memory. Measured on a 16 GB RTX A4000 with a 3-minute
    song: Balanced peaked at 12.9 GiB (of 14 usable), Lowest memory at 9.5 GiB,
    and Lowest memory was only 4% slower (354 s against 340 s)."""
    if gpu is None or gpu.vram_gib >= 23:
        return "fast"
    return "low"


def verdict(gpu: GPU | None) -> tuple[str, str]:
    """(level, sentence) where level is ok | warn | bad."""
    if gpu is None:
        return "bad", t("No NVIDIA graphics card found - Gokuk needs one to make music.")
    if gpu.driver_number and gpu.driver_number < MIN_DRIVER:
        return "bad", t("{name}: the graphics driver ({driver}) is too old. Update it from nvidia.com "
                        "to version {need} or newer.", name=gpu.name, driver=gpu.driver,
                        need=int(MIN_DRIVER))
    gib = round(gpu.vram_gib)
    if gpu.vram_gib >= 23:
        return "ok", t("{name} · {gib} GB - full speed.", name=gpu.name, gib=gib)
    if gpu.vram_gib >= 11:
        return "warn", t("{name} · {gib} GB - works in low-memory mode, slower than a 24 GB card.",
                         name=gpu.name, gib=gib)
    return "bad", t("{name} · {gib} GB - not enough graphics memory (12 GB minimum).",
                    name=gpu.name, gib=gib)
