"""The lyrics box: section tags in orange, one-click tags, gentle hints."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from app import i18n, music
from app.i18n import t
from app.ui import theme
from app.ui.widgets import FlowLayout, heading


class SectionHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.tag = QTextCharFormat()
        self.tag.setForeground(QColor(theme.ACCENT))
        self.tag.setFontWeight(QFont.Bold)
        self.pattern = QRegularExpression(r"^\s*\[[^\]]+\]\s*$")

    def highlightBlock(self, text: str) -> None:
        if self.pattern.match(text).hasMatch():
            self.setFormat(0, len(text), self.tag)


class LyricsEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(heading(t("Lyrics")))
        top.addStretch(1)
        self.counter = QLabel("")
        self.counter.setObjectName("Counter")
        top.addWidget(self.counter)
        layout.addLayout(top)

        tags_row = QHBoxLayout()
        tags_host = QWidget()
        tags_host.setObjectName("Transparent")
        tags = FlowLayout(tags_host, spacing=6)
        for name in music.SECTIONS:
            button = QPushButton(f"[{name}]")
            button.setObjectName("Chip")
            button.setToolTip(t("Insert a [{name}] tag on a new line", name=name))
            button.clicked.connect(lambda _, n=name: self.insert_section(n))
            tags.addWidget(button)
        tags_row.addWidget(tags_host, 1)
        self.sample_btn = QPushButton(t("Sample"))
        self.sample_btn.setFixedHeight(30)
        self.sample_btn.setToolTip(t("Fill in example lyrics"))
        self.sample_btn.clicked.connect(self.load_sample)
        tags_row.addWidget(self.sample_btn, 0, Qt.AlignTop)
        layout.addLayout(tags_row)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(t("[Verse]\nWrite your first line here…\n\n[Chorus]\nThe part everyone sings…"))
        self.text.setTabChangesFocus(True)
        self.highlighter = SectionHighlighter(self.text.document())
        self.text.textChanged.connect(self._on_change)
        layout.addWidget(self.text, 1)

        self.notes = QLabel("")
        self.notes.setObjectName("Hint")
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        self._on_change()

    def lyrics(self) -> str:
        return self.text.toPlainText().strip()

    def set_lyrics(self, text: str) -> None:
        self.text.setPlainText(text)

    def insert_section(self, name: str) -> None:
        cursor = self.text.textCursor()
        block_text = cursor.block().text()
        prefix = "" if not block_text.strip() and cursor.positionInBlock() == 0 else "\n"
        if self.text.toPlainText() and cursor.atBlockStart() and not block_text.strip():
            # Keep a blank line between sections.
            before = cursor.block().previous().text().strip() if cursor.block().previous().isValid() else ""
            prefix = "\n" if before else ""
        cursor.insertText(f"{prefix}[{name}]\n")
        self.text.setTextCursor(cursor)
        self.text.setFocus()

    def load_sample(self) -> None:
        if self.lyrics():
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, t("Replace lyrics?"),
                                    t("Replace what you have written with the example lyrics?")) \
                    != QMessageBox.Yes:
                return
        code = i18n.current()
        self.set_lyrics(music.SAMPLE_LYRICS.get(code, music.SAMPLE_LYRICS["en"]))

    def _on_change(self) -> None:
        text = self.text.toPlainText()
        lines = [l for l in text.splitlines() if l.strip() and not music.SECTION_RE.match(l)]
        self.counter.setText(t("{s} sections · {l} lines", s=len(music.sections(text)), l=len(lines)))
        notes = music.check_lyrics(text) if text.strip() else []
        shown = []
        for note in notes[:3]:
            mark = "⚠" if note.level == "warn" else "•"
            shown.append(f"{mark}  " + t(note.text, line=note.line or "", name=note.name))
        self.notes.setText("\n".join(shown))
        self.changed.emit()
