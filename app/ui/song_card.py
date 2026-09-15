"""One song as a row: play it, and everything you might do next."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout,
)

from app import songs as songlib
from app.i18n import N, t
from app.songs import Song
from app.ui import theme
from app.ui.widgets import open_folder

KIND_LABEL = {"song": N("Song"), "cover": N("Cover"), "plan": N("Score only")}


def when(created: str) -> str:
    try:
        stamp = datetime.fromisoformat(created)
    except ValueError:
        return ""
    today = datetime.now().date()
    if stamp.date() == today:
        return stamp.strftime("%H:%M")
    return stamp.strftime("%Y-%m-%d %H:%M")


class SongCard(QFrame):
    play = Signal(object)
    open_score = Signal(object)
    use_score = Signal(object, str)     # song, "create" | "cover"
    reuse = Signal(object)
    changed = Signal(object)
    delete = Signal(object)

    def __init__(self, song: Song, compact: bool = False, parent=None):
        super().__init__(parent)
        self.song = song
        self.setObjectName("Panel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.setStyleSheet(f"QPushButton{{border-radius:18px;padding:0;font-size:14px;color:{theme.ACCENT};"
                                    f"border:1px solid {theme.ACCENT};background:transparent;}}"
                                    f"QPushButton:hover{{background:{theme.ACCENT};color:{theme.ACCENT_INK};}}"
                                    f"QPushButton:disabled{{color:{theme.DIM};border-color:{theme.LINE};}}")
        self.play_btn.setEnabled(song.has_audio)
        self.play_btn.setToolTip(t("Play"))
        self.play_btn.clicked.connect(lambda: self.play.emit(self.song))
        layout.addWidget(self.play_btn)

        text = QVBoxLayout()
        text.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(song.title)
        self.title.setStyleSheet("font-weight:700;font-size:13.5px;")
        top.addWidget(self.title, 1)
        badge = QLabel(t(KIND_LABEL.get(song.kind, song.kind)))
        badge.setObjectName("Badge")
        top.addWidget(badge)
        text.addLayout(top)
        style = QLabel(song.style)
        style.setObjectName("Hint")
        style.setToolTip(song.style)
        style.setMaximumWidth(520 if not compact else 260)
        text.addWidget(style)
        meta_bits = [when(song.created)]
        if song.seconds:
            meta_bits.append(f"{int(song.seconds) // 60}:{int(song.seconds) % 60:02d}")
        if song.seed is not None:
            meta_bits.append(t("seed {n}", n=song.seed))
        meta = QLabel("  ·  ".join(b for b in meta_bits if b))
        meta.setObjectName("Counter")
        text.addWidget(meta)
        layout.addLayout(text, 1)

        self.star = QPushButton("★" if song.favourite else "☆")
        self.star.setFixedSize(32, 32)
        self.star.setToolTip(t("Favourite"))
        self.star.setStyleSheet(f"QPushButton{{border:none;background:transparent;font-size:18px;"
                                f"color:{theme.ACCENT if song.favourite else theme.DIM};padding:0;}}")
        self.star.clicked.connect(self._toggle_star)
        layout.addWidget(self.star)

        again = QPushButton("↻")
        again.setFixedWidth(40)
        again.setToolTip(t("Load this song's settings to make it again"))
        again.clicked.connect(lambda: self.reuse.emit(self.song))
        layout.addWidget(again)

        if song.score.is_file():
            score = QPushButton(t("Score"))
            score.setToolTip(t("See, play and edit the melody and chords"))
            score.clicked.connect(lambda: self.open_score.emit(self.song))
            layout.addWidget(score)
        more = QPushButton("⋯")
        more.setFixedWidth(40)
        more.setToolTip(t("More"))
        more.clicked.connect(lambda: self._menu(more))
        layout.addWidget(more)

    def _toggle_star(self) -> None:
        self.song.favourite = not self.song.favourite
        self.song.save()
        self.star.setText("★" if self.song.favourite else "☆")
        self.star.setStyleSheet(self.star.styleSheet().replace(
            theme.DIM if self.song.favourite else theme.ACCENT,
            theme.ACCENT if self.song.favourite else theme.DIM))
        self.changed.emit(self.song)

    def _menu(self, anchor) -> None:
        menu = QMenu(self)
        if self.song.score.is_file():
            menu.addAction(t("Use this score for a new song"), lambda: self.use_score.emit(self.song, "create"))
            menu.addAction(t("Use this score for a cover"), lambda: self.use_score.emit(self.song, "cover"))
        menu.addAction(t("Load settings to make it again"), lambda: self.reuse.emit(self.song))
        menu.addSeparator()
        if self.song.has_audio:
            menu.addAction(t("Export as MP3…"), lambda: self._export(".mp3"))
            menu.addAction(t("Export as WAV…"), lambda: self._export(".wav"))
            menu.addAction(t("Export as FLAC…"), lambda: self._export(".flac"))
        menu.addAction(t("Rename…"), self._rename)
        menu.addAction(t("Show in folder"), lambda: open_folder(self.song.folder))
        menu.addSeparator()
        menu.addAction(t("Move to trash"), lambda: self.delete.emit(self.song))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _export(self, suffix: str) -> None:
        name = songlib.slug(self.song.title, 60) + suffix
        start = str(Path.home() / "Music" / name)
        target, _ = QFileDialog.getSaveFileName(self, t("Export song"), start, f"*{suffix}")
        if not target:
            return
        if not target.lower().endswith(suffix):
            target += suffix
        try:
            songlib.export(self.song, Path(target))
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, t("Export"), t("Could not export: {e}", e=exc))
            return
        QMessageBox.information(self, t("Export"), t("Saved:\n{path}", path=target))

    def _rename(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, t("Rename"), t("Song title"), text=self.song.title)
        if ok and name.strip():
            self.song.title = name.strip()
            self.song.save()
            self.title.setText(self.song.title)
            self.changed.emit(self.song)
