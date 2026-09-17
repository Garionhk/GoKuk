"""Create: style + lyrics -> a finished song.

Left, the sound (style chips). Middle, the words. Right, the Create button,
progress, and the songs made in this session. Advanced choices are folded away
under "More options" so the first screen is only what a musician needs.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app import music
from app.i18n import N, t
from app.jobs import requests as rq
from app.jobs.engine import Job
from app.songs import Song, SongSettings, title_from_lyrics
from app.ui.lyrics_editor import LyricsEditor
from app.ui.progress_card import ProgressCard
from app.ui.song_card import SongCard
from app.ui.style_builder import StyleBuilder
from app.ui.widgets import Switch, heading, panel

def apply_style(builder: StyleBuilder, settings: SongSettings) -> None:
    """Restore the chips if the song recorded them, else the exact style line."""
    if settings.style_builder:
        builder.edit_btn.setChecked(False)
        builder.set_style(music.Style.from_dict(settings.style_builder))
    if not settings.style_builder or builder.style_text() != settings.style:
        builder.edit_btn.setChecked(True)
        builder.preview.setText(settings.style)


def write_input_score(song: Song) -> None:
    """Keep the score a run was given beside the song, so it can be loaded again."""
    score = song.extra.pop("_input_score", "")
    if score:
        try:
            (song.folder / Song.INPUT_SCORE).write_text(score, encoding="utf-8")
        except OSError:
            pass


PLANNING = [
    ("full", N("Compose melody and chords first (best)")),
    ("melody", N("Compose the melody only")),
    ("off", N("Sing straight away (fastest, less structured)")),
]


class CreateTab(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.cfg = window.cfg
        self._take_total = 0
        self._take_index = 0
        self._build()
        self._restore()

    # --- layout ----------------------------------------------------------
    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        # Sound
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(400)
        left_box, left = panel(16, 8)
        left.addWidget(QLabel(f"<span style='font-size:16px;font-weight:800'>{t('The sound')}</span>"))
        self.style_builder = StyleBuilder()
        left.addWidget(self.style_builder)
        left.addStretch(1)
        left_scroll.setWidget(left_box)
        layout.addWidget(left_scroll)

        # Words
        mid_box, mid = panel(16, 8)
        mid.addWidget(QLabel(f"<span style='font-size:16px;font-weight:800'>{t('The words')}</span>"))
        title_row = QHBoxLayout()
        title_row.addWidget(heading(t("Title")))
        self.title = QLineEdit()
        self.title.setPlaceholderText(t("optional - the first line is used if empty"))
        title_row.addWidget(self.title, 1)
        mid.addLayout(title_row)
        self.lyrics = LyricsEditor()
        mid.addWidget(self.lyrics, 1)
        layout.addWidget(mid_box, 1)

        # Go
        right = QVBoxLayout()
        right.setSpacing(10)
        opts_box, opts = panel(14, 8)
        self.more = Switch(t("More options"))
        opts.addWidget(self.more)
        self.options = QWidget()
        o = QVBoxLayout(self.options)
        o.setContentsMargins(0, 4, 0, 0)
        o.setSpacing(6)
        o.addWidget(heading(t("How to write it")))
        self.planning = QComboBox()
        for value, label in PLANNING:
            self.planning.addItem(t(label), value)
        o.addWidget(self.planning)
        self.plan_first = QCheckBox(t("Let me see and edit the score before singing"))
        o.addWidget(self.plan_first)

        o.addWidget(heading(t("Variation (seed)")))
        seed_row = QHBoxLayout()
        self.seed = QSpinBox()
        self.seed.setRange(1, 2**31 - 1)
        seed_row.addWidget(self.seed, 1)
        dice = QPushButton("🎲")
        dice.setFixedWidth(44)
        dice.setToolTip(t("New random seed"))
        dice.clicked.connect(lambda: self.seed.setValue(rq.new_seed()))
        seed_row.addWidget(dice)
        o.addLayout(seed_row)
        self.lock_seed = QCheckBox(t("Keep this seed (same seed + same settings = same song)"))
        o.addWidget(self.lock_seed)

        takes_row = QHBoxLayout()
        takes_row.addWidget(heading(t("Takes")))
        takes_row.addStretch(1)
        self.takes = QSpinBox()
        self.takes.setRange(1, 4)
        self.takes.setToolTip(t("Make several versions in a row, each with a different seed"))
        takes_row.addWidget(self.takes)
        o.addLayout(takes_row)

        strength_row = QHBoxLayout()
        strength_row.addWidget(heading(t("Follow the style")))
        strength_row.addStretch(1)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0.0, 3.0)
        self.strength.setSingleStep(0.1)
        self.strength.setSpecialValueText(t("Auto"))
        self.strength.setToolTip(t("How strictly to follow the style words. Auto is recommended."))
        strength_row.addWidget(self.strength)
        o.addLayout(strength_row)
        self.options.setVisible(False)
        self.more.toggled.connect(self.options.setVisible)
        opts.addWidget(self.options)
        right.addWidget(opts_box)

        self.score_banner, banner = panel(12, 6)
        banner_top = QHBoxLayout()
        banner_top.addWidget(heading(t("Using a score")))
        banner_top.addStretch(1)
        edit_score = QPushButton(t("Edit"))
        edit_score.setFixedHeight(28)
        edit_score.clicked.connect(self._edit_pending_score)
        banner_top.addWidget(edit_score)
        clear_score = QPushButton(t("Remove"))
        clear_score.setFixedHeight(28)
        clear_score.clicked.connect(self.clear_score)
        banner_top.addWidget(clear_score)
        banner.addLayout(banner_top)
        self.score_label = QLabel("")
        self.score_label.setObjectName("MonoAccent")
        self.score_label.setWordWrap(True)
        banner.addWidget(self.score_label)
        banner_hint = QLabel(t("The song follows this melody and these chords. Style and lyrics come from this tab."))
        banner_hint.setObjectName("Hint")
        banner_hint.setWordWrap(True)
        banner.addWidget(banner_hint)
        self.score_banner.setVisible(False)
        right.addWidget(self.score_banner)
        self.score_text = ""
        self.score_origin = ""

        self.create_btn = QPushButton(t("Create song"))
        self.create_btn.setObjectName("Primary")
        self.create_btn.setToolTip("Ctrl+Enter")
        self.create_btn.clicked.connect(self.create)
        right.addWidget(self.create_btn)
        self.busy_note = QLabel("")
        self.busy_note.setObjectName("Hint")
        self.busy_note.setWordWrap(True)
        right.addWidget(self.busy_note)

        self.progress = ProgressCard()
        self.progress.setVisible(False)
        self.progress.cancel_clicked.connect(self.window_.engine.cancel_all)
        right.addWidget(self.progress)

        right.addWidget(heading(t("Made this session")))
        self.results_host = QWidget()
        self.results = QVBoxLayout(self.results_host)
        self.results.setContentsMargins(0, 0, 0, 0)
        self.results.setSpacing(8)
        self.empty = QLabel(t("Your songs appear here. They are also saved in the Library."))
        self.empty.setObjectName("Hint")
        self.empty.setWordWrap(True)
        self.results.addWidget(self.empty)
        self.results.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.results_host)
        scroll.setFrameShape(QFrame.NoFrame)
        right.addWidget(scroll, 1)

        holder = QWidget()
        holder.setLayout(right)
        holder.setFixedWidth(430)
        layout.addWidget(holder)

    def _restore(self) -> None:
        self.style_builder.set_style(music.Style.from_dict(self.cfg.get("last_style")) if self.cfg.get("last_style")
                                     else music.Style())
        self.lyrics.set_lyrics(self.cfg.get("last_lyrics") or "")
        index = self.planning.findData(self.cfg.get("planning", "full"))
        self.planning.setCurrentIndex(max(0, index))
        self.seed.setValue(int(self.cfg.get("seed") or rq.new_seed()))
        self.lock_seed.setChecked(bool(self.cfg.get("seed_locked")))
        self.takes.setValue(int(self.cfg.get("takes") or 1))
        self.strength.setValue(float(self.cfg.get("cfg_scale") or 0.0))

    def remember(self) -> None:
        self.cfg.set("last_style", self.style_builder.style().to_dict())
        self.cfg.set("last_lyrics", self.lyrics.lyrics())
        self.cfg.set("planning", self.planning.currentData())
        self.cfg.set("seed", self.seed.value())
        self.cfg.set("seed_locked", self.lock_seed.isChecked())
        self.cfg.set("takes", self.takes.value())
        self.cfg.set("cfg_scale", self.strength.value() or None)

    def set_busy(self, busy: bool) -> None:
        self.create_btn.setEnabled(not busy)
        self.busy_note.setText(t("The engine is busy - wait for the current job, or press Stop.") if busy else "")

    # --- running ---------------------------------------------------------
    def _check_ready(self) -> bool:
        if self.window_.engine.busy:
            return False
        if not self.lyrics.lyrics():
            QMessageBox.information(self, t("Create song"), t("Write some lyrics first - or press Sample."))
            return False
        if not self.style_builder.style_text():
            QMessageBox.information(self, t("Create song"), t("Pick at least a language or a genre."))
            return False
        return True

    def create(self) -> None:
        if not self._check_ready():
            return
        self.remember()
        self.cfg.save()
        style, lyrics = self.style_builder.style_text(), self.lyrics.lyrics()
        title = self.title.text().strip() or title_from_lyrics(lyrics)
        if self.score_text:
            self._run(style=style, lyrics=lyrics, title=title, cot="full", seeds=self._seeds(self.takes.value()),
                      abc=self.score_text)
            return
        cot = self.planning.currentData()
        if self.plan_first.isChecked() and cot != "off":
            self._run(style=style, lyrics=lyrics, title=title, cot=cot, seeds=self._seeds(1), stage="plan")
            return
        self._run(style=style, lyrics=lyrics, title=title, cot=cot, seeds=self._seeds(self.takes.value()))

    def _seeds(self, count: int) -> list[int]:
        """A different seed for every take (see requests.take_seeds)."""
        seeds = rq.take_seeds(count, self.seed.value() if self.lock_seed.isChecked() else None)
        self.seed.setValue(seeds[0])
        return seeds

    def sing_score(self, song: Song, abc: str, *, style: str | None = None, lyrics: str | None = None) -> None:
        """Sing a given score (after 'plan first'). If another job is running it waits its turn."""
        if self.window_.engine.busy:
            self.busy_note.setText(t("Queued - it starts as soon as the current job finishes."))
        self._run(style=style or song.style, lyrics=lyrics or song.lyrics, title=song.title,
                  cot="full", seeds=[rq.new_seed()], abc=abc, parent=song.folder.name)

    def _run(self, *, style: str, lyrics: str, title: str, cot: str, seeds: list[int], stage: str = "audio",
             abc: str | None = None, parent: str = "", kind: str = "song") -> None:
        library = self.window_.library
        self._take_total, self._take_index = len(seeds), 0
        cfg_scale = self.strength.value() or None
        for index, seed in enumerate(seeds):
            folder = library.new_folder(title, "" if len(seeds) == 1 else f"-take{index + 1}")
            song = Song(folder, title=title if len(seeds) == 1 else f"{title} · {t('take {n}', n=index + 1)}",
                        kind="plan" if stage == "plan" else kind, style=style, lyrics=lyrics, seed=seed,
                        cot=cot, parent=parent)
            song.extra["style_builder"] = self.style_builder.style().to_dict()
            if abc:
                song.extra["_input_score"] = abc      # written beside the song when it is done
            data = rq.song_job(self.cfg, style=style, lyrics=lyrics, cot=cot, seed=seed, output_dir=folder,
                               abc=abc, stage=stage, cfg_scale=cfg_scale)
            skip = {"plan"} if abc else {"score"}
            self.window_.engine.submit(Job(
                "yue2", data,
                on_start=lambda i=index, sk=skip: self._on_start(i, sk),
                on_retry=lambda why, sk=skip: self.progress.start(self._retry_title(why), sk),
                on_stage=self.progress.on_stage,
                on_done=lambda event, s=song: self._on_done(s, event),
                on_fail=lambda event, s=song: self._on_fail(s, event)))

    @staticmethod
    def _retry_title(why: str) -> str:
        if why == "memory":
            return t("Out of graphics memory - trying again with less memory…")
        return t("Trying again in safe mode…")

    def _on_start(self, index: int, skip: set[str]) -> None:
        self._take_index = index
        label = t("Take {n} of {total}", n=index + 1, total=self._take_total) if self._take_total > 1 else ""
        self.progress.start(label, skip)

    def _on_done(self, song: Song, event: dict) -> None:
        song.created = datetime.now().isoformat(timespec="seconds")
        truncated = event.get("truncated")
        if isinstance(truncated, dict) and any(truncated.values()) or truncated is True:
            song.extra["truncated"] = True
        write_input_score(song)
        song.save()
        song = Song.load(song.folder) or song
        self.window_.library_changed()
        if song.kind == "plan":
            self.progress.finish(t("The score is ready"))
            # Open the editor once the engine has finished with this job. Opening it
            # here - inside the job's own "done" callback - kept the planning job
            # counted as running for as long as the window stayed open, so
            # "Sing it now" was refused with "the engine is busy".
            QTimer.singleShot(0, lambda s=song: self._open_plan(s))
            return
        self.progress.finish(t("Done - {title}", title=song.title))
        self._add_result(song)
        if self._take_index == 0 or not self.window_.player.song:
            self.window_.player.load(song)
        if song.extra.get("truncated"):
            self.busy_note.setText(t("The song reached the length limit and may end abruptly. "
                                     "Shorter lyrics help."))

    def _on_fail(self, song: Song, event: dict) -> None:
        self.window_.discard_folder(song.folder)
        if event.get("queued"):
            return
        message = rq.friendly_error(event)
        if event.get("kind") == "cancelled":
            self.progress.fail(t("Stopped"))
            return
        self.progress.fail(t("Could not finish"))
        self.window_.show_error(t("Create song"), message, event)

    def _open_plan(self, song: Song) -> None:
        try:
            abc = song.score.read_text(encoding="utf-8")
        except OSError:
            QMessageBox.warning(self, t("Create song"), t("The score could not be read."))
            return
        self.window_.edit_score(abc, title=t("Your score - {title}", title=song.title), origin=song.title,
                                actions=("sing", "cover"), primary="sing",
                                on_sing=lambda text: self.sing_score(song, text))

    def set_score(self, text: str, origin: str) -> None:
        """Make the next Create follow this score (melody and chords)."""
        from app.ui.score_editor import describe

        self.score_text, self.score_origin = text, origin
        self.score_label.setText(f"{origin}\n{describe(text)}")
        self.score_banner.setVisible(True)
        self.planning.setEnabled(False)
        self.plan_first.setEnabled(False)

    def clear_score(self) -> None:
        self.score_text, self.score_origin = "", ""
        self.score_banner.setVisible(False)
        self.planning.setEnabled(True)
        self.plan_first.setEnabled(True)

    def _edit_pending_score(self) -> None:
        self.window_.edit_score(self.score_text, title=t("Score"), origin=self.score_origin or t("Edited score"),
                                actions=("create", "cover"), primary="create")

    def _add_result(self, song: Song) -> None:
        self.empty.setVisible(False)
        card = self.window_.make_card(song, compact=True)
        self.results.insertWidget(0, card)

    def load_settings(self, settings: SongSettings) -> None:
        """Fill the tab with a past song's settings, ready to press Create again."""
        apply_style(self.style_builder, settings)
        self.lyrics.set_lyrics(settings.lyrics)
        self.title.setText(settings.title)
        if settings.seed:
            self.seed.setValue(int(settings.seed))
        self.strength.setValue(float(settings.cfg_scale or 0.0))
        self.planning.setCurrentIndex(max(0, self.planning.findData(settings.cot)))
        self.plan_first.setChecked(False)
        if settings.score and settings.kind != "plan":
            self.set_score(settings.score, t("Score from “{title}”", title=settings.title))
        else:
            self.clear_score()
        if settings.cfg_scale or settings.cot != "full":
            self.more.setChecked(True)
        self.busy_note.setText(t("Loaded the settings of “{title}”. Press Create song to make a new version - "
                                 "tick Keep this seed to make the same one.", title=settings.title))
