# Gokuk

A portable Windows app for making music with
[YuE2](https://github.com/multimodal-art-projection/YuE):

- **Create**: pick a sound with chips (language, genre, mood, voice, instruments,
  tempo), write lyrics with `[Verse]`/`[Chorus]` tags, and press **Create song**.
- **Cover**: drop in a recording. SheetSage2 writes its melody down as a score,
  and YuE2 sings it with your new words and sound.
- **Library**: every song, with a player, favourites, the score editor, and MP3/WAV/FLAC export.
- **Score editor**: YuE2 plans every song as a score (melody and chords). The editor shows it as a
  piano roll with a chord chart and song parts, and lets you:
  - add, move, stretch or delete notes, for the vocal line and the instrument line;
  - type chords and rename parts;
  - change key (notes and chords move together) and tempo;
  - hear a quick preview, and open or save `.abc` files.

  Edited scores go to **Create** (sung with melody and chords) or **Cover** (melody only, no
  recording needed). Every score is written in YuE2's own ABC dialect and re-read before use,
  so it always means exactly what you edited. The parser is vendored from YuE2's
  `abc_tools.py` (Apache-2.0).

The look and structure follow EasyAI: the same dark theme with an orange accent,
a separate **Gokuk Setup** program, and English + 繁體中文.

## For users

1. Unzip `Gokuk-v1.0.0.zip` somewhere with about 25 GB free (e.g. `D:\Gokuk`).
2. Run **Gokuk Setup.exe**. It downloads about 16 GB, largest files first, and shows
   progress: step, amount, speed, time left, the current file, and a checklist.
   Cancel any time; running it again resumes from where it stopped.
3. Run **Gokuk.exe**.

Needs an NVIDIA GPU with 12 GB or more and a driver ≥ R570. On the RTX A4000
(16 GB) it was tested on, a one-minute song takes about 80 seconds.

## How it is put together

```
Gokuk.exe / Gokuk Setup.exe   PySide6 windows (no torch inside)
runtime/yue2/                 standalone Python 3.12 + torch 2.10 cu128 + yue2-infer 0.1.6
runtime/sheetsage/            standalone Python 3.11 + torch 2.8 cu126 (SheetSage2 pins)
runtime/ffmpeg/, runtime/uv/  FFmpeg 6.1 shared, uv
models/                       YuE2-3B, YuE2-Vae, SheetSage2, MERT-v2-FullSong (pinned commits)
songs/                        one plain folder per song (audio.flac, score.abc, song.json …)
```

- **Portable:** there are no venvs, since a venv stores absolute paths. Packages
  install straight into the standalone Pythons. Every cache and temp folder
  (`HF_HOME`, `TORCH_HOME`, `TEMP` …) points inside the folder, and nothing goes
  to `%APPDATA%` or the registry. The spike proved this by moving the runtime and
  generating from the new path.
- **Workers:** the window never imports torch. Each job is
  `runtime\…\python.exe workers\<name>_worker.py <job>.json`, which reports
  progress as JSON lines on stdout (`workers/_protocol.py`). To cancel, the window
  creates `<job>.cancel`. The worker exits after each job, so VRAM is always freed.
- **Setup:** `setup/catalog.json` pins every download with its URL, size, and
  sha256. PyTorch arrives as individual wheels, so its 2.5 GB file gets a real
  progress bar, and uv then installs them offline. Each finished item leaves a
  marker, so re-runs skip completed work and `.part` downloads resume.

### Windows fixes the workers carry (found in the Phase 0 spike)

| Problem | Fix |
|---|---|
| PyTorch's Windows wheels declare FlashAttention but aren't built with it, so YuE2's CUDA-graph decoder fails on the first song | `yue2_worker.py` probes it and steers `GraphAR` to cuDNN attention. If fast mode still fails, `Engine` retries once in eager mode |
| YuE2 writes JSON in the default code page (cp1252), which crashes on Chinese lyrics | workers run with `PYTHONUTF8=1` |
| A thread reading stdin deadlocks imports on Windows | job passed as a file; cancel via a flag file |
| `soundfile` has a pure wheel without `libsndfile.dll` | the manifest builder prefers `win_amd64` wheels |
| `pretty_midi` is source-only | built offline during Setup with the locked setuptools |

## Developing

```bash
pip install -r requirements-build.txt
python -m pytest tests -q                 # no GPU needed
python Gokuk.py                           # or Gokuk.bat / GokukSetup.bat
set GOKUK_ROOT=D:\somewhere               # point either program at another portable folder
```

Maintainer tools:

```bash
python tools/build_manifest.py            # re-pin downloads -> setup/catalog.json + setup/locks/
python tools/extract_strings.py           # translation coverage for lang/zh-Hant.json
python tools/make_icons.py                # icons (uses Gokuk-Icon.png if present)
python tools/build_release.py             # dist/Gokuk + release/Gokuk-v1.0.0.zip
```

`GOKUK_SMOKE_SHOT=<png>` makes either exe screenshot its window and quit.
Adding `GOKUK_SMOKE_CREATE=1` to `Gokuk.exe` makes it generate one song first.

## Licences

The YuE2 code is Apache-2.0. The YuE2, SheetSage2 and MERT model weights are
CC BY-NC 4.0 (non-commercial), which Setup makes users acknowledge before
downloading.
