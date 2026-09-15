"""Where things live, whether running from source or from a built .exe.

Gokuk is portable: every file it creates or downloads sits inside one folder,
so the whole thing can be copied to another drive and keep working. Nothing
goes to %APPDATA%, the registry, or a user-wide cache.

Two different questions still get two different answers:

* **Files shipped with the program** - language catalogues, the install
  catalogue, workers, icons. Read-only; inside a PyInstaller bundle they live
  in ``_internal``.
* **Files the user creates or Setup downloads** - settings, runtimes, models,
  songs. These sit next to the .exe.

Running from source both are the project folder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: The checked-out project, used whenever this is not a frozen build.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only files that shipped inside the program."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    return PROJECT_ROOT


def root() -> Path:
    """The portable folder: everything Gokuk writes goes under here.

    ``GOKUK_ROOT`` overrides it, for testing Setup against a scratch folder.
    """
    override = os.environ.get("GOKUK_ROOT")
    if override:
        return Path(override).resolve()
    if frozen():
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


# Kept for code ported from EasyAI.
data_dir = root


def runtime_dir() -> Path:
    return root() / "runtime"


def models_dir() -> Path:
    return root() / "models"


def cache_dir() -> Path:
    return root() / "cache"


def songs_dir() -> Path:
    return root() / "songs"


def logs_dir() -> Path:
    return root() / "logs"


def yue2_python() -> Path:
    return runtime_dir() / "yue2" / "python.exe"


def sheetsage_python() -> Path:
    return runtime_dir() / "sheetsage" / "python.exe"


def ffmpeg_bin() -> Path:
    return runtime_dir() / "ffmpeg" / "bin"


def workers_dir() -> Path:
    return resource_dir() / "workers"


def setup_state_file() -> Path:
    return root() / "setup_state.json"


def settings_file() -> Path:
    return root() / "settings.json"


def setup_exe() -> Path:
    """The Setup program sitting beside the app (or its script from source)."""
    if frozen():
        return root() / "Gokuk Setup.exe"
    return PROJECT_ROOT / "GokukSetup.py"


def app_exe() -> Path:
    if frozen():
        return root() / "Gokuk.exe"
    return PROJECT_ROOT / "Gokuk.py"
