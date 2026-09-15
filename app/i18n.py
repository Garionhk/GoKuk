"""Translating the interface, without turning the source into puzzle pieces.

Strings are looked up by their English text rather than by an invented key:

    label.setText(t("Create song"))

A missing translation falls back to perfectly good English, and a translator
works from a file where both halves are sentences. Catalogues live in
lang/<code>.json as a flat {english: translated} map.

Ported from EasyAI. One change: the chosen language is remembered inside the
portable folder (language.json beside the exe), never in %APPDATA%. Both Gokuk
and Gokuk Setup read and write it, so choosing once covers both.
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path

from app.paths import resource_dir, root

LANG_DIR = resource_dir() / "lang"

#: Code -> the language's name written in that language, so someone stuck in
#: the wrong one can still find their way out.
LANGUAGES = {
    "en": "English",
    "zh-Hant": "繁體中文",
}

DEFAULT = "en"

_current = DEFAULT
_catalogue: dict[str, str] = {}
_missing: set[str] = set()


def available() -> dict[str, str]:
    """Languages that can actually be selected: English plus any file present."""
    found = {"en": LANGUAGES["en"]}
    for code, name in LANGUAGES.items():
        if code != "en" and (LANG_DIR / f"{code}.json").is_file():
            found[code] = name
    return found


def detect() -> str:
    """Guess from Windows: any Chinese locale opens in Traditional Chinese."""
    raw = ""
    try:
        raw = locale.getdefaultlocale()[0] or ""
    except (ValueError, TypeError):
        pass
    raw = (raw or os.environ.get("LANG", "")).replace("_", "-").lower()
    if raw.startswith("zh"):
        return "zh-Hant"
    return DEFAULT


def load(code: str) -> str:
    """Switch language. Returns the code actually in use."""
    global _current, _catalogue
    if code not in LANGUAGES:
        code = DEFAULT
    _catalogue = {}
    if code != DEFAULT:
        path = LANG_DIR / f"{code}.json"
        try:
            # utf-8-sig: Notepad saves JSON with a byte order mark.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            _catalogue = {k: v for k, v in raw.items()
                          if not k.startswith("_") and isinstance(v, str) and v}
        except (OSError, json.JSONDecodeError):
            code = DEFAULT
    _current = code
    return code


def current() -> str:
    return _current


def t(text: str, **fields) -> str:
    """Translate ``text``, then fill in any {placeholders}."""
    out = _catalogue.get(text, text)
    if out is text and _current != DEFAULT:
        _missing.add(text)
    if fields:
        try:
            out = out.format(**fields)
        except (KeyError, IndexError, ValueError):
            try:
                out = text.format(**fields)
            except (KeyError, IndexError, ValueError):
                return text
    return out


def _preference_file() -> Path:
    return root() / "language.json"


def remember(code: str) -> None:
    try:
        _preference_file().write_text(json.dumps({"language": code}), encoding="utf-8")
    except OSError:
        pass            # a read-only folder is not a reason to fail


def remembered() -> str | None:
    try:
        code = json.loads(_preference_file().read_text(encoding="utf-8-sig"))["language"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return code if code in LANGUAGES else None


def start() -> str:
    """Choose the language for this run: the saved one, else what Windows says."""
    return load(remembered() or detect())


def plural(count: int, one: str, many: str, **fields) -> str:
    """Pick the singular or plural sentence, then translate the whole thing."""
    return t(one if count == 1 else many, n=count, **fields)


def N(text: str) -> str:
    """Mark text for translation without translating it yet (table data)."""
    return text


def missing() -> set[str]:
    return set(_missing)


load(DEFAULT)
