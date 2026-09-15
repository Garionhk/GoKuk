"""Write setup/catalog.json: every file Gokuk Setup downloads, pinned.

Run by the maintainer, never on a user's machine:

    python tools/build_manifest.py

Needs Python 3.11+ (tomllib), network access, and ``uv`` on PATH or in
``spike/uv``. What it does:

1. Resolves both Python environments with ``uv pip compile`` for Windows x64 and
   writes the lock files to setup/locks/ (kept in the repo for review).
2. Flattens each lock into one wheel per package: URL, size and sha256, so Setup
   can download them with byte-level progress and verify every one.
3. Pins each model repository to its current commit and records every file's
   size and sha256 (LFS oid) from the HuggingFace API.
4. Downloads the few archives that publish no size/hash we can trust in one call
   (uv, Python runtimes, FFmpeg) and hashes them locally.

The output is deterministic for a given set of upstream releases, so a diff of
catalog.json shows exactly what a new Gokuk release will download.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ROOT / "setup" / "locks"
CATALOG = ROOT / "setup" / "catalog.json"

UV_VERSION = "0.12.13"
PBS_RELEASE = "20260901"
PY_YUE2 = "3.12.14"
PY_SHEETSAGE = "3.11.16"
YUE2_WHEEL = ("https://github.com/multimodal-art-projection/YuE/releases/download/"
              "yue2-v0.1.6/yue2_infer-0.1.6-py3-none-any.whl")
FFMPEG = ("https://github.com/GyanD/codexffmpeg/releases/download/6.1.1/"
          "ffmpeg-6.1.1-full_build-shared.zip")

ENVS = {
    "yue2": {
        "python": PY_YUE2,
        "requirements": [f"yue2-infer @ {YUE2_WHEEL}"],
        "index": "https://download.pytorch.org/whl/cu128",
    },
    "sheetsage": {
        "python": PY_SHEETSAGE,
        # Pinned exactly as m-a-p/SheetSage2 requirements.txt, plus soundfile
        # for reading the uploaded song.
        "requirements": [
            "torch==2.8.0", "torchaudio==2.8.0", "transformers==4.45.2",
            "huggingface-hub==0.36.0", "safetensors==0.5.3", "numpy==1.24.3",
            "scipy==1.13.1", "mir_eval==0.8.2", "pretty_midi==0.2.10", "mido==1.3.3",
            "setuptools==78.1.1", "soundfile==0.13.1",
        ],
        "index": "https://download.pytorch.org/whl/cu126",
    },
}

MODELS = {
    "YuE2-3B": "song",
    "YuE2-Vae": "song",
    "SheetSage2": "cover",
    "MERT-v2-FullSong": "cover",
    "YuE2-Vae-legacy": "legacy",
}
#: Files in the model repos Gokuk never needs.
MODEL_SKIP_PREFIXES = ("assets/", "render_assets/", "examples/")
MODEL_SKIP_SUFFIXES = (".whl", ".md", ".png", ".jpg", ".gif", ".pdf")
MODEL_KEEP = {"README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"}


def uv() -> str:
    found = shutil.which("uv") or str(ROOT / "spike" / "uv" / "uv.exe")
    if not Path(found).exists():
        sys.exit("uv not found: install it or put uv.exe in spike/uv/")
    return found


def _open(url: str):
    # Some mirrors refuse urllib's default User-Agent with a 403.
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GokukBuild/1.0"}), timeout=120)


def get_json(url: str):
    with _open(url) as r:
        return json.load(r)


def hash_remote(url: str) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with _open(url) as r:
        while chunk := r.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    print(f"  hashed {url.rsplit('/', 1)[-1]}: {size:,} bytes")
    return size, digest.hexdigest()


def archive(item_id: str, url: str, dest: str, strip: str | None, component: str) -> dict:
    size, sha = hash_remote(url)
    return {"id": item_id, "kind": "archive", "component": component, "url": url,
            "size": size, "sha256": sha, "dest": dest, "strip": strip}


def compile_env(name: str, spec: dict) -> dict:
    LOCKS.mkdir(parents=True, exist_ok=True)
    lock = LOCKS / f"pylock.{name}.toml"
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "requirements.in"
        req.write_text("\n".join(spec["requirements"]) + "\n", encoding="utf-8")
        subprocess.run([
            uv(), "pip", "compile", str(req), "-o", str(lock), "--no-header",
            "--python-version", spec["python"], "--python-platform", "x86_64-pc-windows-msvc",
            "--extra-index-url", spec["index"], "--index-strategy", "unsafe-best-match",
        ], check=True)

    tag = "cp" + "".join(spec["python"].split(".")[:2])
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    wheels = []
    for package in data["packages"]:
        if "archive" in package:
            entry = package["archive"]
            wheels.append({"name": package["name"], "url": entry["url"],
                           "size": entry.get("size") or hash_remote(entry["url"])[0],
                           "sha256": entry["hashes"]["sha256"]})
            continue
        chosen = pick_wheel(package.get("wheels", []), tag)
        if chosen is None:
            # Source-only packages (pretty_midi, mir_eval) are pure Python: Setup
            # builds them offline with the setuptools from this same lock.
            if "sdist" not in package:
                sys.exit(f"No Windows wheel or sdist for {package['name']} {package['version']} ({tag})")
            sdist = package["sdist"]
            wheels.append({"name": package["name"], "url": sdist["url"], "size": sdist["size"],
                           "sha256": sdist["hashes"]["sha256"], "build": True})
            print(f"  {package['name']}: source only, built during Setup")
            continue
        url = chosen["url"].split("#")[0]
        size, sha = chosen.get("size"), chosen.get("hashes", {}).get("sha256")
        if not sha and "#sha256=" in chosen["url"]:
            sha = chosen["url"].split("#sha256=", 1)[1]
        if not size or not sha:
            # The PyTorch index mirrors some small wheels without metadata.
            size, sha = hash_remote(url)
        wheels.append({"name": package["name"], "url": url, "size": size, "sha256": sha})
    component = "song" if name == "yue2" else "cover"
    return {"id": f"env:{name}", "kind": "wheels", "component": component,
            "python": f"runtime/{name}/python.exe", "lock": f"setup/locks/{lock.name}",
            "size": sum(w["size"] for w in wheels), "wheels": wheels}


def pick_wheel(wheels: list[dict], tag: str) -> dict | None:
    """The one wheel a Windows x64 CPython of this version would install.

    Platform wheels beat pure ones: soundfile ships ``py3-none-win_amd64`` with
    libsndfile.dll inside *and* a ``py3-none-any`` without it, and choosing the
    pure one installs a package that cannot load.
    """
    def score(url: str) -> int:
        file = url.rsplit("/", 1)[-1].split("#")[0]
        parts = file[:-4].split("-")
        if not file.endswith(".whl") or len(parts) < 5:
            return 0
        python, abi, platform = parts[-3], parts[-2], parts[-1]
        pythons = python.split(".")
        if platform == "win_amd64":
            if tag in pythons:
                return 5
            if abi == "abi3" and any(p.startswith("cp3") for p in pythons):
                return 4
            if abi == "none" and "py3" in pythons:
                return 3
            return 0
        if platform == "any" and abi == "none":
            return 1
        return 0
    ranked = sorted(wheels, key=lambda w: score(w["url"]), reverse=True)
    return ranked[0] if ranked and score(ranked[0]["url"]) else None


def model(repo: str, component: str) -> dict:
    info = get_json(f"https://huggingface.co/api/models/m-a-p/{repo}")
    sha = info["sha"]
    tree = get_json(f"https://huggingface.co/api/models/m-a-p/{repo}/tree/{sha}?recursive=true")
    files = []
    for entry in tree:
        path = entry["path"]
        if entry["type"] != "file" or path.startswith(MODEL_SKIP_PREFIXES):
            continue
        if path.endswith(MODEL_SKIP_SUFFIXES) and path not in MODEL_KEEP:
            continue
        lfs = entry.get("lfs") or {}
        files.append({"path": path, "size": entry["size"], "sha256": lfs.get("oid", ""),
                      "url": f"https://huggingface.co/m-a-p/{repo}/resolve/{sha}/{path}"})
    print(f"  {repo}@{sha[:10]}: {len(files)} files, {sum(f['size'] for f in files):,} bytes")
    return {"id": f"model:{repo}", "kind": "model", "component": component, "repo": f"m-a-p/{repo}",
            "revision": sha, "dest": f"models/{repo}", "size": sum(f["size"] for f in files),
            "files": files}


def main() -> None:
    pbs = "https://github.com/astral-sh/python-build-standalone/releases/download"
    items = []
    print("Models")
    # Models first: they are the biggest downloads, so Setup starts on them.
    for repo, component in MODELS.items():
        items.append(model(repo, component))
    print("Archives")
    items += [
        archive("tool:uv", f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
                f"uv-x86_64-pc-windows-msvc.zip", "runtime/uv", None, "song"),
        archive("python:yue2", f"{pbs}/{PBS_RELEASE}/cpython-{PY_YUE2}%2B{PBS_RELEASE}-"
                f"x86_64-pc-windows-msvc-install_only_stripped.tar.gz", "runtime/yue2", "python", "song"),
        archive("python:sheetsage", f"{pbs}/{PBS_RELEASE}/cpython-{PY_SHEETSAGE}%2B{PBS_RELEASE}-"
                f"x86_64-pc-windows-msvc-install_only_stripped.tar.gz", "runtime/sheetsage", "python", "cover"),
        archive("tool:ffmpeg", FFMPEG, "runtime/ffmpeg", "ffmpeg-6.1.1-full_build-shared", "cover"),
    ]
    print("Environments")
    for name, spec in ENVS.items():
        items.append(compile_env(name, spec))

    catalog = {
        "schema": 1,
        "components": [
            {"id": "song", "label": "Song creation", "required": True, "default": True,
             "detail": "Style + lyrics to a finished song (YuE2)"},
            {"id": "cover", "label": "Cover maker", "required": False, "default": True,
             "detail": "Turn a recording into a new song with its melody (SheetSage2)"},
            {"id": "legacy", "label": "Benchmark decoder", "required": False, "default": False,
             "detail": "The paper's evaluation decoder - most people never need it"},
        ],
        "items": items,
    }
    CATALOG.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(i["size"] for i in items)
    print(f"Wrote {CATALOG} - {len(items)} items, {total / 2**30:.2f} GiB")


if __name__ == "__main__":
    main()
