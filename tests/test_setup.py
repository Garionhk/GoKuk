import http.server
import io
import json
import tarfile
import threading
import zipfile
from functools import partial
from pathlib import Path

import pytest

import build_manifest
from setup import download as dl
from setup.catalog import Catalog
from setup.steps import Installer, unpack

ROOT = Path(__file__).resolve().parent.parent


def test_catalog_components_and_order():
    catalog = Catalog(ROOT / "setup" / "catalog.json")
    ids = {c.id for c in catalog.components}
    assert {"song", "cover"} <= ids
    song_only = {i.id for i in catalog.items_for([])}          # required component always included
    assert "model:YuE2-3B" in song_only and "env:sheetsage" not in song_only
    installer = Installer(catalog, Path("Z:/does-not-exist"), ["song", "cover"])
    kinds = [i.kind for i in installer.plan()]
    assert kinds == sorted(kinds, key=["model", "archive", "wheels"].index)
    archives = [i.id for i in installer.plan() if i.kind == "archive"]
    assert archives[0] == "tool:uv"


def test_every_download_is_pinned():
    catalog = Catalog(ROOT / "setup" / "catalog.json")
    for item in catalog.items:
        for f in item.files:
            assert f["size"] > 0, f
            if item.kind != "model" or f["size"] > 10_000_000:
                assert len(f["sha256"]) == 64, f
        if item.kind == "model":
            assert "/resolve/" + item.data["revision"] + "/" in item.files[0]["url"]


def test_pick_wheel_prefers_platform_wheels():
    wheels = [{"url": "https://x/soundfile-0.13.1-py2.py3-none-any.whl"},
              {"url": "https://x/soundfile-0.13.1-py2.py3-none-win_amd64.whl"},
              {"url": "https://x/soundfile-0.13.1-py2.py3-none-macosx_11_0_arm64.whl"}]
    assert build_manifest.pick_wheel(wheels, "cp312")["url"].endswith("win_amd64.whl")
    wheels = [{"url": "https://x/numpy-2.2.6-cp311-cp311-win_amd64.whl"},
              {"url": "https://x/numpy-2.2.6-cp312-cp312-win_amd64.whl"}]
    assert "cp312-cp312" in build_manifest.pick_wheel(wheels, "cp312")["url"]
    assert build_manifest.pick_wheel([{"url": "https://x/a-1-cp312-cp312-manylinux_x86_64.whl"}], "cp312") is None


def test_unpack_strips_top_folder(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("top/bin/ffmpeg.exe", b"x")
        z.writestr("other/skip.txt", b"y")
        z.writestr("top/../evil.txt", b"z")
    unpack(archive, tmp_path / "out", "top")
    assert (tmp_path / "out" / "bin" / "ffmpeg.exe").read_bytes() == b"x"
    assert not (tmp_path / "out" / "skip.txt").exists()
    assert not (tmp_path / "evil.txt").exists()

    tgz = tmp_path / "p.tar.gz"
    with tarfile.open(tgz, "w:gz") as tar:
        data = b"python"
        info = tarfile.TarInfo("python/python.exe")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    unpack(tgz, tmp_path / "py", "python")
    assert (tmp_path / "py" / "python.exe").read_bytes() == b"python"


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files with Range support, which http.server lacks."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = Path(self.translate_path(self.path))
        data = path.read_bytes()
        start = 0
        if "Range" in self.headers:
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_download_resumes_and_verifies(server, tmp_path):
    import hashlib

    root, base = server
    payload = bytes(range(256)) * 20_000          # ~5 MB
    (root / "model.bin").write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    target = tmp_path / "dl" / "model.bin"
    target.parent.mkdir()
    # A previous attempt left the first 1.5 MB behind.
    target.with_suffix(".bin.part").write_bytes(payload[:1_500_000])
    seen = []
    dl.download(f"{base}/model.bin", target, expected_bytes=len(payload), sha256=sha,
                on_progress=lambda p: seen.append(p.done))
    assert target.read_bytes() == payload
    assert seen and min(seen) >= 1_500_000              # continued, did not restart

    bad = tmp_path / "dl" / "bad.bin"
    with pytest.raises(dl.DownloadError):
        dl.download(f"{base}/model.bin", bad, expected_bytes=len(payload), sha256="0" * 64)
    assert not bad.exists()


def test_download_cancel_keeps_part(server, tmp_path):
    root, base = server
    (root / "big.bin").write_bytes(b"\0" * 8_000_000)
    target = tmp_path / "big.bin"
    with pytest.raises(dl.Cancelled):
        dl.download(f"{base}/big.bin", target, expected_bytes=8_000_000,
                    should_stop=lambda: target.with_suffix(".bin.part").exists()
                    and target.with_suffix(".bin.part").stat().st_size > 0)
    assert target.with_suffix(".bin.part").exists() and not target.exists()


def test_install_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GOKUK_ROOT", str(tmp_path))
    from app.install_state import InstallState

    assert not InstallState.load().ready
    (tmp_path / "setup_state.json").write_text(json.dumps({"components": ["song"]}))
    state = InstallState.load()
    assert not state.ready and "song" in state.missing        # recorded but files gone
    for f in ("runtime/yue2/python.exe", "models/YuE2-3B/model.safetensors",
              "models/YuE2-Vae/model.safetensors"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"x")
    assert InstallState.load().ready
