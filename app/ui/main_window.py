"""The Gokuk window: Create, Cover and Library tabs above a player bar."""
from __future__ import annotations

import shutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QTabBar, QVBoxLayout,
    QWidget,
)

from app import gpu, paths
from app.config import Config
from app.i18n import t
from app.install_state import InstallState
from app.jobs.engine import Engine
from app.songs import Library, Song
from app.ui import theme
from app.ui.cover_tab import CoverTab
from app.ui.create_tab import CreateTab
from app.ui.library_tab import LibraryTab
from app.ui.player_bar import PlayerBar
from app.ui.settings_dialog import SettingsDialog
from app.ui.song_card import SongCard


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, install: InstallState):
        super().__init__()
        self.cfg = cfg
        self.install = install
        self.library = Library(cfg.songs_dir())
        self.engine = Engine(self)
        self.gpu = gpu.detect()
        self.setWindowTitle("Gokuk")
        self.resize(1480, 920)
        self.setMinimumSize(1180, 760)
        self._build()
        self.engine.busy_changed.connect(self._busy)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run_current)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._run_current)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self._settings)
        QShortcut(QKeySequence(Qt.Key_Space | Qt.ControlModifier), self, activated=self.player.toggle)

    def _build(self) -> None:
        self.player = PlayerBar()       # first: the tabs' song cards connect to it
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(20, 6, 16, 0)
        logo = QLabel(f"<span style='font-size:21px;font-weight:900;color:{theme.ACCENT}'>●</span>"
                      f"<span style='font-size:21px;font-weight:900'>&nbsp;Gokuk</span>")
        header.addWidget(logo)
        header.addSpacing(18)

        self.create_tab = CreateTab(self)
        self.cover_tab = CoverTab(self)
        self.library_tab = LibraryTab(self)
        self.tabs = QStackedWidget()
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        for widget, label in ((self.create_tab, t("Create")), (self.cover_tab, t("Cover")),
                              (self.library_tab, t("Library"))):
            self.tabs.addWidget(widget)
            self.tab_bar.addTab(label)
        self.tab_bar.currentChanged.connect(self._switch)
        header.addWidget(self.tab_bar, 0, Qt.AlignBottom)

        header.addStretch(1)
        pill_text = f"{self.gpu.name} · {round(self.gpu.vram_gib)} GB" if self.gpu else t("No NVIDIA GPU")
        self.pill = QLabel(pill_text)
        self.pill.setObjectName("EnginePill")
        header.addWidget(self.pill, 0, Qt.AlignVCenter)
        self.status = QLabel(t("Ready"))
        self.status.setObjectName("EnginePill")
        self.status.setMinimumWidth(96)
        self.status.setAlignment(Qt.AlignCenter)
        header.addWidget(self.status, 0, Qt.AlignVCenter)
        settings = QPushButton(t("Settings"))
        settings.clicked.connect(self._settings)
        header.addWidget(settings, 0, Qt.AlignVCenter)

        header_widget = QWidget()
        header_widget.setLayout(header)
        outer.addWidget(header_widget)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.LINE};")
        outer.addWidget(line)
        outer.addWidget(self.tabs, 1)

        outer.addWidget(self.player)
        self.setCentralWidget(page)

    # --- shared services for the tabs --------------------------------------
    def make_card(self, song: Song, compact: bool = False) -> SongCard:
        card = SongCard(song, compact=compact)
        card.play.connect(self.player.load)
        card.open_score.connect(self.show_score)
        card.use_score.connect(self._use_song_score)
        card.reuse.connect(self._reuse)
        card.delete.connect(self._delete)
        return card

    def edit_score(self, text: str, *, title: str, origin: str, actions=("create", "cover"),
                   primary: str = "create", on_sing=None) -> None:
        """Open the score editor; whatever the user chooses is routed from here."""
        from app.ui.score_editor import ScoreEditor

        dialog = ScoreEditor(text, title=title, actions=actions, primary=primary,
                             pause_player=self.player.player.pause, parent=self)

        def chosen(action: str, abc: str) -> None:
            if action == "sing" and on_sing:
                on_sing(abc)
            else:
                self.use_score(abc, action, origin)

        dialog.chosen.connect(chosen)
        dialog.exec()

    def use_score(self, text: str, target: str, origin: str) -> None:
        """Hand a score to the Create or Cover tab, and show that tab."""
        if target == "cover":
            self.cover_tab.set_score(text, origin)
            self.show_tab(self.cover_tab)
        else:
            self.create_tab.set_score(text, origin)
            self.show_tab(self.create_tab)

    def show_score(self, song: Song) -> None:
        try:
            abc = song.score.read_text(encoding="utf-8")
        except OSError:
            return
        self.edit_score(abc, title=t("Score - {title}", title=song.title), origin=song.title)

    def _use_song_score(self, song: Song, target: str) -> None:
        try:
            abc = song.score.read_text(encoding="utf-8")
        except OSError:
            return
        self.use_score(abc, target, song.title)

    def _switch(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        if self.tabs.currentWidget() is self.library_tab:
            self.library_tab.refresh()

    def show_tab(self, widget) -> None:
        self.tab_bar.setCurrentIndex(self.tabs.indexOf(widget))

    def _reuse(self, song: Song) -> None:
        """Fill the tab a song came from with everything used to make it."""
        settings = song.settings()
        if song.kind == "cover":
            self.cover_tab.load_settings(settings)
            self.show_tab(self.cover_tab)
        else:
            self.create_tab.load_settings(settings)
            self.show_tab(self.create_tab)

    def _delete(self, song: Song) -> None:
        if QMessageBox.question(self, t("Move to trash"),
                                t("Move “{title}” to the trash folder?\nIt stays in songs/_trash until you "
                                  "delete it there.", title=song.title)) != QMessageBox.Yes:
            return
        self.player.release(song)
        try:
            self.library.trash(song)
        except OSError as exc:
            QMessageBox.warning(self, t("Move to trash"), str(exc))
        self.library_changed()

    def show_error(self, title: str, message: str, event: dict) -> None:
        """A plain-words message, with the engine's own text under Show Details.

        Shown on the next event-loop turn: callers are inside an engine callback,
        and a modal dialog there would hold the engine "busy" (and any queued
        takes) until the user closed it.
        """
        details = event.get("message") or ""
        if self.engine.last_log:
            details += f"\n\n{t('Log file')}: {self.engine.last_log}"

        def show() -> None:
            box = QMessageBox(QMessageBox.Warning, title, message, QMessageBox.Ok, self)
            if details.strip():
                box.setDetailedText(details.strip())
            box.exec()

        QTimer.singleShot(0, show)

    def library_changed(self) -> None:
        if self.tabs.currentWidget() is self.library_tab:
            self.library_tab.refresh()

    def discard_folder(self, folder) -> None:
        """Remove what a failed or cancelled run left behind."""
        if folder and folder.is_dir() and not (folder / "song.json").exists():
            shutil.rmtree(folder, ignore_errors=True)

    # --- window ----------------------------------------------------------
    def _busy(self, busy: bool) -> None:
        self.status.setText(t("Working…") if busy else t("Ready"))
        self.status.setStyleSheet(f"color:{theme.ACCENT};" if busy else "")
        self.create_tab.set_busy(busy)
        self.cover_tab.set_busy(busy)

    def _run_current(self) -> None:
        current = self.tabs.currentWidget()
        if current is self.create_tab:
            self.create_tab.create()
        elif current is self.cover_tab:
            self.cover_tab.create()

    def _settings(self) -> None:
        dialog = SettingsDialog(self.cfg, self)
        if dialog.exec() and dialog.language_changed:
            QMessageBox.information(self, t("Settings"), t("The new language is used the next time Gokuk starts."))

    def closeEvent(self, event) -> None:
        if self.engine.busy:
            if QMessageBox.question(self, t("Quit Gokuk?"),
                                    t("A song is still being made. Stop it and quit?")) != QMessageBox.Yes:
                event.ignore()
                return
            self.engine.cancel_all()
            if self.engine.process:
                self.engine.process.proc.kill()
                self.engine.process.proc.waitForFinished(5000)
        self.create_tab.remember()
        self.cfg.save()
        event.accept()
