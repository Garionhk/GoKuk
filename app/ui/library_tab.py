"""Library: every song Gokuk has made, newest first."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.i18n import N, t
from app.songs import Song
from app.ui.widgets import open_folder

FILTERS = [("all", N("All")), ("favourite", N("Favourites")), ("song", N("Songs")), ("cover", N("Covers")),
           ("plan", N("Scores only"))]


class LibraryTab(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search titles, styles and lyrics…"))
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        self.filter = QComboBox()
        for value, label in FILTERS:
            self.filter.addItem(t(label), value)
        self.filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.filter)
        self.count = QLabel("")
        self.count.setObjectName("Counter")
        top.addWidget(self.count)
        folder = QPushButton(t("Open songs folder"))
        folder.clicked.connect(lambda: open_folder(self.window_.library.root))
        top.addWidget(folder)
        layout.addLayout(top)

        self.host = QWidget()
        self.list = QVBoxLayout(self.host)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.host)
        layout.addWidget(scroll, 1)
        self.refresh()

    def refresh(self) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        query = self.search.text().strip().lower()
        kind = self.filter.currentData()
        all_songs = self.window_.library.songs()
        shown = [s for s in all_songs if self._matches(s, query, kind)]
        self.count.setText(t("{n} of {total}", n=len(shown), total=len(all_songs)))
        if not shown:
            empty = QLabel(t("No songs yet - make one on the Create tab.") if not all_songs
                           else t("Nothing matches."))
            empty.setObjectName("Hint")
            self.list.addWidget(empty)
        for song in shown:
            self.list.addWidget(self.window_.make_card(song))
        self.list.addStretch(1)

    @staticmethod
    def _matches(song: Song, query: str, kind: str) -> bool:
        if kind == "favourite" and not song.favourite:
            return False
        if kind in ("song", "cover", "plan") and song.kind != kind:
            return False
        if query:
            haystack = f"{song.title}\n{song.style}\n{song.lyrics}".lower()
            return all(word in haystack for word in query.split())
        return True
