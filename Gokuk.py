"""Gokuk - make songs from style and lyrics, and covers from recordings.

Start it with:   python Gokuk.py   (or double-click Gokuk.exe)

Needs Gokuk Setup to have run first; if it hasn't, Gokuk says so and offers to
open it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _make_console_utf8_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _report_fatal(error: BaseException) -> None:
    """Put a startup failure where the user can see it - a windowed build has no console."""
    import traceback

    details = "".join(traceback.format_exception(error))
    try:
        print(details, file=sys.stderr)
    except Exception:
        pass
    message = (
        "Gokuk could not start.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "If this keeps happening, run Gokuk.bat from a Command Prompt to see the full message."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        box = QMessageBox(QMessageBox.Critical, "Gokuk", message)
        box.setDetailedText(details)
        box.exec()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "Gokuk", 0x10)
        except Exception:
            pass


def not_installed(install) -> bool:
    """Explain that Setup comes first, and offer to open it. True if Setup was started."""
    from PySide6.QtWidgets import QMessageBox

    from app.i18n import t
    from app.ui.settings_dialog import launch_setup

    if install.missing:
        detail = t("Some files are missing:\n{files}",
                   files="\n".join(f for files in install.missing.values() for f in files[:3]))
    else:
        detail = t("The music engine and its models (about 16 GB) need to be downloaded first.")
    box = QMessageBox()
    box.setWindowTitle("Gokuk")
    box.setIcon(QMessageBox.Information)
    box.setText(t("Gokuk isn't installed yet"))
    box.setInformativeText(detail + "\n\n" + t("Gokuk Setup does this for you, with progress shown."))
    open_btn = box.addButton(t("Open Gokuk Setup"), QMessageBox.AcceptRole)
    open_btn.setObjectName("Primary")
    box.addButton(t("Quit"), QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is open_btn:
        return launch_setup()
    return False


def _smoke_shot(app, window) -> None:
    """GOKUK_SMOKE_SHOT=<png>: save a screenshot of the window, then quit.

    Lets an automated check confirm a windowed build really draws its window.
    """
    import os

    target = os.environ.get("GOKUK_SMOKE_SHOT")
    if not target:
        return
    from PySide6.QtCore import QTimer

    if not os.environ.get("GOKUK_SMOKE_CREATE"):
        QTimer.singleShot(3000, lambda: (window.grab().save(target), app.quit()))
        return

    # GOKUK_SMOKE_CREATE=1: also make one song from the sample lyrics, then
    # screenshot and quit - proves a built exe can drive the engine end to end.
    def wait() -> None:
        if window.engine.busy:
            QTimer.singleShot(1000, wait)
        else:
            window.grab().save(target)
            app.quit()

    def go() -> None:
        from app import music

        window.create_tab.lyrics.set_lyrics(music.SAMPLE_LYRICS["en"])
        window.create_tab.takes.setValue(1)
        window.create_tab.create()
        QTimer.singleShot(1000, wait)

    QTimer.singleShot(1500, go)


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.config import Config
    from app.install_state import InstallState
    from app.ui import theme

    app = QApplication(sys.argv)
    app.setApplicationName("Gokuk")
    app.setOrganizationName("Gokuk")
    i18n.start()
    theme.apply(app)

    install = InstallState.load()
    if not install.ready:
        not_installed(install)
        return 0

    from app.ui.main_window import MainWindow

    window = MainWindow(Config(), install)
    window.show()
    _smoke_shot(app, window)
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as fatal:      # noqa: BLE001 - last line of defence
        _report_fatal(fatal)
        raise SystemExit(1)
