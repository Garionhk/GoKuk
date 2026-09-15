"""The song library: one folder per song, readable without Gokuk.

    songs/2026-09-12_1422_city-lights/
        audio.flac        the song
        score.abc         the melody and chords YuE2 planned (or was given)
        song.json         what Gokuk shows: title, style, lyrics, kind, seed, ...
        peaks.json        waveform summary for the player
        plan.json, request.json, result.json, *.npy   YuE2's own records

Nothing is kept in a database, so copying, renaming or deleting a folder in
Explorer is a perfectly good way to manage songs. Deleting from Gokuk moves
the folder to songs/_trash rather than destroying it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import paths

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRASH = "_trash"


def slug(text: str, limit: int = 32) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "song"


def title_from_lyrics(lyrics: str) -> str:
    """The first sung line is a better default title than 'Untitled'."""
    for line in lyrics.splitlines():
        line = line.strip()
        if line and not line.startswith("["):
            return line[:48]
    return "Untitled"


@dataclass
class Song:
    folder: Path
    title: str = "Untitled"
    kind: str = "song"                 # song | cover | plan
    style: str = ""
    lyrics: str = ""
    seed: int | None = None
    cot: str = "full"
    created: str = ""
    favourite: bool = False
    seconds: float = 0.0
    source_audio: str = ""
    parent: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def audio(self) -> Path:
        return self.folder / "audio.flac"

    @property
    def score(self) -> Path:
        return self.folder / "score.abc"

    @property
    def has_audio(self) -> bool:
        return self.audio.is_file()

    INPUT_SCORE = "input_score.abc"

    def settings(self) -> "SongSettings":
        """Everything needed to make this song again.

        song.json holds what Gokuk shows; job.json (written by the engine for every
        run, older songs included) holds exactly what was sent: planning mode, style
        strength and any score supplied. A cover's score in job.json has its chords
        removed, so the complete score is kept separately as input_score.abc.
        """
        try:
            job = json.loads((self.folder / "job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            job = {}
        score = ""
        try:
            score = (self.folder / self.INPUT_SCORE).read_text(encoding="utf-8")
        except OSError:
            score = job.get("abc") or ""
        return SongSettings(
            kind=self.kind, title=self.title, style=job.get("style") or self.style,
            style_builder=self.extra.get("style_builder"), lyrics=job.get("lyrics") or self.lyrics,
            cot=job.get("cot") or self.cot or "full", seed=job.get("seed", self.seed),
            cfg_scale=job.get("cfg_scale"), score=score,
            source_path=self.extra.get("source_path", ""), source_name=self.source_audio)

    def peaks(self) -> list[float]:
        try:
            return json.loads((self.folder / "peaks.json").read_text())["peaks"]
        except (OSError, ValueError, KeyError):
            return []

    def save(self) -> None:
        data = {k: v for k, v in self.__dict__.items() if k not in ("folder", "extra")}
        data.update(self.extra)
        (self.folder / "song.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                               encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "Song | None":
        try:
            data = json.loads((folder / "song.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        song = cls(folder)
        for key, value in data.items():
            if key in song.__dict__ and key not in ("folder", "extra"):
                setattr(song, key, value)
            else:
                song.extra[key] = value
        if not song.seconds:
            try:
                song.seconds = float(json.loads((folder / "peaks.json").read_text())["seconds"])
            except (OSError, ValueError, KeyError):
                pass
        return song


@dataclass
class SongSettings:
    kind: str
    title: str
    style: str
    style_builder: dict | None
    lyrics: str
    cot: str
    seed: int | None
    cfg_scale: float | None
    score: str                 # the score the run was given ("" if YuE2 wrote its own)
    source_path: str           # covers: the original recording, if Gokuk knows where it is
    source_name: str


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)

    def new_folder(self, title: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        folder = self.root / f"{stamp}_{slug(title)}"
        n = 2
        while folder.exists():
            folder = self.root / f"{stamp}_{slug(title)}-{n}"
            n += 1
        return folder

    def songs(self) -> list[Song]:
        if not self.root.is_dir():
            return []
        found = []
        for folder in self.root.iterdir():
            if folder.is_dir() and folder.name != TRASH:
                song = Song.load(folder)
                if song is not None:
                    found.append(song)
        return sorted(found, key=lambda s: s.created, reverse=True)

    def trash(self, song: Song) -> Path:
        bin_ = self.root / TRASH
        bin_.mkdir(parents=True, exist_ok=True)
        target = bin_ / song.folder.name
        if target.exists():
            target = bin_ / f"{song.folder.name}-{datetime.now():%H%M%S}"
        shutil.move(str(song.folder), str(target))
        return target


def export(song: Song, target: Path) -> Path:
    """Copy the FLAC, or convert with the bundled FFmpeg for WAV / MP3."""
    target = Path(target)
    suffix = target.suffix.lower()
    if suffix == ".flac":
        shutil.copyfile(song.audio, target)
        return target
    ffmpeg = paths.ffmpeg_bin() / "ffmpeg.exe"
    if not ffmpeg.is_file():
        raise FileNotFoundError("FFmpeg is part of the Cover maker - install it with Gokuk Setup "
                                "to export WAV or MP3.")
    codec = ["-c:a", "libmp3lame", "-b:a", "320k"] if suffix == ".mp3" else ["-c:a", "pcm_s24le"]
    title = ["-metadata", f"title={song.title}"]
    result = subprocess.run([str(ffmpeg), "-y", "-v", "error", "-i", str(song.audio), *codec, *title,
                             str(target)], capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-400:] or "FFmpeg failed")
    return target
