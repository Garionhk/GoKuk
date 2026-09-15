"""The install itself: everything Gokuk needs, into one portable folder.

Order matters and is deliberate:

1. **Check** the disk has room.
2. **Models first.** They are most of the download (YuE2-3B alone is 7 GB), so
   a slow connection shows real progress straight away and can be left running.
3. **Tools and runtimes** - uv, standalone Python builds, FFmpeg - unpacked.
4. **Libraries** - every wheel downloaded individually (so PyTorch's 2.5 GB
   wheel has a proper progress bar), then installed offline with uv straight
   into the standalone Python. No virtual environments: a venv remembers
   absolute paths and breaks when the folder is moved.
5. **Verify** each runtime can import its engine and see the graphics card.
6. **Tidy** the wheel cache and write setup_state.json, which Gokuk reads to
   decide whether it is installed.

Every step is idempotent. A finished item leaves a marker holding the hash of
its catalogue entry, so re-running Setup skips it, and a changed catalogue
entry (a new release) redoes exactly that item. Downloads resume from their
``.part`` file, so Cancel never throws work away.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.i18n import t
from setup.catalog import Catalog, Item, human_bytes
from setup.download import Cancelled, DownloadError, Progress, download, free_space

HEADROOM = 1.2          # unpacking and wheel installs need more than the download
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Overall:
    """Whole-install progress, sent alongside each per-file update."""
    step: int
    steps: int
    label: str
    done: int
    total: int
    speed: float = 0.0
    file: Progress | None = None

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 0.0

    @property
    def eta_seconds(self) -> float | None:
        if not self.speed or self.done >= self.total:
            return None
        return (self.total - self.done) / self.speed


@dataclass
class Report:
    ok: bool = False
    cancelled: bool = False
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Installer:
    def __init__(self, catalog: Catalog, root: Path, components: list[str],
                 on_step: Callable[[str], None] | None = None,
                 on_progress: Callable[[Overall], None] | None = None,
                 on_log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 hf_token: str = "", clean_wheels: bool = True):
        self.catalog = catalog
        self.root = Path(root)
        self.components = components
        self.items = catalog.items_for(components)
        self.on_step = on_step or (lambda s: None)
        self.on_progress = on_progress or (lambda p: None)
        self.on_log = on_log or (lambda s: None)
        self.should_stop = should_stop or (lambda: False)
        self.hf_token = hf_token
        self.clean_wheels = clean_wheels
        self._step, self._steps, self._label = 0, 0, ""
        self._done_before = 0      # bytes of finished files this run
        self._total = 0
        self._speed = 0.0

    # --- paths -----------------------------------------------------------
    def path(self, relative: str) -> Path:
        return self.root / relative

    def marker(self, item: Item) -> Path:
        return self.path(f"cache/state/{item.id.replace(':', '_')}.done")

    @staticmethod
    def fingerprint(item: Item) -> str:
        return hashlib.sha256(json.dumps(item.data, sort_keys=True).encode()).hexdigest()

    def finished(self, item: Item) -> bool:
        m = self.marker(item)
        try:
            return m.read_text().strip() == self.fingerprint(item) and self._present(item)
        except OSError:
            return False

    def _present(self, item: Item) -> bool:
        """Cheap check that a marked item is still actually there."""
        if item.kind == "model":
            return all(self.path(f["target"]).is_file() and
                       self.path(f["target"]).stat().st_size == f["size"] for f in item.files)
        if item.kind == "archive":
            return self.path(item.dest).is_dir()
        if item.kind == "wheels":
            return self.path(item.data["python"]).is_file()
        return True

    def mark(self, item: Item) -> None:
        m = self.marker(item)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(self.fingerprint(item))

    # --- run -------------------------------------------------------------
    def plan(self) -> list[Item]:
        """Items still to do, in install order: models, archives, environments."""
        order = {"model": 0, "archive": 1, "wheels": 2}
        todo = [i for i in self.items if not self.finished(i)]
        # uv must be unpacked before anything that installs wheels; python before its env.
        return sorted(todo, key=lambda i: (order[i.kind], 0 if i.id == "tool:uv" else 1))

    def run(self) -> Report:
        report = Report()
        todo = self.plan()
        report.skipped = [i.id for i in self.items if i not in todo]
        self._steps = len(todo) + 2
        self._total = sum(i.size for i in todo)
        started = time.time()
        try:
            self._begin(t("Checking free space"))
            self.check_space(todo)
            for item in todo:
                self._begin(self._describe(item))
                self._log(f"== {item.id}")
                if item.kind == "model":
                    self.install_model(item)
                elif item.kind == "archive":
                    self.install_archive(item)
                elif item.kind == "wheels":
                    self.install_wheels(item)
                self.mark(item)
                report.installed.append(item.id)
            self._begin(t("Checking the installation"))
            self.verify()
            if self.clean_wheels:
                shutil.rmtree(self.path("cache/wheels"), ignore_errors=True)
                shutil.rmtree(self.path("cache/downloads"), ignore_errors=True)
            self.write_state()
            report.ok = True
            self._log(f"Finished in {(time.time() - started) / 60:.1f} min")
        except Cancelled:
            report.cancelled = True
            self._log("Cancelled - everything downloaded so far is kept.")
        except (DownloadError, InstallError, OSError) as exc:
            report.errors.append(str(exc))
            self._log(f"FAILED: {exc}")
        return report

    def _describe(self, item: Item) -> str:
        if item.kind == "model":
            return t("Downloading model {name}", name=item.label)
        if item.kind == "wheels":
            return t("Installing libraries for {name}", name=item.id.split(":")[1])
        return t("Unpacking {name}", name=item.label)

    def _begin(self, label: str) -> None:
        self._step += 1
        self._label = label
        self.on_step(label)
        self._emit(None)

    def _log(self, text: str) -> None:
        self.on_log(text)

    def _emit(self, file: Progress | None) -> None:
        done = self._done_before + (file.done if file else 0)
        if file and file.speed:
            self._speed = file.speed
        self.on_progress(Overall(self._step, self._steps, self._label, done, self._total,
                                 self._speed, file))

    # --- steps -----------------------------------------------------------
    def check_space(self, todo: list[Item]) -> None:
        need = int(sum(i.size for i in todo) * HEADROOM)
        have = free_space(self.root)
        self._log(f"Need about {human_bytes(need)}, {human_bytes(have)} free on this drive")
        if have and have < need:
            raise InstallError(t("Not enough disk space: about {need} is needed and {have} is free. "
                                 "Free some space or choose another folder.",
                                 need=human_bytes(need), have=human_bytes(have)))

    def _fetch(self, file: dict) -> Path:
        if self.should_stop():
            raise Cancelled(file["target"])
        target = self.path(file["target"])
        token = self.hf_token if "huggingface.co" in file["url"] else ""
        result = download(file["url"], target, expected_bytes=file["size"], sha256=file["sha256"],
                          token=token, on_progress=self._emit, should_stop=self.should_stop)
        self._done_before += file["size"]
        return result

    def install_model(self, item: Item) -> None:
        # Largest file first, so the long wait comes while the user is still watching.
        for file in sorted(item.files, key=lambda f: -f["size"]):
            self._fetch(file)

    def install_archive(self, item: Item) -> None:
        archive = self._fetch(item.files[0])
        dest = self.path(item.dest)
        staging = dest.with_name(dest.name + ".unpacking")
        shutil.rmtree(staging, ignore_errors=True)
        self._log(f"Unpacking {archive.name} -> {item.dest}")
        unpack(archive, staging, item.data.get("strip"))
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
        if item.id.startswith("python:"):
            # A fresh interpreter has no libraries: its environment must be redone too.
            self.path(f"cache/state/env_{item.id.split(':')[1]}.done").unlink(missing_ok=True)
        # Standalone Pythons refuse package installs by default (PEP 668). This
        # copy belongs to Gokuk alone, so that protection has nothing to protect.
        (dest / "Lib" / "EXTERNALLY-MANAGED").unlink(missing_ok=True)

    def install_wheels(self, item: Item) -> None:
        paths = [self._fetch(f) for f in item.files]
        python = self.path(item.data["python"])
        if not python.is_file():
            raise InstallError(t("The Python runtime for {name} is missing - run Setup again.",
                                 name=item.id))
        self.on_step(t("Installing {n} libraries…", n=len(paths)))
        wheels = [str(p) for p, w in zip(paths, item.data["wheels"]) if not w.get("build")]
        sources = [str(p) for p, w in zip(paths, item.data["wheels"]) if w.get("build")]
        # --no-deps: the lock already lists every package, resolved in advance.
        base = [str(self.uv()), "pip", "install", "--python", str(python), "--offline",
                "--no-deps", "--no-index", "--link-mode", "copy", "--reinstall"]
        self._run(base + wheels, t("Installing libraries failed"))
        if sources:
            # Pure-Python source packages, built with the setuptools installed just above.
            self._run(base + ["--no-build-isolation", *sources], t("Building libraries failed"))

    def verify(self) -> None:
        checks = []
        if any(i.id == "env:yue2" for i in self.items):
            checks.append((self.path("runtime/yue2/python.exe"),
                           "import torch, yue2, soundfile; print('yue2', torch.__version__, torch.cuda.is_available())"))
        if any(i.id == "env:sheetsage" for i in self.items):
            checks.append((self.path("runtime/sheetsage/python.exe"),
                           "import torch, transformers, pretty_midi, soundfile; "
                           "print('sheetsage', torch.__version__, torch.cuda.is_available())"))
        for python, code in checks:
            out = self._run([str(python), "-c", code], t("A runtime failed its self-test"))
            if "False" in out:
                self._log("Warning: the runtime cannot see the graphics card (CUDA unavailable)")

    def write_state(self) -> None:
        state_file = self.path("setup_state.json")
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        items = state.get("items", {})
        for item in self.items:
            items[item.id] = {"component": item.component, "fingerprint": self.fingerprint(item),
                              "revision": item.data.get("revision")}
        components = sorted({i.component for i in self.catalog.items if i.id in items and
                             all(j.id in items for j in self.catalog.items if j.component == i.component)})
        state.update({"schema": 1, "components": components, "items": items,
                      "updated": datetime.now().isoformat(timespec="seconds")})
        state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")

    # --- helpers ---------------------------------------------------------
    def uv(self) -> Path:
        exe = self.path("runtime/uv/uv.exe")
        if not exe.is_file():
            raise InstallError(t("uv is missing - run Setup again."))
        return exe

    def environment(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "UV_", "VIRTUAL_ENV"))}
        env.update({
            "UV_CACHE_DIR": str(self.path("cache/uv")),
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "HF_HOME": str(self.path("cache/hf")),
        })
        return env

    def _run(self, cmd: list[str], failure: str) -> str:
        self._log("$ " + " ".join(cmd[:6]) + (" …" if len(cmd) > 6 else ""))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=self.environment(),
                                cwd=self.root, creationflags=NO_WINDOW)
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                self._log(line)
            if self.should_stop():
                proc.kill()
                raise Cancelled(cmd[0])
        if proc.wait():
            tail = "\n".join(lines[-12:])
            raise InstallError(f"{failure}.\n\n{tail}")
        return "\n".join(lines)


class InstallError(Exception):
    """A step failed in a way worth explaining to the user."""


def unpack(archive: Path, destination: Path, strip: str | None) -> None:
    """Unpack a .zip or .tar.gz, dropping a single top folder named ``strip``."""
    destination.mkdir(parents=True, exist_ok=True)
    prefix = f"{strip.strip('/')}/" if strip else ""

    def target_for(name: str) -> Path | None:
        name = name.replace("\\", "/")
        if prefix:
            if not name.startswith(prefix):
                return None
            name = name[len(prefix):]
        if not name or name.startswith("/") or ".." in Path(name).parts:
            return None
        return destination / name

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                out = target_for(info.filename)
                if out is None or info.is_dir():
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    else:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar:
                out = target_for(member.name)
                if out is None or not member.isfile():
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
