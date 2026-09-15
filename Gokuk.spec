# -*- mode: python ; coding: utf-8 -*-
"""Gokuk and Gokuk Setup, built into one portable folder.

    python tools/build_release.py        (or: pyinstaller --noconfirm Gokuk.spec)

Two programs share one _internal folder, so PySide6 is shipped once. Neither
contains torch: the engines are downloaded by Setup into runtime/.

Shipped as plain files beside the code, because they are read (or run) from
disk rather than imported: the language catalogue, the icons, the download
catalogue and its lock files, and the worker scripts that the downloaded
Pythons execute.
"""
import sys

sys.path.insert(0, SPECPATH)
from build_common import EXCLUDES, strip_unused

DATAS = [
    ("lang", "lang"),
    ("assets/icons", "assets/icons"),
    ("setup/catalog.json", "setup"),
    ("setup/locks", "setup/locks"),
    ("workers", "workers"),
]


def analysis(script, extra_excludes=()):
    a = Analysis(
        [script],
        pathex=[SPECPATH],
        binaries=[],
        datas=DATAS,
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=EXCLUDES + list(extra_excludes),
        noarchive=False,
        optimize=0,
    )
    a.binaries = strip_unused(a.binaries)
    return a


def program(a, name, icon):
    return EXE(
        PYZ(a.pure),
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=[icon],
    )


app = analysis("Gokuk.py")
setup = analysis("GokukSetup.py", extra_excludes=["PySide6.QtMultimedia"])

app_exe = program(app, "Gokuk", "assets/icons/Gokuk.ico")
setup_exe = program(setup, "Gokuk Setup", "assets/icons/GokukSetup.ico")

coll = COLLECT(
    app_exe, app.binaries, app.datas,
    setup_exe, setup.binaries, setup.datas,
    strip=False,
    upx=False,
    name="Gokuk",
)
