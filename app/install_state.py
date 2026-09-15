"""Is Gokuk installed? Asked by Gokuk at startup and by Setup for its cards.

Setup writes setup_state.json when it finishes. That file alone is not trusted:
a folder copied without its models, or a half-deleted runtime, would still
carry it. So the handful of files each component cannot work without are
checked too - quick existence checks, no hashing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app import paths

#: Files each component cannot run without, relative to the portable root.
ESSENTIAL = {
    "song": ["runtime/yue2/python.exe", "models/YuE2-3B/model.safetensors",
             "models/YuE2-Vae/model.safetensors"],
    "cover": ["runtime/sheetsage/python.exe", "runtime/ffmpeg/bin/ffmpeg.exe",
              "models/SheetSage2/model.safetensors", "models/MERT-v2-FullSong/model.safetensors"],
    "legacy": ["models/YuE2-Vae-legacy/model.safetensors"],
}


@dataclass
class InstallState:
    components: list[str] = field(default_factory=list)
    missing: dict[str, list[str]] = field(default_factory=dict)
    updated: str = ""

    @classmethod
    def load(cls) -> "InstallState":
        root = paths.root()
        try:
            raw = json.loads(paths.setup_state_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        recorded = raw.get("components", []) if isinstance(raw, dict) else []
        present, missing = [], {}
        for component in recorded:
            gone = [f for f in ESSENTIAL.get(component, []) if not (root / f).is_file()]
            if gone:
                missing[component] = gone
            else:
                present.append(component)
        return cls(present, missing, raw.get("updated", "") if isinstance(raw, dict) else "")

    @property
    def ready(self) -> bool:
        """Song creation is the minimum Gokuk can do anything with."""
        return "song" in self.components

    def has(self, component: str) -> bool:
        return component in self.components
