"""The player along the bottom of the window: play, a waveform to click, time.

The waveform comes from peaks.json, written by the worker when the song is
saved, so the window draws it without decoding audio or needing numpy.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from app.i18n import t
from app.songs import Song
from app.ui import theme


def clock(ms: int) -> str:
    seconds = max(0, int(ms / 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Waveform(QWidget):
    seek = Signal(float)        # 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks: list[float] = []
        self.position = 0.0
        self.setMinimumHeight(38)
        self.setCursor(Qt.PointingHandCursor)

    def set_peaks(self, peaks: list[float]) -> None:
        self.peaks = peaks
        self.position = 0.0
        self.update()

    def set_position(self, fraction: float) -> None:
        self.position = max(0.0, min(1.0, fraction))
        self.update()

    def mousePressEvent(self, event) -> None:
        if self.width():
            self.seek.emit(event.position().x() / self.width())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and self.width():
            self.seek.emit(max(0.0, min(1.0, event.position().x() / self.width())))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if not self.peaks:
            painter.fillRect(QRectF(0, h / 2 - 1, w, 2), QColor(theme.LINE_2))
            return
        bars = max(1, w // 3)
        step = len(self.peaks) / bars
        top = max(self.peaks) or 1.0
        played = QColor(theme.ACCENT)
        rest = QColor(theme.LINE_2)
        for i in range(bars):
            chunk = self.peaks[int(i * step): int((i + 1) * step) or int(i * step) + 1]
            value = (max(chunk) if chunk else 0) / top
            bar_h = max(2.0, value * (h - 4))
            painter.fillRect(QRectF(i * 3, (h - bar_h) / 2, 2, bar_h),
                             played if (i / bars) < self.position else rest)


class PlayerBar(QFrame):
    #: Emitted whenever playback starts, pauses, stops or the source changes.
    state_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setStyleSheet(f"QFrame#PlayerBar{{background:{theme.SURFACE};border-top:1px solid {theme.LINE};}}")
        self.song: Song | None = None
        self.file_path = ""                 # set when playing a loose file (the Cover original)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(14)

        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("Primary")
        self.play_btn.setFixedSize(46, 46)
        self.play_btn.setStyleSheet("font-size:18px;border-radius:23px;padding:0;")
        self.play_btn.clicked.connect(self.toggle)
        layout.addWidget(self.play_btn)

        info = QVBoxLayout()
        info.setSpacing(0)
        self.title = QLabel(t("Nothing playing"))
        self.title.setStyleSheet("font-weight:700;font-size:13.5px;")
        self.title.setMaximumWidth(260)
        info.addWidget(self.title)
        self.subtitle = QLabel(t("Make a song, or pick one from the Library"))
        self.subtitle.setObjectName("Hint")
        self.subtitle.setMaximumWidth(260)
        info.addWidget(self.subtitle)
        layout.addLayout(info)

        self.now = QLabel("0:00")
        self.now.setObjectName("Mono")
        layout.addWidget(self.now)
        self.wave = Waveform()
        self.wave.seek.connect(self._seek)
        layout.addWidget(self.wave, 1)
        self.length = QLabel("0:00")
        self.length.setObjectName("Mono")
        layout.addWidget(self.length)

        vol = QLabel("🔊")
        vol.setObjectName("Hint")
        layout.addWidget(vol)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        layout.addWidget(self.volume)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda d: self.length.setText(clock(d)))
        self.player.playbackStateChanged.connect(self._on_state)
        self.play_btn.setEnabled(False)

    def load(self, song: Song, autoplay: bool = True) -> None:
        if not song.has_audio:
            return
        self.song = song
        self.file_path = ""
        self.title.setText(song.title)
        self.title.setToolTip(song.title)
        self.subtitle.setText(song.style)
        self.subtitle.setToolTip(song.style)
        self.wave.set_peaks(song.peaks())
        self.player.setSource(QUrl.fromLocalFile(str(song.audio)))
        self.play_btn.setEnabled(True)
        if autoplay:
            self.player.play()

    def load_file(self, path, title: str, subtitle: str = "") -> None:
        """Play any audio file (the original recording on the Cover tab)."""
        self.song = None
        self.file_path = str(path)
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.wave.set_peaks([])
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.play_btn.setEnabled(True)
        self.player.play()

    def release(self, song: Song) -> None:
        """Let go of a file (so it can be moved or deleted)."""
        if self.song and self.song.folder == song.folder:
            self.player.stop()
            self.player.setSource(QUrl())
            self.song = None
            self.title.setText(t("Nothing playing"))
            self.subtitle.setText("")
            self.wave.set_peaks([])
            self.play_btn.setEnabled(False)

    def stop(self) -> None:
        """Stop and go back to the start (unlike pause, which keeps the place)."""
        self.player.stop()

    def is_playing_file(self, path) -> bool:
        return (bool(self.file_path) and Path(self.file_path) == Path(path)
                and self.player.playbackState() == QMediaPlayer.PlayingState)

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _seek(self, fraction: float) -> None:
        if self.player.duration():
            self.player.setPosition(int(fraction * self.player.duration()))

    def _on_position(self, ms: int) -> None:
        self.now.setText(clock(ms))
        if self.player.duration():
            self.wave.set_position(ms / self.player.duration())

    def _on_state(self, state) -> None:
        self.play_btn.setText("❚❚" if state == QMediaPlayer.PlayingState else "▶")
        self.state_changed.emit()
