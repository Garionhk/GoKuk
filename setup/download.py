"""Downloading very large files without losing work.

A 27 GB model over a domestic connection takes hours, and something will
interrupt it. Three rules follow from that:

* **Resume, never restart.** Bytes land in ``<name>.part``; a second attempt
  asks the server to continue from wherever that file stopped.
* **Never rename an unfinished file.** The final name appears only once the
  size (and hash, when the catalogue has one) checks out, so a half-downloaded
  model can never look installed.
* **Say what is happening.** Speed and time remaining, because "wait several
  hours" is only tolerable when it is visibly progressing.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from app.i18n import t

CHUNK = 1024 * 1024          # 1 MB - large enough to be fast, small enough to cancel promptly
RETRIES = 4
BACKOFF = 3                  # seconds, doubled each retry


class DownloadError(Exception):
    """A download could not be completed."""


class Cancelled(Exception):
    """The user asked to stop."""


@dataclass
class Progress:
    """One update, cheap enough to emit every chunk."""
    done: int
    total: int
    speed: float = 0.0           # bytes per second, smoothed
    name: str = ""

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)

    @property
    def eta_seconds(self) -> float | None:
        if not self.speed or not self.total or self.done >= self.total:
            return None
        return (self.total - self.done) / self.speed


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 90:
        return t("{n}s left", n=seconds)
    if seconds < 5400:
        return t("{n} min left", n=seconds // 60)
    return t("{n} hours left", n=f"{seconds / 3600:.1f}")


#: Where to go for a key, per host, so the message can say something useful
#: instead of "403".
ACCOUNTS = {
    "huggingface.co": ("HuggingFace", "https://huggingface.co/settings/tokens"),
    "civitai.com": ("Civitai", "https://civitai.com/user/account"),
}


def _account_for(url: str) -> tuple[str, str]:
    host = url.split("/")[2].lower() if "//" in url else ""
    return ACCOUNTS.get(host, ("", ""))


def _sent_to_login(response) -> bool:
    """Civitai answers 200 and redirects to auth.civitai.com rather than 401.

    Without this the login page itself would be saved as the model file, which
    would look like a successful download of a few KB of HTML. Only the host
    and path are examined - signed CDN links carry all sorts of words in their
    query strings, and matching those would reject good downloads.
    """
    final = getattr(response, "url", "") or ""
    if "//" not in final:
        return False
    rest = final.split("//", 1)[1].split("?", 1)[0]
    host, _, path = rest.partition("/")
    return host.lower().startswith("auth.") or path.lower().startswith("login")


def _account_needed(name: str, url: str, status: int) -> str:
    site, page = _account_for(url)
    if not site:
        return t("{name} was refused (HTTP {status}). It may need an account, or "
                 "the link may have moved.", name=name, status=status)
    return t("{name} needs a {site} account.\n\n"
             "1. Sign in at {site_url} and accept the model's licence on its own "
             "page.\n"
             "2. Create an API key at:\n   {page}\n"
             "3. Paste it into the {site} box in this window and press Install "
             "again.", name=name, site=site, site_url=f"{site.lower()}.com", page=page)


def _session(token: str = "") -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "GokukSetup/1.0"
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def download(
    url: str,
    target: Path,
    expected_bytes: int = 0,
    sha256: str = "",
    token: str = "",
    on_progress: Callable[[Progress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Path:
    """Fetch ``url`` to ``target``, resuming a previous attempt if there is one.

    Returns the finished path. Raises Cancelled if stopped, DownloadError if it
    cannot be completed.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")

    # Already there and the right size? Nothing to do - this is what makes a
    # re-run cheap and a resumed install possible.
    if target.is_file() and (not expected_bytes or target.stat().st_size == expected_bytes):
        if on_progress:
            size = target.stat().st_size
            on_progress(Progress(size, size, name=target.name))
        return target

    session = _session(token)
    last_error = ""

    for attempt in range(RETRIES):
        have = part.stat().st_size if part.is_file() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as r:
                if r.status_code == 416:            # already complete
                    have = part.stat().st_size
                    break
                if r.status_code in (401, 403) or _sent_to_login(r):
                    raise DownloadError(_account_needed(target.name, url, r.status_code))
                r.raise_for_status()

                # A server that ignores Range restarts the file, so the part
                # file has to be thrown away rather than appended to.
                resuming = r.status_code == 206
                if have and not resuming:
                    have = 0

                total = int(r.headers.get("Content-Length") or 0) + have
                total = total or expected_bytes

                started, seen, speed = time.time(), 0, 0.0
                with open(part, "ab" if resuming and have else "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        if should_stop and should_stop():
                            raise Cancelled(target.name)
                        if not chunk:
                            continue
                        f.write(chunk)
                        have += len(chunk)
                        seen += len(chunk)
                        elapsed = time.time() - started
                        if elapsed > 0.5:
                            sample = seen / elapsed
                            speed = sample if not speed else speed * 0.7 + sample * 0.3
                            started, seen = time.time(), 0
                        if on_progress:
                            on_progress(Progress(have, total, speed, target.name))
            break
        except (Cancelled, DownloadError):
            raise
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt == RETRIES - 1:
                raise DownloadError(
                    t("Could not download {name} after {tries} tries.",
                      name=target.name, tries=RETRIES) + f"\n\n{last_error}")
            time.sleep(BACKOFF * (2 ** attempt))

    size = part.stat().st_size if part.is_file() else 0
    if expected_bytes and size != expected_bytes:
        raise DownloadError(t(
            "{name} finished at the wrong size - expected {expected} bytes, got "
            "{got}.\n\n"
            "The link probably points at a different build of this model. "
            "The partial file has been kept so nothing is lost.",
            name=target.name, expected=f"{expected_bytes:,}", got=f"{size:,}"))

    if sha256:
        if on_progress:
            on_progress(Progress(size, size,
                                 name=t("checking {name}", name=target.name)))
        if file_sha256(part, should_stop) != sha256.lower():
            part.unlink(missing_ok=True)
            raise DownloadError(t(
                "{name} failed its checksum and was discarded.", name=target.name))

    os.replace(part, target)
    return target


def file_sha256(path: Path, should_stop: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if should_stop and should_stop():
                raise Cancelled(str(path))
            digest.update(chunk)
    return digest.hexdigest()


def free_space(path: Path) -> int:
    """Bytes available on the drive holding ``path``, existing or not."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        import shutil
        return shutil.disk_usage(probe).free
    except OSError:
        return 0
