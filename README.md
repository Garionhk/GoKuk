# Gokuk

A portable Windows app for making music on your own graphics card, built on
[YuE2](https://github.com/multimodal-art-projection/YuE) (songs) and
SheetSage2 (listening to a recording).

Three tabs, plus a score editor:

- **Create** — pick a sound from chips (language, genre, mood, voice, instruments, tempo),
  write lyrics with `[Verse]` / `[Chorus]` tags, press **Create song**.
- **Cover** — choose a recording, let the AI write its melody down, then give it new words and a
  new sound. **Takes** makes several versions in one go.
- **Library** — every song, with a player, search, favourites, MP3/WAV/FLAC export, and
  **↻ Load settings** to make any song again.
- **Score editor** — see and edit the melody, chords and song parts as a piano roll.

The look and structure follow EasyAI: the same dark theme with an orange accent, a separate
**Gokuk Setup** program, and English + 繁體中文.

## For users

1. Unzip `Gokuk-v1.0.0.zip` somewhere with about 25 GB free (for example `D:\Gokuk`), not inside
   OneDrive or Program Files.
2. Run **Gokuk Setup.exe**. It downloads about 16.5 GB, largest files first, showing the step,
   amount, speed, time left, the current file and a checklist. Cancel any time; running it again
   carries on from where it stopped.
3. Run **Gokuk.exe**.

### What you need

| | |
|---|---|
| Windows | 10 or 11, 64-bit |
| Graphics card | NVIDIA, 12 GB or more (16 GB comfortable, 24 GB fastest). RTX 30xx, 40xx and 50xx are all covered, as are workstation cards like the A4000 |
| Driver | R570 or newer |
| Disk | ~25 GB |

Speed on the RTX A4000 (16 GB) it was developed on: a one-minute song takes about 80 seconds,
a three-minute song about five minutes, and writing down a melody about 80 seconds.

### Making a song

**Create** writes the song in stages, which the progress card names: it plans a score (melody and
chords), performs it, mixes the sound, then renders the audio. Under **More options**:

- **How to write it** — plan melody and chords first (best), melody only, or sing straight away.
- **Let me see and edit the score before singing** — plans the score, opens the editor, and sings
  it when you press **Sing it now**.
- **Seed** — the dice makes a new one; tick **Keep this seed** to reproduce a song exactly.
- **Takes** — 1 to 4 versions in a row, each with a different seed.
- **Follow the style** — how strictly to follow the style words.

### Making a cover

1. Drop in a recording (**▶ Listen** plays it, **■ Stop** ends it).
2. **Write down the melody** — SheetSage2 writes the melody *and chords* as a score. You can also
   **Load a score file…** instead and skip the recording entirely.
3. New style and lyrics, then **Make cover**, with its own **Takes** box.

Covers are sung from the melody without chords, so the new style can re-harmonise; the chords stay
in the editor for reference.

### The score editor

Opens from a song card's **Score** button, from **See / edit the score** on the Cover tab, or after
"plan first" on Create. It is the same editor everywhere.

- **Piano roll** with note names, a keyboard, and a faded view of the other voice. Click to add a
  note, drag to move, drag its right edge to change length, Delete to remove, ↑/↓ to change pitch,
  Shift-drag to select several. Switch between the **Vocal** and **Instrument** lines.
- **Chords and parts** — click the chord row to type a chord (checked against what the engine
  understands), right-click a part to rename it or start a new one, right-click a bar number to
  insert or delete bars.
- **Key and tempo** — changing the key moves notes and chords together, warns past ±5 semitones,
  and shows the vocal range. Songs that change key partway through (common in transcriptions) are
  shown with the change marked on the ruler, and so are bars in another metre.
- **Play** — a plain synth preview of melody, instrument line and chords, so you can check the
  notes before spending minutes on a song.
- **Use it** — **Make a song with this score** (Create) or **Make a cover with this score**
  (Cover), plus **Open .abc…** / **Save as .abc…** and an **ABC text** view for hand editing.

Every score the editor sends is written in YuE2's own ABC dialect, read back and compared with what
you edited; if anything differs it refuses rather than sending something else. The parser is
vendored from YuE2's `abc_tools.py` (Apache-2.0, see `licences/`).

### Making a song again

**↻** on any song card (or ⋯ → *Load settings to make it again*) fills the tab that song came from
with everything used to make it: style, lyrics, title, planning mode, style strength, seed, the
score it was sung from, and for covers the original recording. Press Create or Make cover for a new
version, or tick **Keep this seed** to reproduce the same one. It works on older songs too, because
the settings are read from the `job.json` saved with every run.

### Settings

Language, graphics-card mode, audio decoder, and maintenance (repair, log folder, clear cache).

**Graphics-card modes** decide what is traded for memory, never quality:

| Mode | What it does |
|---|---|
| Fastest | everything stays on the card (24 GB cards) |
| Balanced | the singing model is parked in system memory while the sound is mixed |
| Lowest memory | also mixes and decodes in smaller pieces (automatic below 24 GB) |

If a song still runs out of memory, Gokuk retries it once in Lowest memory mode.

## How it is put together

```
Gokuk.exe / Gokuk Setup.exe   PySide6 windows (no torch inside)
runtime/yue2/                 standalone Python 3.12 + torch 2.10 cu128 + yue2-infer 0.1.6
runtime/sheetsage/            standalone Python 3.11 + torch 2.8 cu128 (SheetSage2's pinned versions)
runtime/ffmpeg/, runtime/uv/  FFmpeg 6.1 shared, uv
models/                       YuE2-3B, YuE2-Vae, SheetSage2, MERT-v2-FullSong (pinned commits)
songs/                        one plain folder per song (audio.flac, score.abc, song.json …)
app/score/                    read, edit, write and preview scores (stdlib only)
workers/                      the scripts the downloaded Pythons run
```

- **Portable:** there are no venvs, since a venv stores absolute paths. Packages install straight
  into the standalone Pythons. Every cache and temp folder (`HF_HOME`, `TORCH_HOME`, `TEMP` …)
  points inside the folder, and nothing goes to `%APPDATA%` or the registry. Proven by moving the
  runtime to another path and generating from there.
- **Workers:** the window never imports torch. Each job is
  `runtime\…\python.exe workers\<name>_worker.py <job>.json`, reporting progress as JSON lines on
  stdout (`workers/_protocol.py`). To cancel, the window creates `<job>.cancel`. The worker exits
  after each job, so graphics memory is always freed. The window checks the workers' `WORKER_VERSION`
  before starting one, so a folder that mixes files from two releases says so instead of misbehaving.
- **Setup:** `setup/catalog.json` pins every download with its URL, size and sha256. PyTorch arrives
  as individual wheels, so its 2.5 GB file gets a real progress bar, and uv then installs them
  offline. Each finished item leaves a marker, so re-runs skip completed work and `.part` downloads
  resume. Nothing is resolved on the user's machine: the Python versions, wheels and CUDA builds are
  all decided when the release is built.

### Windows and hardware fixes the code carries

| Problem | Fix |
|---|---|
| PyTorch's Windows wheels declare FlashAttention but aren't built with it, so YuE2's CUDA-graph decoder fails on the first song | `yue2_worker.py` probes it and steers `GraphAR` to cuDNN attention; if the fast path still fails, `Engine` retries once in eager mode |
| YuE2's `memory_budget_gib` is a hard ceiling, not a saving, so a "12 GB" setting caused out-of-memory on a 16 GB card | the engine always gets the whole card; modes switch on real savers (`offload_ar`, smaller VAE tiles, chunked acoustic attention, which YuE2 supports but never passes) |
| YuE2 writes JSON in the default code page (cp1252), which crashes on Chinese lyrics | workers run with `PYTHONUTF8=1` |
| A thread reading stdin deadlocks imports on Windows | the job is passed as a file; cancel is a flag file |
| `soundfile` has a pure wheel without `libsndfile.dll` | the manifest builder prefers `win_amd64` wheels |
| `pretty_midi` is source-only | built offline during Setup with the locked setuptools |
| SheetSage2's CUDA 12.6 build has no RTX 50-series (`sm_120`) code | both runtimes use CUDA 12.8 builds, which still include Ampere and Ada |

## Developing

```bash
pip install -r requirements-build.txt
python -m pytest tests -q                 # 83 tests, no GPU needed
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

`GOKUK_SMOKE_SHOT=<png>` makes either exe screenshot its window and quit; adding
`GOKUK_SMOKE_CREATE=1` to `Gokuk.exe` makes it generate one song first. Both are how the built exes
are smoke-tested.

The tests cover the score core hardest, because a wrong score is the one bug a user could not
spot: every sample score round-trips through read → edit → write → read unchanged, transposing and
back restores it exactly, and every pitch in every key spells back to itself.

## Licences

YuE2's code is Apache-2.0, and `app/score/abc_dialect.py` is adapted from its `abc_tools.py`
(notice and licence in `licences/`, shipped with the release). The YuE2, SheetSage2 and MERT model
weights are **CC BY-NC 4.0** (non-commercial), which Setup makes users acknowledge before
downloading.

Gokuk's own licence is not decided yet. `LICENSE-2.0.txt` in the root is just a copy of the Apache
2.0 text, not a statement that this project uses it - pick a licence and replace it.
