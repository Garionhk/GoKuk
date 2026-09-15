"""Small widgets shared by Gokuk and Gokuk Setup (ported from EasyAI)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QLayout, QVBoxLayout

from app.ui import theme


def open_in_explorer(path: str | Path) -> None:
    """Open Explorer with this file selected (one ``/select,`` argument)."""
    path = Path(path)
    if not path.exists():
        open_folder(path.parent)
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        open_folder(path.parent)


def open_folder(path: str | Path) -> None:
    """Open a folder itself, rather than selecting something inside it."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer "{path}"')
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class FlowLayout(QLayout):
    """Lays widgets left to right, wrapping onto a new line when out of room."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, apply: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class Switch(QCheckBox):
    """A tick box drawn as a sliding switch; still a QCheckBox underneath."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QCheckBox{{color:{theme.TEXT};font-size:14px;font-weight:600;spacing:10px;}}"
            f"QCheckBox:disabled{{color:{theme.MUTED};}}"
            f"QCheckBox::indicator{{width:34px;height:19px;border-radius:10px;"
            f"border:1px solid {theme.LINE_2};background:{theme.SURFACE_2};}}"
            f"QCheckBox::indicator:checked{{background:{theme.ACCENT};"
            f"border-color:{theme.ACCENT};}}"
            f"QCheckBox::indicator:checked:disabled{{background:{theme.ACCENT_DOWN};"
            f"border-color:{theme.ACCENT_DOWN};}}")


def heading(text: str) -> QLabel:
    """A small uppercase letter-spaced section label."""
    label = QLabel(text.upper())
    label.setObjectName("Heading")
    return label


def panel(margins: int = 16, spacing: int = 10) -> tuple[QFrame, QVBoxLayout]:
    """A rounded surface with a vertical layout inside."""
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return frame, layout
