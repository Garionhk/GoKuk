"""Settings, kept in settings.json inside the portable folder.

Defaults are merged over whatever is on disk, so a settings file from an older
version keeps working and gains new keys silently.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import paths

DEFAULTS: dict[str, Any] = {
    # "auto" picks from the card's memory; "24" / "16" / "12" force a profile.
    "performance": "auto",
    # "torch" uses CUDA graphs (fastest); "torch-eager" is the safe fallback.
    "backend": "torch",
    "decoder": "standard",
    "songs_dir": "songs",            # relative paths stay inside the portable folder
    "last_style": {},
    "last_lyrics": "",
    "last_cover_style": {},
    "planning": "full",
    "cfg_scale": None,
    "takes": 1,
    "cover_takes": 1,
    "seed_locked": False,
    "seed": 831001,
    "window": None,
}


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or paths.settings_file())
        self.data = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(stored, dict):
                self.data.update(stored)
        except (OSError, json.JSONDecodeError):
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass      # a read-only folder must not crash the app

    def songs_dir(self) -> Path:
        value = Path(self.get("songs_dir") or "songs")
        return value if value.is_absolute() else paths.root() / value
