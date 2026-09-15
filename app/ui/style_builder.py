"""Build a YuE2 style line by tapping chips, and see the line it makes."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from app import music
from app.i18n import t
from app.ui.widgets import FlowLayout, heading


class ChipGroup(QWidget):
    """A wrapping row of pick-one or pick-several chips."""

    changed = Signal()

    def __init__(self, options: list[tuple[str, str]], multi: bool = False, limit: int = 0,
                 allow_none: bool = False, parent=None):
        super().__init__(parent)
        self.multi, self.limit, self.allow_none = multi, limit, allow_none
        self.buttons: dict[str, QPushButton] = {}
        self._order: list[str] = []          # pick order, so the first pick leads
        self.setObjectName("Transparent")
        flow = FlowLayout(self, spacing=6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        for value, label in options:
            button = QPushButton(t(label).replace("&", "&&"))   # "&" would be a shortcut marker
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked, v=value: self._clicked(v, checked))
            flow.addWidget(button)
            self.buttons[value] = button

    def _clicked(self, value: str, checked: bool) -> None:
        if not self.multi:
            if not checked and not self.allow_none:
                self.buttons[value].setChecked(True)
                return
            for other, button in self.buttons.items():
                if other != value:
                    button.setChecked(False)
            self._order = [value] if checked else []
        else:
            if checked:
                self._order.append(value)
                if self.limit and len(self._order) > self.limit:
                    dropped = self._order.pop(0)
                    self.buttons[dropped].setChecked(False)
            elif value in self._order:
                self._order.remove(value)
        self.changed.emit()

    def values(self) -> list[str]:
        return [v for v in self._order if self.buttons[v].isChecked()]

    def value(self) -> str:
        values = self.values()
        return values[0] if values else ""

    def set_values(self, values: list[str] | str) -> None:
        if isinstance(values, str):
            values = [values] if values else []
        self._order = [v for v in values if v in self.buttons]
        for value, button in self.buttons.items():
            button.setChecked(value in self._order)


class StyleBuilder(QWidget):
    changed = Signal(str)

    def __init__(self, compact: bool = False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        def section(title: str, widget: QWidget, hint: str = "") -> None:
            row = QHBoxLayout()
            row.addWidget(heading(title))
            if hint:
                note = QLabel(hint)
                note.setObjectName("Counter")
                row.addWidget(note)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addWidget(widget)
            layout.addSpacing(2)

        self.language = ChipGroup(music.LANGUAGES)
        section(t("Language"), self.language)
        self.genres = ChipGroup(music.GENRES, multi=True, limit=2)
        section(t("Genre"), self.genres, t("up to 2"))
        self.moods = ChipGroup(music.MOODS, multi=True, limit=2)
        section(t("Mood"), self.moods, t("up to 2"))
        self.vocal = ChipGroup(music.VOCALS)
        section(t("Voice"), self.vocal)
        self.instruments = ChipGroup(music.INSTRUMENTS, multi=True, limit=4)
        section(t("Instruments"), self.instruments, t("up to 4"))

        bpm_row = QHBoxLayout()
        bpm_row.addWidget(heading(t("Tempo")))
        bpm_row.addStretch(1)
        self.bpm_auto = QCheckBox(t("Let the AI choose"))
        bpm_row.addWidget(self.bpm_auto)
        self.bpm_value = QLabel("")
        self.bpm_value.setObjectName("MonoAccent")
        self.bpm_value.setMinimumWidth(64)
        self.bpm_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bpm_row.addWidget(self.bpm_value)
        layout.addLayout(bpm_row)
        self.bpm = QSlider(Qt.Horizontal)
        self.bpm.setRange(music.BPM_MIN, music.BPM_MAX)
        self.bpm.setValue(music.BPM_DEFAULT)
        layout.addWidget(self.bpm)

        layout.addWidget(heading(t("Extra style words")))
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(t("e.g. 90s vibe, big chorus, vinyl crackle"))
        layout.addWidget(self.extra)

        layout.addSpacing(4)
        preview_row = QHBoxLayout()
        preview_row.addWidget(heading(t("What the AI reads")))
        preview_row.addStretch(1)
        self.edit_btn = QPushButton(t("Write my own"))
        self.edit_btn.setCheckable(True)
        self.edit_btn.setFixedHeight(26)
        self.edit_btn.toggled.connect(self._toggle_manual)
        preview_row.addWidget(self.edit_btn)
        layout.addLayout(preview_row)
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName("StylePreview")
        self.preview.setStyleSheet("font-family:'Consolas';font-size:12px;")
        self.preview.textEdited.connect(lambda text: self.changed.emit(text))
        layout.addWidget(self.preview)

        for group in (self.language, self.genres, self.moods, self.vocal, self.instruments):
            group.changed.connect(self._update)
        self.bpm.valueChanged.connect(self._update)
        self.bpm_auto.toggled.connect(self._update)
        self.extra.textChanged.connect(self._update)
        self.set_style(music.Style())

    def _toggle_manual(self, manual: bool) -> None:
        """Advanced users can type the style line directly."""
        self.preview.setReadOnly(not manual)
        for w in (self.language, self.genres, self.moods, self.vocal, self.instruments,
                  self.bpm, self.bpm_auto, self.extra):
            w.setEnabled(not manual)
        if not manual:
            self._update()
        else:
            self.preview.setFocus()

    def style(self) -> music.Style:
        return music.Style(
            language=self.language.value(), genres=self.genres.values(), moods=self.moods.values(),
            vocal=self.vocal.value(), instruments=self.instruments.values(),
            bpm=None if self.bpm_auto.isChecked() else self.bpm.value(), extra=self.extra.text())

    def style_text(self) -> str:
        return self.preview.text().strip()

    def set_style(self, style: music.Style) -> None:
        blockers = [self.language, self.genres, self.moods, self.vocal, self.instruments,
                    self.bpm, self.bpm_auto, self.extra]
        for w in blockers:
            w.blockSignals(True)
        self.language.set_values(style.language)
        self.genres.set_values(style.genres)
        self.moods.set_values(style.moods)
        self.vocal.set_values(style.vocal)
        self.instruments.set_values(style.instruments)
        self.bpm_auto.setChecked(style.bpm is None)
        if style.bpm:
            self.bpm.setValue(int(style.bpm))
        self.extra.setText(style.extra)
        for w in blockers:
            w.blockSignals(False)
        self._update()

    def _update(self) -> None:
        self.bpm.setEnabled(not self.bpm_auto.isChecked() and not self.edit_btn.isChecked())
        self.bpm_value.setText("" if self.bpm_auto.isChecked() else f"{self.bpm.value()} BPM")
        if self.edit_btn.isChecked():
            return
        text = self.style().compose()
        self.preview.setText(text)
        self.preview.setCursorPosition(0)
        self.changed.emit(text)
