"""Cover: keep a recording's melody, give it new words and a new sound.

Three steps, each unlocked by the one before:

1. Choose a recording.
2. SheetSage2 listens and writes the melody down as a score (no chords, so the
   new arrangement is free to re-harmonise).
3. Style + lyrics, then YuE2 sings that melody with ``cot="melody"``.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app import music, paths
from app.i18n import t
from app.jobs import requests as rq
from app.jobs.engine import Job
from app.songs import Song, SongSettings, title_from_lyrics
from app.ui.create_tab import apply_style, write_input_score
from app.ui import theme
from app.ui.lyrics_editor import LyricsEditor
from app.score import abc_dialect
from app.ui.progress_card import ProgressCard
from app.ui.score_editor import describe
from app.ui.style_builder import StyleBuilder
from app.ui.widgets import heading, panel

AUDIO_TYPES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus"}
TRANSCRIBE_WEIGHT = {"load": 0.25, "transcribe_audio": 0.05, "transcribe_encoding": 0.2,
                     "transcribe_decoding": 0.4, "transcribe_notation": 0.1}


class AudioDrop(QFrame):
    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self._style(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.label = QLabel(t("Drop a song here, or click to choose"))
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-weight:700;")
        layout.addWidget(self.label)
        self.sub = QLabel("MP3 · WAV · FLAC · M4A")
        self.sub.setObjectName("Counter")
        self.sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.sub)

    def _style(self, hot: bool) -> None:
        colour = theme.ACCENT if hot else theme.LINE_2
        self.setStyleSheet(f"AudioDrop{{background:{theme.INPUT_BG};border:1px dashed {colour};border-radius:12px;}}")

    def set_file(self, path: str) -> None:
        p = Path(path)
        self.label.setText(p.name)
        size = p.stat().st_size / 2**20 if p.exists() else 0
        self.sub.setText(t("{size} MB · click to choose another", size=f"{size:.1f}"))

    def mousePressEvent(self, _event) -> None:
        start = str(Path.home() / "Music")
        path, _ = QFileDialog.getOpenFileName(self, t("Choose a recording"), start,
                                              t("Audio") + " (" + " ".join(f"*{e}" for e in sorted(AUDIO_TYPES)) + ")")
        if path:
            self.chosen.emit(path)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and Path(urls[0].toLocalFile()).suffix.lower() in AUDIO_TYPES:
            event.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, _event) -> None:
        self._style(False)

    def dropEvent(self, event) -> None:
        self._style(False)
        path = event.mimeData().urls()[0].toLocalFile()
        if Path(path).suffix.lower() in AUDIO_TYPES:
            self.chosen.emit(path)


def step_title(number: int, text: str) -> QLabel:
    label = QLabel(f"<span style='color:{theme.ACCENT};font-weight:800'>{number}</span>&nbsp;&nbsp;"
                   f"<span style='font-size:15px;font-weight:800'>{text}</span>")
    return label


class CoverTab(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.cfg = window.cfg
        self.source = ""
        self.score_text = ""
        self.score_origin = ""
        self._build()
        self._update_steps()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        # --- steps 1 and 2 ---------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(12)
        box1, s1 = panel(16, 8)
        s1.addWidget(step_title(1, t("Choose the original")))
        self.drop = AudioDrop()
        self.drop.chosen.connect(self._choose)
        s1.addWidget(self.drop)
        self.preview_btn = QPushButton(t("▶  Listen"))
        self.preview_btn.clicked.connect(self._preview)
        self.window_.player.state_changed.connect(self._update_listen)
        s1.addWidget(self.preview_btn)
        note = QLabel(t("Only use recordings you have the right to cover."))
        note.setObjectName("Hint")
        note.setWordWrap(True)
        s1.addWidget(note)
        left.addWidget(box1)

        box2, s2 = panel(16, 8)
        s2.addWidget(step_title(2, t("Write down the melody")))
        hint = QLabel(t("The AI listens to the song and writes its melody as a score. "
                        "Takes about a minute."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        s2.addWidget(hint)
        self.vocal_only = QCheckBox(t("Vocal melody only (ignore instrumental lines)"))
        s2.addWidget(self.vocal_only)
        self.transcribe_btn = QPushButton(t("Write down the melody"))
        self.transcribe_btn.setFixedHeight(40)
        self.transcribe_btn.clicked.connect(self._transcribe)
        s2.addWidget(self.transcribe_btn)
        self.t_progress = ProgressCard(rq.TRANSCRIBE_STAGES, TRANSCRIBE_WEIGHT)
        self.t_progress.setVisible(False)
        self.t_progress.cancel_clicked.connect(self.window_.engine.cancel_all)
        s2.addWidget(self.t_progress)
        self.score_info = QLabel("")
        self.score_info.setObjectName("MonoAccent")
        self.score_info.setWordWrap(True)
        s2.addWidget(self.score_info)
        self.score_warn = QLabel("")
        self.score_warn.setObjectName("Warning")
        self.score_warn.setWordWrap(True)
        s2.addWidget(self.score_warn)
        score_row = QHBoxLayout()
        self.view_score = QPushButton(t("See / edit the score"))
        self.view_score.clicked.connect(self._edit_score)
        score_row.addWidget(self.view_score, 1)
        load_score = QPushButton(t("Load a score file…"))
        load_score.setToolTip(t("Use a saved .abc score instead of a recording"))
        load_score.clicked.connect(self._load_score)
        score_row.addWidget(load_score)
        s2.addLayout(score_row)
        left.addWidget(box2)
        left.addStretch(1)
        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(380)
        layout.addWidget(holder)

        # --- step 3 ----------------------------------------------------------
        self.step3 = QWidget()
        s3_layout = QHBoxLayout(self.step3)
        s3_layout.setContentsMargins(0, 0, 0, 0)
        s3_layout.setSpacing(14)
        style_scroll = QScrollArea()
        style_scroll.setWidgetResizable(True)
        style_scroll.setFixedWidth(380)
        style_box, st = panel(16, 8)
        st.addWidget(step_title(3, t("The new sound")))
        self.style_builder = StyleBuilder()
        st.addWidget(self.style_builder)
        st.addStretch(1)
        style_scroll.setWidget(style_box)
        s3_layout.addWidget(style_scroll)

        words_box, w = panel(16, 8)
        w.addWidget(QLabel(f"<span style='font-size:15px;font-weight:800'>{t('The new words')}</span>"))
        tip = QLabel(t("Tip: keep roughly the same number of lines and syllables as the original, "
                       "so the words fit the melody."))
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        w.addWidget(tip)
        title_row = QHBoxLayout()
        title_row.addWidget(heading(t("Title")))
        self.title = QLineEdit()
        self.title.setPlaceholderText(t("optional"))
        title_row.addWidget(self.title, 1)
        w.addLayout(title_row)
        self.lyrics = LyricsEditor()
        w.addWidget(self.lyrics, 1)
        go_row = QHBoxLayout()
        go_row.setSpacing(10)
        takes_box = QVBoxLayout()
        takes_box.setSpacing(2)
        takes_box.addWidget(heading(t("Takes")))
        self.takes = QSpinBox()
        self.takes.setRange(1, 4)
        self.takes.setValue(int(self.cfg.get("cover_takes") or 1))
        self.takes.setToolTip(t("Make several versions in a row, each with a different seed"))
        self.takes.setFixedHeight(40)
        self.takes.valueChanged.connect(self._takes_changed)
        takes_box.addWidget(self.takes)
        go_row.addLayout(takes_box)
        self.cover_btn = QPushButton(t("Make cover"))
        self.cover_btn.setObjectName("Primary")
        self.cover_btn.setToolTip("Ctrl+Enter")
        self.cover_btn.clicked.connect(self.create)
        go_row.addWidget(self.cover_btn, 1)
        w.addLayout(go_row)
        self.progress = ProgressCard()
        self.progress.setVisible(False)
        self.progress.cancel_clicked.connect(self.window_.engine.cancel_all)
        w.addWidget(self.progress)

        # Takes finished in this session, newest first - the same cards as the Library.
        self.results_host = QWidget()
        self.results = QVBoxLayout(self.results_host)
        self.results.setContentsMargins(0, 0, 0, 0)
        self.results.setSpacing(6)
        self.results.addStretch(1)
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.NoFrame)
        self.results_scroll.setWidget(self.results_host)
        self.results_scroll.setMaximumHeight(230)
        self.results_scroll.setVisible(False)
        w.addWidget(self.results_scroll)
        self._take_total = 0
        s3_layout.addWidget(words_box, 1)
        layout.addWidget(self.step3, 1)

        if self.cfg.get("last_cover_style"):
            self.style_builder.set_style(music.Style.from_dict(self.cfg.get("last_cover_style")))

    # --- state -----------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_steps()

    def _update_steps(self) -> None:
        busy = getattr(self, "_busy", False)
        has_cover = self.window_.install.has("cover")
        self.preview_btn.setEnabled(bool(self.source))
        self.transcribe_btn.setEnabled(bool(self.source) and not busy and has_cover)
        if not has_cover:
            self.transcribe_btn.setToolTip(t("The Cover maker is not installed - add it with Gokuk Setup."))
        self.view_score.setEnabled(bool(self.score_text))
        self.step3.setEnabled(bool(self.score_text))
        self.cover_btn.setEnabled(bool(self.score_text) and not busy)

    def _choose(self, path: str) -> None:
        if self.source and self.window_.player.is_playing_file(self.source):
            self.window_.player.stop()          # don't keep playing the recording being replaced
        self.source = path
        self.drop.set_file(path)
        self.score_text = ""
        self.score_info.setText("")
        self.score_warn.setText("")
        self._update_steps()

    def _preview(self) -> None:
        """Listen / Stop: the same button starts the original recording and stops it."""
        player = self.window_.player
        if not self.source:
            return
        if player.is_playing_file(self.source):
            player.stop()
        else:
            player.load_file(Path(self.source), Path(self.source).stem, t("Original recording"))
        self._update_listen()

    def _update_listen(self) -> None:
        playing = bool(self.source) and self.window_.player.is_playing_file(self.source)
        self.preview_btn.setText(t("■  Stop") if playing else t("▶  Listen"))

    # --- step 2 ----------------------------------------------------------
    def _transcribe(self) -> None:
        if not self.source or self.window_.engine.busy:
            return
        folder = paths.cache_dir() / "scores" / f"{time.strftime('%Y%m%d-%H%M%S')}"
        data = rq.transcribe_job(audio=self.source, output_dir=folder, vocal_only=self.vocal_only.isChecked())
        self.score_text = ""
        self.score_info.setText("")
        self.score_warn.setText("")
        self._update_steps()
        self.window_.engine.submit(Job(
            "sheetsage", data,
            on_start=lambda: self.t_progress.start(t("Loading the listener…")),
            on_stage=self.t_progress.on_stage,
            on_done=self._transcribed, on_fail=self._transcribe_failed))

    def _transcribed(self, event: dict) -> None:
        try:
            self.score_text = Path(event["score"]).read_text(encoding="utf-8")
        except (OSError, KeyError):
            self._transcribe_failed({"kind": "no_score", "message": "score.abc missing"})
            return
        self.score_origin = Path(self.source).stem
        self.t_progress.finish(t("Melody written down"))
        self.score_info.setText(describe(self.score_text) or t("Score ready"))
        warnings = event.get("warnings") or []
        if warnings:
            self.score_warn.setText("⚠  " + t("The listener noted: {w}", w="; ".join(warnings[:2])))
        self._update_steps()
        self.lyrics.text.setFocus()

    def _transcribe_failed(self, event: dict) -> None:
        if event.get("kind") == "cancelled":
            self.t_progress.fail(t("Stopped"))
        else:
            self.t_progress.fail(t("Could not write down the melody"))
            self.window_.show_error(t("Cover"), rq.friendly_error(event), event)
        self._update_steps()

    def _edit_score(self) -> None:
        # The same editor, title and buttons as on the Create tab.
        origin = self.score_origin or t("Edited score")
        self.window_.edit_score(self.score_text, title=t("Score - {title}", title=origin), origin=origin,
                                actions=("create", "cover"), primary="cover")

    def _load_score(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Open a score"), str(Path.home()), t("ABC score") + " (*.abc)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            abc_dialect.parse(text)
        except (OSError, UnicodeDecodeError, abc_dialect.AbcError) as error:
            QMessageBox.warning(self, t("Open a score"), t("This file is not a score Gokuk can use: {why}", why=error))
            return
        self.set_score(text, Path(path).name)

    def set_score(self, text: str, origin: str) -> None:
        """Use this score for the cover (a recording is then optional).

        The score is kept whole - chords included - so it looks and plays the same
        in the editor as on the Create tab. Chords are removed only when the cover
        is sung (see ``create``).
        """
        self.score_text = text
        self.score_origin = origin
        self.score_info.setText(describe(text) or t("Score ready"))
        self.score_warn.setText(t("Using a score from: {origin}", origin=origin))
        self._update_steps()

    def load_settings(self, settings: SongSettings) -> None:
        """Fill the tab with a past cover's settings, ready to press Make cover again."""
        if settings.source_path and Path(settings.source_path).is_file():
            self._choose(settings.source_path)
        if settings.score:
            self.set_score(settings.score, settings.source_name or settings.title)
        apply_style(self.style_builder, settings)
        self.lyrics.set_lyrics(settings.lyrics)
        self.title.setText(settings.title)
        self.score_warn.setText(t("Loaded the settings of “{title}”. Press Make cover to make a new version.",
                                  title=settings.title))

    # --- step 3 ----------------------------------------------------------
    def create(self) -> None:
        if not self.score_text or self.window_.engine.busy:
            return
        if not self.lyrics.lyrics():
            QMessageBox.information(self, t("Make cover"), t("Write the new lyrics first."))
            return
        style, lyrics = self.style_builder.style_text(), self.lyrics.lyrics()
        self.cfg.set("last_cover_style", self.style_builder.style().to_dict())
        self.cfg.save()
        title = self.title.text().strip() or title_from_lyrics(lyrics)
        # cot="melody" follows the melody and lets the new style re-harmonise, which
        # is what YuE2 expects a chord-free score for.
        try:
            melody = abc_dialect.strip_chords(self.score_text)
        except abc_dialect.AbcError as error:
            self.window_.show_error(t("Make cover"), t("The score cannot be used yet: {why}", why=str(error)),
                                    {"message": str(error)})
            return
        takes = self.takes.value()
        self._take_total = takes
        # One job per take, queued on the engine; each take gets its own seed and folder.
        for index in range(takes):
            take_title = title if takes == 1 else f"{title} · {t('take {n}', n=index + 1)}"
            folder = self.window_.library.new_folder(title if takes == 1 else f"{title}-take{index + 1}")
            seed = rq.new_seed()
            song = Song(folder, title=take_title, kind="cover", style=style, lyrics=lyrics, seed=seed,
                        cot="melody", source_audio=Path(self.source).name)
            song.extra["style_builder"] = self.style_builder.style().to_dict()
            song.extra["source_path"] = self.source
            song.extra["_input_score"] = self.score_text      # the full score, chords included
            data = rq.song_job(self.cfg, style=style, lyrics=lyrics, cot="melody", seed=seed,
                               output_dir=folder, abc=melody)
            self.window_.engine.submit(Job(
                "yue2", data,
                on_start=lambda i=index: self.progress.start(self._take_label(i), skip={"plan"}),
                on_retry=lambda why: self.progress.start(
                    t("Out of graphics memory - trying again with less memory…") if why == "memory"
                    else t("Trying again in safe mode…"), skip={"plan"}),
                on_stage=self.progress.on_stage,
                on_done=lambda event, s=song, i=index: self._done(s, event, i),
                on_fail=lambda event, s=song: self._failed(s, event)))

    def _take_label(self, index: int) -> str:
        if self._take_total <= 1:
            return ""
        return t("Take {n} of {total}", n=index + 1, total=self._take_total)

    def _takes_changed(self, value: int) -> None:
        self.cfg.set("cover_takes", value)
        self.cfg.save()

    def _done(self, song: Song, event: dict, index: int = 0) -> None:
        song.created = datetime.now().isoformat(timespec="seconds")
        write_input_score(song)
        song.save()
        song = Song.load(song.folder) or song
        self.progress.finish(t("Done - {title}", title=song.title))
        self.window_.library_changed()
        self.results_scroll.setVisible(True)
        self.results.insertWidget(0, self.window_.make_card(song, compact=True))
        if index == 0 or not self.window_.player.song:
            self.window_.player.load(song)             # the first take plays; later ones wait in the list

    def _failed(self, song: Song, event: dict) -> None:
        self.window_.discard_folder(song.folder)
        if event.get("queued"):                        # a later take dropped by Stop
            return
        if event.get("kind") == "cancelled":
            self.progress.fail(t("Stopped"))
            return
        self.progress.fail(t("Could not finish"))
        self.window_.show_error(t("Make cover"), rq.friendly_error(event), event)
