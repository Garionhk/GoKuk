"""See and edit a score as a piano roll with a chord chart, then use it.

Layout, top to bottom inside the scrolling canvas:

    ruler      bar numbers - click to move the playhead, right-click for bars
    sections   VERSE / CHORUS bands - double-click to rename or start one
    chords     chord symbols - click to type one, right-click to remove
    roll       note blocks against a keyboard, one colour per voice

The keyboard and the three header lanes stay pinned while the roll scrolls:
the canvas paints them at the edge of whatever part is visible.

Edits never touch the score while the mouse is down. A drag moves a ghost;
releasing commits one edit (so undo is one step and neighbours are only trimmed
where the note finally lands).

Nothing leaves this window unless ``abc_write.to_abc`` has written it and
re-read it back identical, so an edit can never send YuE2 a score that means
something else.
"""
from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app import paths
from app.i18n import N, t
from app.score import abc_dialect, preview, theory
from app.score.abc_write import WriteError, to_abc
from app.score.model import History, Note, Score, UnsupportedScore
from app.ui import theme
from app.ui.widgets import heading

F = Fraction
KEY_W = 58
RULER_H, SECTION_H, CHORD_H = 22, 24, 28
HEAD_H = RULER_H + SECTION_H + CHORD_H
ROW_H = 13
EDGE_PX = 6

SECTION_PRESETS = [N("intro"), N("verse"), N("pre-chorus"), N("chorus"), N("bridge"),
                   N("interlude"), N("solo"), N("outro")]
SNAPS = [(F(1), N("1/4")), (F(1, 2), N("1/8")), (F(1, 4), N("1/16"))]
VOICE_LABELS = {"Vocal": N("Vocal"), "Ins": N("Instrument")}


def describe(text_or_score) -> str:
    """'D major · 86 BPM · 65 bars · 3:01' for a score or its ABC text."""
    try:
        score = text_or_score if isinstance(text_or_score, Score) else Score.from_abc(text_or_score)
    except (abc_dialect.AbcError, UnsupportedScore, ValueError):
        return ""
    seconds = int(score.seconds)
    keys = key_name(score.key)
    if score.key_changes:
        keys += " → " + " → ".join(key_name(k) for _, k in score.key_changes[:2])
        if len(score.key_changes) > 2:
            keys += " …"
    return "  ·  ".join([keys, f"{score.bpm} BPM", t("{n} bars", n=score.bars),
                         f"{seconds // 60}:{seconds % 60:02d}"])


def key_name(key: str) -> str:
    name = theory.tonic(key).replace("#", "♯").replace("b", "♭")
    return t("{name} minor", name=name) if theory.is_minor(key) else t("{name} major", name=name)


# ---------------------------------------------------------------------------
class RollCanvas(QWidget):
    changed = Signal()            # after every committed edit
    seek = Signal(object)         # Fraction: playhead moved by the user

    def __init__(self, history: History, parent=None):
        super().__init__(parent)
        self.history = history
        self.voice = "Vocal"
        self.snap = F(1, 2)
        self.ppq = 36.0                     # pixels per quarter note
        self.playhead = F(0)
        self.selected: list[Note] = []
        self._drag = None                   # dict describing the gesture in progress
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._fit_range()

    # --- geometry --------------------------------------------------------
    @property
    def score(self) -> Score:
        return self.history.score

    def _fit_range(self) -> None:
        pitches = [n.pitch for v in self.score.voices.values() for n in v] or [60, 72]
        self.top = min(108, max(max(pitches) + 4, 79))
        self.bottom = max(24, min(min(pitches) - 4, 48))
        self._resize()

    def _resize(self) -> None:
        width = KEY_W + int(float(self.score.length) * self.ppq) + 240
        height = HEAD_H + (self.top - self.bottom + 1) * ROW_H + 4
        self.setMinimumSize(width, height)
        self.resize(width, height)

    def set_zoom(self, ppq: float) -> None:
        self.ppq = ppq
        self._resize()
        self.update()

    def x_of(self, time) -> float:
        return KEY_W + float(time) * self.ppq

    def time_of(self, x: float) -> Fraction:
        return F(max(0.0, (x - KEY_W) / self.ppq)).limit_denominator(64)

    def y_of(self, pitch: int) -> int:
        return HEAD_H + (self.top - pitch) * ROW_H

    def pitch_of(self, y: float) -> int:
        return max(self.bottom, min(self.top, self.top - int((y - HEAD_H) // ROW_H)))

    def snapped(self, time: Fraction) -> Fraction:
        return F(int(time / self.snap)) * self.snap

    def _visible(self) -> QRect:
        return self.visibleRegion().boundingRect()

    def note_rect(self, note: Note) -> QRectF:
        return QRectF(self.x_of(note.onset), self.y_of(note.pitch) + 1,
                      max(3.0, float(note.duration) * self.ppq - 1), ROW_H - 2)

    def ensure_visible(self, time: Fraction) -> None:
        scroll = self.parent().parent() if self.parent() else None
        if isinstance(scroll, QScrollArea):
            scroll.ensureVisible(int(self.x_of(time)), int(self._visible().center().y()), 200, 0)

    # --- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        view = self._visible()
        score = self.score
        p.fillRect(view, QColor(theme.INPUT_BG))

        # Rows: black keys darker, C lines stronger.
        for pitch in range(self.bottom, self.top + 1):
            y = self.y_of(pitch)
            if y + ROW_H < view.top() or y > view.bottom():
                continue
            black = pitch % 12 in (1, 3, 6, 8, 10)
            p.fillRect(QRect(view.left(), y, view.width(), ROW_H), QColor("#0a0a0d" if black else "#101014"))
            if pitch % 12 == 0:
                p.fillRect(QRect(view.left(), y + ROW_H - 1, view.width(), 1), QColor(theme.LINE_2))

        # Grid: snap lines, beats, bars (bars can differ in length).
        end_x = self.x_of(score.length)
        starts = score.bar_starts
        for bar in range(score.bars):
            if self.x_of(starts[bar + 1]) < view.left() or self.x_of(starts[bar]) > view.right():
                continue
            offset = F(0)
            while starts[bar] + offset < starts[bar + 1]:
                x = int(self.x_of(starts[bar] + offset))
                colour = QColor(theme.LINE_2) if offset == 0 else QColor(theme.LINE) if offset % 1 == 0 \
                    else QColor("#17171c")
                p.fillRect(QRect(x, HEAD_H, 1, self.height() - HEAD_H), colour)
                offset += self.snap
        p.fillRect(QRectF(end_x, HEAD_H, max(0, self.width() - end_x), self.height()), QColor("#050506"))

        # Notes: the voice being edited on top, in the accent.
        other = "Ins" if self.voice == "Vocal" else "Vocal"
        font = QFont(theme.MONO_FONT)
        font.setPixelSize(9)
        p.setFont(font)
        for note in score.voices[other]:
            rect = self.note_rect(note)
            if rect.right() < view.left() or rect.left() > view.right():
                continue
            p.setPen(QPen(QColor(theme.DIM), 1))
            p.setBrush(QColor(95, 95, 106, 60))
            p.drawRoundedRect(rect, 2, 2)
        fill = QColor(theme.ACCENT if self.voice == "Vocal" else theme.ACCENT_2)
        ghost = self._ghost()
        for note in score.voices[self.voice]:
            if ghost and note is ghost[0]:
                continue
            self._draw_note(p, note, fill, view, note in self.selected)
        if ghost:
            _, g = ghost
            colour = QColor(fill)
            colour.setAlpha(170)
            self._draw_note(p, g, colour, view, True)
        if self._drag and self._drag["kind"] == "band":
            p.setPen(QPen(QColor(theme.TEXT), 1, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawRect(QRect(self._drag["start"], self._drag["now"]).normalized())

        # Playhead.
        px = int(self.x_of(self.playhead))
        p.fillRect(QRect(px, HEAD_H, 2, self.height()), QColor(theme.ACCENT))

        self._paint_header(p, view)
        self._paint_keyboard(p, view)

    def _draw_note(self, p: QPainter, note: Note, colour: QColor, view: QRect, selected: bool) -> None:
        rect = self.note_rect(note)
        if rect.right() < view.left() or rect.left() > view.right():
            return
        p.setPen(QPen(QColor(theme.TEXT), 1.5) if selected else Qt.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(rect, 2.5, 2.5)
        label = theory.note_name(note.pitch, self.score.key_at(self.score.bar_of(note.onset)))
        if rect.width() > 26:
            p.setPen(QColor(theme.ACCENT_INK))
            p.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, label)

    def _paint_header(self, p: QPainter, view: QRect) -> None:
        score = self.score
        top = view.top()
        head = QRect(view.left(), top, view.width(), HEAD_H)
        p.fillRect(head, QColor(theme.SURFACE))
        p.fillRect(QRect(view.left(), top + HEAD_H - 1, view.width(), 1), QColor(theme.LINE_2))
        small = QFont(theme.MONO_FONT)
        small.setPixelSize(10)
        bold = QFont(theme.UI_FONT)
        bold.setPixelSize(11)
        bold.setBold(True)
        chord_font = QFont(theme.UI_FONT)
        chord_font.setPixelSize(13)
        chord_font.setBold(True)

        # Ruler.
        p.setFont(small)
        starts = score.bar_starts
        for bar in range(score.bars + 1):
            x = int(self.x_of(starts[bar]))
            if x < view.left() - 60 or x > view.right() + 2:
                continue
            p.fillRect(QRect(x, top, 1, HEAD_H), QColor(theme.LINE))
            if bar < score.bars:
                label = str(bar + 1)
                if bar == 0 or score.meters[bar] != score.meters[bar - 1]:
                    label += f"  {score.meters[bar][0]}/{score.meters[bar][1]}"
                if bar > 0 and score.key_at(bar) != score.key_at(bar - 1):
                    label += "  " + t("Key {key}", key=key_name(score.key_at(bar)))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRect(x + 4, top, 220, RULER_H), Qt.AlignVCenter, label)

        # Sections.
        sections = sorted(score.sections)
        y = top + RULER_H
        for index, (bar, name) in enumerate(sections):
            end_bar = sections[index + 1][0] if index + 1 < len(sections) else score.bars
            x0, x1 = self.x_of(starts[bar]), self.x_of(starts[end_bar])
            if x1 < view.left() or x0 > view.right():
                continue
            rect = QRectF(x0 + 1, y + 2, x1 - x0 - 2, SECTION_H - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE_3 if index % 2 else theme.SURFACE_2))
            p.drawRoundedRect(rect, 4, 4)
            # Keep the name readable when the part starts off-screen, but never
            # let it spill into the next part.
            label_x = max(rect.left() + 6, view.left() + KEY_W + 6)
            label = QRectF(label_x, rect.top(), rect.right() - label_x - 4, rect.height())
            if label.width() > 12:
                p.save()
                p.setClipRect(label)
                p.setFont(bold)
                p.setPen(QColor(theme.TEXT))
                p.drawText(label, Qt.AlignVCenter, t(name).upper() if name in SECTION_PRESETS else name.upper())
                p.restore()

        # Chords.
        y = top + RULER_H + SECTION_H
        p.setFont(chord_font)
        for onset, symbol in score.chords:
            x = self.x_of(onset)
            if x < view.left() - 80 or x > view.right():
                continue
            p.fillRect(QRectF(x, y + 4, 2, CHORD_H - 8), QColor(theme.ACCENT))
            p.setPen(QColor(theme.ACCENT_2))
            p.drawText(QRectF(x + 5, y, 90, CHORD_H), Qt.AlignVCenter, symbol.replace("b", "♭").replace("#", "♯"))

        # Corner label over the keyboard.
        corner = QRect(view.left(), top, KEY_W, HEAD_H)
        p.fillRect(corner, QColor(theme.SURFACE))
        p.setFont(small)
        p.setPen(QColor(theme.DIM))
        p.drawText(QRect(view.left() + 6, top, KEY_W, RULER_H), Qt.AlignVCenter, t("Bar"))
        p.drawText(QRect(view.left() + 6, top + RULER_H, KEY_W, SECTION_H), Qt.AlignVCenter, t("Part"))
        p.drawText(QRect(view.left() + 6, top + RULER_H + SECTION_H, KEY_W, CHORD_H), Qt.AlignVCenter, t("Chord"))

    def _paint_keyboard(self, p: QPainter, view: QRect) -> None:
        left = view.left()
        font = QFont(theme.MONO_FONT)
        font.setPixelSize(9)
        p.setFont(font)
        for pitch in range(self.bottom, self.top + 1):
            y = self.y_of(pitch)
            if y < view.top() + HEAD_H - ROW_H or y > view.bottom():
                continue
            black = pitch % 12 in (1, 3, 6, 8, 10)
            rect = QRect(left, y, KEY_W, ROW_H)
            p.fillRect(rect, QColor("#1b1b21" if black else "#d9d9de"))
            p.fillRect(QRect(left, y + ROW_H - 1, KEY_W, 1), QColor("#2a2a31" if black else "#9a9aa3"))
            if pitch % 12 == 0 or pitch in (self.top, self.bottom):
                p.setPen(QColor("#111" if not black else theme.MUTED))
                p.drawText(rect.adjusted(4, 0, 0, 0), Qt.AlignVCenter, theory.note_name(pitch, "C"))
        if view.top() + HEAD_H > self.y_of(self.top):
            p.fillRect(QRect(left, view.top(), KEY_W, HEAD_H), QColor(theme.SURFACE))
        p.fillRect(QRect(left + KEY_W - 1, view.top(), 1, view.height()), QColor(theme.LINE_2))

    # --- hit testing -----------------------------------------------------
    def _lane(self, pos: QPoint) -> str:
        view = self._visible()
        if pos.x() < view.left() + KEY_W:
            return "keys"
        y = pos.y() - view.top()
        if y < RULER_H:
            return "ruler"
        if y < RULER_H + SECTION_H:
            return "sections"
        if y < HEAD_H:
            return "chords"
        return "roll"

    def _note_under(self, pos: QPoint) -> tuple[Note | None, bool]:
        for note in reversed(self.score.voices[self.voice]):
            rect = self.note_rect(note)
            if rect.contains(pos):
                return note, pos.x() >= rect.right() - EDGE_PX
        return None, False

    def _chord_near(self, pos: QPoint) -> tuple[Fraction, str] | None:
        for onset, symbol in self.score.chords:
            x = self.x_of(onset)
            if x - 4 <= pos.x() <= x + 60:
                return onset, symbol
        return None

    # --- gestures --------------------------------------------------------
    def _ghost(self):
        drag = self._drag
        if not drag or drag["kind"] not in ("move", "resize", "new"):
            return None
        return drag.get("note"), drag["ghost"]

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        pos = event.position().toPoint()
        lane = self._lane(pos)
        time = self.time_of(pos.x())
        if lane == "ruler":
            if event.button() == Qt.RightButton:
                self._bar_menu(event.globalPosition().toPoint(), self.score.bar_of(time))
            elif time < self.score.length:
                self.playhead = self.snapped(time)
                self.seek.emit(self.playhead)
                self.update()
            return
        if lane == "sections":
            if event.button() == Qt.RightButton:
                self._section_menu(event.globalPosition().toPoint(), self.score.bar_of(time))
            return
        if lane == "chords":
            near = self._chord_near(pos)
            if event.button() == Qt.RightButton and near:
                menu = QMenu(self)
                menu.addAction(t("Change chord…"), lambda: self._edit_chord(near[0], near[1]))
                menu.addAction(t("Remove chord"), lambda: self._commit(lambda s: s.delete_chord(near[0])))
                menu.exec(event.globalPosition().toPoint())
            elif event.button() == Qt.LeftButton:
                if near:
                    self._edit_chord(near[0], near[1])
                elif time < self.score.length:
                    onset = self.snapped(time)
                    self._edit_chord(onset, "")
            return
        if lane != "roll":
            return
        note, edge = self._note_under(pos)
        if event.button() == Qt.RightButton:
            if note:
                targets = self.selected if note in self.selected else [note]
                menu = QMenu(self)
                menu.addAction(t("Delete note"), lambda: self._delete(targets))
                menu.exec(event.globalPosition().toPoint())
            return
        if event.modifiers() & Qt.ShiftModifier and not note:
            self._drag = {"kind": "band", "start": pos, "now": pos}
            return
        if note:
            if event.modifiers() & Qt.ControlModifier:
                if note in self.selected:
                    self.selected.remove(note)
                else:
                    self.selected.append(note)
                self.update()
                return
            self.selected = [note]
            if edge:
                self._drag = {"kind": "resize", "note": note, "ghost": Note(note.onset, note.pitch, note.duration)}
            else:
                self._drag = {"kind": "move", "note": note, "offset": time - note.onset,
                              "ghost": Note(note.onset, note.pitch, note.duration)}
        else:
            if time >= self.score.length:
                return
            onset = self.snapped(time)
            self.selected = []
            self._drag = {"kind": "new", "note": None, "ghost": Note(onset, self.pitch_of(pos.y()), self.snap)}
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        drag = self._drag
        if not drag:
            note, edge = self._note_under(pos) if self._lane(pos) == "roll" else (None, False)
            self.setCursor(Qt.SizeHorCursor if edge else Qt.PointingHandCursor if note else Qt.CrossCursor
                           if self._lane(pos) == "roll" else Qt.ArrowCursor)
            return
        time = self.time_of(pos.x())
        ghost = drag.get("ghost")
        if drag["kind"] == "move":
            ghost.onset = max(F(0), min(self.score.length - ghost.duration, self.snapped(time - drag["offset"] + self.snap / 2)))
            ghost.pitch = self.pitch_of(pos.y())
        elif drag["kind"] in ("resize", "new"):
            end = max(ghost.onset + self.snap, self.snapped(time + self.snap / 2))
            ghost.duration = min(end, self.score.length) - ghost.onset
        elif drag["kind"] == "band":
            drag["now"] = pos
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        drag, self._drag = self._drag, None
        if not drag:
            return
        voice = self.voice
        if drag["kind"] == "band":
            rect = QRect(drag["start"], drag["now"]).normalized()
            self.selected = [n for n in self.score.voices[voice] if self.note_rect(n).intersects(QRectF(rect))]
            self.update()
            return
        ghost, original = drag["ghost"], drag.get("note")
        if original and (ghost.onset, ghost.pitch, ghost.duration) == (original.onset, original.pitch, original.duration):
            self.update()
            return
        self.history.checkpoint()
        if original:
            self.score.delete_note(voice, original)
        placed = self.score.add_note(voice, ghost.onset, ghost.pitch, ghost.duration)
        self.selected = [placed]
        self._after_edit()

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._lane(pos) == "sections":
            bar = self.score.bar_of(self.time_of(pos.x()))
            self._rename_section(bar)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected:
            self._delete(self.selected)
        elif event.key() in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            step = (12 if event.modifiers() & Qt.ShiftModifier else 1) * (1 if event.key() == Qt.Key_Up else -1)
            self._shift_selection(step)
        else:
            super().keyPressEvent(event)

    # --- edits -----------------------------------------------------------
    def _after_edit(self) -> None:
        live = {id(n) for n in self.score.voices[self.voice]}
        self.selected = [n for n in self.selected if id(n) in live]
        self._resize()
        self.update()
        self.changed.emit()

    def _commit(self, change) -> bool:
        self.history.checkpoint()
        try:
            change(self.score)
        except ValueError as error:
            self.history.undo()
            QMessageBox.information(self, t("Score"), str(error))
            return False
        self._after_edit()
        return True

    def _delete(self, notes: list[Note]) -> None:
        targets = list(notes)
        self._commit(lambda s: [s.delete_note(self.voice, n) for n in targets])
        self.selected = []

    def _shift_selection(self, step: int) -> None:
        targets = sorted(self.selected, key=lambda n: n.onset)

        def change(score: Score) -> None:
            moved = []
            for note in targets:
                if not 0 <= note.pitch + step <= 127:
                    raise ValueError(t("That would move notes out of range."))
            for note in targets:
                score.delete_note(self.voice, note)
            for note in targets:
                moved.append(score.add_note(self.voice, note.onset, note.pitch + step, note.duration))
            self.selected = moved

        self._commit(change)

    def _edit_chord(self, onset: Fraction, current: str) -> None:
        text, ok = QInputDialog.getText(
            self, t("Chord"), t("Chord at bar {bar}, beat {beat} (for example C, Am7, F/A, G7sus4):",
                                bar=self.score.bar_of(onset) + 1,
                                beat=f"{float(onset - self.score.bar_start(self.score.bar_of(onset))) + 1:g}"),
            text=current)
        if not ok:
            return
        text = text.strip().replace("♭", "b").replace("♯", "#")
        if not text:
            if current:
                self._commit(lambda s: s.delete_chord(onset))
            return
        if not abc_dialect.CHORD.fullmatch(text):
            QMessageBox.information(self, t("Chord"), t("“{text}” is not a chord the engine understands. Use a "
                                                         "root and a type such as C, Cm, C7, Cmaj7, Cm7, Cdim, "
                                                         "Csus4, C6, with an optional bass like C/E.", text=text))
            return
        self._commit(lambda s: s.set_chord(onset, text))

    def _rename_section(self, bar: int) -> None:
        starts = dict(self.score.sections)
        current = starts.get(bar, "")
        items = [t(p) for p in SECTION_PRESETS]
        choice, ok = QInputDialog.getItem(self, t("Song part"), t("Part starting at bar {bar}:", bar=bar + 1),
                                          items, items.index(t(current)) if t(current) in items else 1, True)
        if ok and choice.strip():
            reverse = {t(p): p for p in SECTION_PRESETS}
            name = reverse.get(choice, choice)
            self._commit(lambda s: s.set_section(bar, name))

    def _section_menu(self, where: QPoint, bar: int) -> None:
        menu = QMenu(self)
        start = menu.addMenu(t("Start a part at bar {bar}", bar=bar + 1))
        for preset in SECTION_PRESETS:
            start.addAction(t(preset), lambda p=preset: self._commit(lambda s: s.set_section(bar, p)))
        start.addAction(t("Other…"), lambda: self._rename_section(bar))
        if bar in dict(self.score.sections) and bar != 0:
            menu.addAction(t("Remove this part start"), lambda: self._commit(lambda s: s.set_section(bar, "")))
        menu.exec(where)

    def _bar_menu(self, where: QPoint, bar: int) -> None:
        menu = QMenu(self)
        if bar < self.score.bars:
            menu.addAction(t("Insert a bar before bar {bar}", bar=bar + 1),
                           lambda: self._commit(lambda s: s.add_bars(1, bar)))
            menu.addAction(t("Delete bar {bar}", bar=bar + 1), lambda: self._commit(lambda s: s.delete_bar(bar)))
        menu.addAction(t("Add 4 bars at the end"), lambda: self._commit(lambda s: s.add_bars(4)))
        menu.exec(where)


# ---------------------------------------------------------------------------
class PreviewRender(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, score: Score, target: Path, parent=None):
        super().__init__(parent)
        self.score, self.target = score, target

    def run(self) -> None:
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.target.write_bytes(preview.render(self.score))
            self.done.emit(str(self.target))
        except Exception as error:  # noqa: BLE001 - reported in the window
            self.failed.emit(str(error))


# ---------------------------------------------------------------------------
class ScoreEditor(QDialog):
    """Open with ABC text; emits ``chosen(target, abc)`` for "sing" / "create" / "cover"."""

    chosen = Signal(str, str)

    LABELS = {"sing": N("Sing it now"), "create": N("Make a song with this score"),
              "cover": N("Make a cover with this score")}

    def __init__(self, text: str, *, title: str, actions: tuple[str, ...] = ("create", "cover"),
                 primary: str = "create", pause_player=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1360, 860)
        self.setMinimumSize(1000, 640)
        self.original_text = text
        self.pause_player = pause_player
        self.dirty = False
        self._render: PreviewRender | None = None
        self._preview_hash = ""
        self._preview_file = ""
        try:
            self.history = History(Score.from_abc(text))
            self.original_key = self.history.score.key
            self.visual_error = ""
        except UnsupportedScore as error:
            self.history, self.visual_error = None, t("This score can only be edited as text: {why}.", why=str(error))
        except abc_dialect.AbcError as error:
            self.history, self.visual_error = None, t("This score could not be read: {why}", why=str(error))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        name = QLabel(title)
        name.setStyleSheet("font-size:18px;font-weight:800;")
        top.addWidget(name)
        top.addSpacing(12)
        self.info = QLabel("")
        self.info.setObjectName("MonoAccent")
        top.addWidget(self.info, 1)
        self.advanced = QPushButton(t("ABC text"))
        self.advanced.setCheckable(True)
        self.advanced.setToolTip(t("Show the score as the text the engine reads"))
        self.advanced.toggled.connect(self._toggle_text)
        top.addWidget(self.advanced)
        open_btn = QPushButton(t("Open .abc…"))
        open_btn.clicked.connect(self._open_file)
        top.addWidget(open_btn)
        save_btn = QPushButton(t("Save as .abc…"))
        save_btn.clicked.connect(self._save_file)
        top.addWidget(save_btn)
        layout.addLayout(top)

        # --- toolbar -----------------------------------------------------
        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(heading(t("Key")))
        self.key_combo = QComboBox()
        self.key_combo.setMinimumWidth(130)
        self.key_combo.activated.connect(self._key_chosen)
        bar.addWidget(self.key_combo)
        down = QPushButton("−1")
        down.setFixedWidth(40)
        down.setToolTip(t("Down a semitone"))
        down.clicked.connect(lambda: self._transpose(-1))
        bar.addWidget(down)
        up = QPushButton("+1")
        up.setFixedWidth(40)
        up.setToolTip(t("Up a semitone"))
        up.clicked.connect(lambda: self._transpose(1))
        bar.addWidget(up)
        bar.addSpacing(10)
        bar.addWidget(heading(t("Tempo")))
        self.tempo = QSpinBox()
        self.tempo.setRange(30, 240)
        self.tempo.setSuffix(" BPM")
        self.tempo.editingFinished.connect(self._tempo_changed)
        bar.addWidget(self.tempo)
        bar.addSpacing(10)
        bar.addWidget(heading(t("Edit")))
        self.voice_buttons = {}
        for voice in ("Vocal", "Ins"):
            button = QPushButton(t(VOICE_LABELS[voice]))
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.clicked.connect(lambda _, v=voice: self._set_voice(v))
            bar.addWidget(button)
            self.voice_buttons[voice] = button
        bar.addSpacing(10)
        bar.addWidget(heading(t("Snap")))
        self.snap_buttons = []
        for value, label in SNAPS:
            button = QPushButton(t(label))
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.clicked.connect(lambda _, v=value: self._set_snap(v))
            bar.addWidget(button)
            self.snap_buttons.append((value, button))
        bar.addSpacing(10)
        bar.addWidget(heading(t("Zoom")))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(12, 120)
        self.zoom.setValue(36)
        self.zoom.setFixedWidth(110)
        bar.addWidget(self.zoom)
        bar.addStretch(1)
        self.undo_btn = QPushButton(t("Undo"))
        self.undo_btn.clicked.connect(self._undo)
        bar.addWidget(self.undo_btn)
        self.redo_btn = QPushButton(t("Redo"))
        self.redo_btn.clicked.connect(self._redo)
        bar.addWidget(self.redo_btn)
        self.play_btn = QPushButton(t("▶  Play"))
        self.play_btn.setMinimumWidth(110)
        self.play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self.play_btn)
        layout.addWidget(self.toolbar)

        # --- body --------------------------------------------------------
        self.stack = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet(f"QScrollArea{{border:1px solid {theme.LINE};border-radius:10px;}}")
        if self.history:
            self.canvas = RollCanvas(self.history)
            self.canvas.changed.connect(self._changed)
            self.canvas.seek.connect(self._seek)
            self.zoom.valueChanged.connect(lambda v: self.canvas.set_zoom(float(v)))
            self.scroll.setWidget(self.canvas)
            self.scroll.horizontalScrollBar().valueChanged.connect(lambda _: self.canvas.update())
            self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self.canvas.update())
        else:
            self.canvas = None
        self.stack.addWidget(self.scroll)
        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        self.text = QPlainTextEdit()
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setStyleSheet(f"font-family:'{theme.MONO_FONT}';font-size:12.5px;")
        text_layout.addWidget(self.text, 1)
        apply_row = QHBoxLayout()
        self.text_note = QLabel(t("Edit the text, then press Apply. The score is checked before it is used."))
        self.text_note.setObjectName("Hint")
        apply_row.addWidget(self.text_note, 1)
        apply_btn = QPushButton(t("Apply"))
        apply_btn.clicked.connect(self._apply_text)
        apply_row.addWidget(apply_btn)
        text_layout.addLayout(apply_row)
        self.stack.addWidget(text_page)
        layout.addWidget(self.stack, 1)

        self.hint = QLabel(t("Click to add a note · drag to move · drag its right edge to change length · "
                             "Delete removes · ↑/↓ change pitch · Shift-drag selects several · "
                             "click the chord lane to add a chord · right-click bars and parts for more"))
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.status = QLabel("")
        self.status.setObjectName("Warning")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        footer = QHBoxLayout()
        close = QPushButton(t("Close"))
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        footer.addStretch(1)
        for action in actions:
            button = QPushButton(t(self.LABELS[action]))
            if action == primary:
                button.setObjectName("Primary")
                button.setMinimumWidth(260)
            else:
                button.setMinimumHeight(40)
            button.clicked.connect(lambda _, a=action: self._use(a))
            footer.addWidget(button)
        layout.addLayout(footer)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_play_state)

        QShortcut(QKeySequence.Undo, self, activated=self._undo)
        QShortcut(QKeySequence.Redo, self, activated=self._redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._toggle_play)

        if self.history:
            self._set_voice("Vocal")
            self._set_snap(F(1, 2))
            self._refresh()
        else:
            self.text.setPlainText(text)
            self.advanced.setChecked(True)
            self.advanced.setEnabled(False)
            self.toolbar.setEnabled(False)
            self.hint.setVisible(False)
            self.status.setText(self.visual_error)

    # --- state -----------------------------------------------------------
    @property
    def score(self) -> Score | None:
        return self.history.score if self.history else None

    def _refresh(self) -> None:
        score = self.score
        if score is None:
            return
        self.info.setText(describe(score))
        pool = theory.MINOR_KEYS if theory.is_minor(score.key) else theory.MAJOR_KEYS
        self.key_combo.blockSignals(True)
        self.key_combo.clear()
        for key in pool:
            self.key_combo.addItem(key_name(key), key)
        self.key_combo.setCurrentIndex(max(0, self.key_combo.findData(score.key)))
        self.key_combo.blockSignals(False)
        self.tempo.blockSignals(True)
        self.tempo.setValue(int(score.bpm))
        self.tempo.blockSignals(False)
        self.undo_btn.setEnabled(self.history.can_undo)
        self.redo_btn.setEnabled(self.history.can_redo)
        notes = []
        span = score.pitch_range("Vocal")
        if span:
            notes.append(t("Vocal range {low} – {high}", low=theory.note_name(span[0], score.key),
                           high=theory.note_name(span[1], score.key)))
        if score.key_changes:
            notes.append(t("The key changes {n} times - the Key box moves the whole song", n=len(score.key_changes)))
        shift = theory.nearest_interval(self.original_key, score.key)
        if abs(shift) > 5:
            notes.append("⚠  " + t("Moved {n} semitones from the original key - the singer may strain.",
                                   n=f"{shift:+d}"))
        if not score.voices["Vocal"]:
            notes.append("⚠  " + t("The vocal line has no notes."))
        self.status.setObjectName("Warning" if any(n.startswith("⚠") for n in notes) else "Hint")
        self.status.setStyleSheet("")
        self.status.setText("   ·   ".join(notes))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _changed(self) -> None:
        self.dirty = True
        self._stop()
        self._refresh()

    def _set_voice(self, voice: str) -> None:
        for name, button in self.voice_buttons.items():
            button.setChecked(name == voice)
        if self.canvas:
            self.canvas.voice = voice
            self.canvas.selected = []
            self.canvas.update()

    def _set_snap(self, value: Fraction) -> None:
        for snap, button in self.snap_buttons:
            button.setChecked(snap == value)
        if self.canvas:
            self.canvas.snap = value
            self.canvas.update()

    def _undo(self) -> None:
        if self.history and self.history.undo():
            self._after_history()

    def _redo(self) -> None:
        if self.history and self.history.redo():
            self._after_history()

    def _after_history(self) -> None:
        self.canvas.history = self.history
        self.canvas.selected = []
        self.canvas._fit_range()
        self.canvas.update()
        self._changed()

    def _transpose(self, semitones: int) -> None:
        if not self.canvas or not semitones:
            return
        if self.canvas._commit(lambda s: s.transpose(semitones)):
            self.canvas._fit_range()
            self._refresh()

    def _key_chosen(self) -> None:
        target = self.key_combo.currentData()
        if target and self.score and target != self.score.key:
            self._transpose(theory.nearest_interval(self.score.key, target))

    def _tempo_changed(self) -> None:
        if self.canvas and self.score and self.tempo.value() != self.score.bpm:
            value = self.tempo.value()
            self.canvas._commit(lambda s: setattr(s, "bpm", value))

    # --- text mode -------------------------------------------------------
    def current_text(self, strip_chords: bool = False) -> str:
        """The score as validated ABC. Raises WriteError / AbcError with a readable message."""
        if self.score is None:
            text = self.text.toPlainText()
            abc_dialect.parse(text)
            if strip_chords:
                return abc_dialect.strip_chords(text)
            return text
        return to_abc(self.score, strip_chords=strip_chords)

    def _toggle_text(self, on: bool) -> None:
        if on:
            if self.score is not None:
                try:
                    self.text.setPlainText(self.current_text())
                except WriteError as error:
                    self.status.setText(str(error))
            self.stack.setCurrentIndex(1)
            self.toolbar.setEnabled(False)
        else:
            if self.score is not None and not self._apply_text(quiet=True):
                self.advanced.blockSignals(True)
                self.advanced.setChecked(True)
                self.advanced.blockSignals(False)
                return
            self.stack.setCurrentIndex(0)
            self.toolbar.setEnabled(True)

    def _apply_text(self, quiet: bool = False) -> bool:
        text = self.text.toPlainText()
        if self.score is None:
            try:
                abc_dialect.parse(text)
            except abc_dialect.AbcError as error:
                self.status.setText(t("The text is not a valid score: {why}", why=str(error)))
                return False
            self.dirty = True
            self.status.setText(t("Score checked."))
            return True
        try:
            if text.strip() == to_abc(self.score).strip():
                return True
            new = Score.from_abc(text)
        except (abc_dialect.AbcError, UnsupportedScore, WriteError, ValueError) as error:
            self.status.setText(t("The text is not a valid score: {why}", why=str(error)))
            return False
        self.history.checkpoint()
        self.history.score = new
        self.canvas.history = self.history
        self.canvas._fit_range()
        self._changed()
        if not quiet:
            self.status.setText(t("Score checked."))
        return True

    # --- files -----------------------------------------------------------
    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Open a score"), str(Path.home()), t("ABC score") + " (*.abc)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            new = Score.from_abc(text)
        except (OSError, UnicodeDecodeError, abc_dialect.AbcError, UnsupportedScore) as error:
            QMessageBox.warning(self, t("Open a score"), t("This file is not a score Gokuk can use: {why}", why=error))
            return
        if self.history is None:
            QMessageBox.information(self, t("Open a score"), t("Close this window and open the file from the tab."))
            return
        self.history.checkpoint()
        self.history.score = new
        self.original_key = new.key
        self.canvas._fit_range()
        self._after_history()

    def _save_file(self) -> None:
        try:
            text = self.current_text()
        except (WriteError, abc_dialect.AbcError) as error:
            QMessageBox.warning(self, t("Save score"), str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, t("Save score"), str(Path.home() / "score.abc"),
                                              t("ABC score") + " (*.abc)")
        if not path:
            return
        if not path.lower().endswith(".abc"):
            path += ".abc"
        Path(path).write_text(text, encoding="utf-8")
        self.dirty = False
        self.status.setText(t("Saved {path}", path=path))

    # --- preview ---------------------------------------------------------
    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self._stop()
            return
        if self.score is None:
            return
        digest = hashlib.sha1(repr((self.score.bpm, self.score.chords, self.score.voices)).encode()).hexdigest()[:16]
        if digest == self._preview_hash and self._preview_file:
            self._play_from_playhead()
            return
        if self._render and self._render.isRunning():
            return
        self.play_btn.setText(t("Preparing…"))
        self.play_btn.setEnabled(False)
        target = paths.cache_dir() / "tmp" / f"score-preview-{digest}.wav"
        self._render = PreviewRender(self.score.copy(), target, self)
        self._render.done.connect(lambda file, d=digest: self._rendered(file, d))
        self._render.failed.connect(self._render_failed)
        self._render.start()

    def _rendered(self, file: str, digest: str) -> None:
        self._preview_hash, self._preview_file = digest, file
        self.play_btn.setEnabled(True)
        self.player.setSource(QUrl.fromLocalFile(file))
        self._play_from_playhead()

    def _render_failed(self, message: str) -> None:
        self.play_btn.setEnabled(True)
        self.play_btn.setText(t("▶  Play"))
        self.status.setText(t("The preview could not be made: {why}", why=message))

    def _play_from_playhead(self) -> None:
        if self.pause_player:
            self.pause_player()
        seconds = float(self.canvas.playhead) * 60 / self.score.bpm if self.canvas else 0
        self.player.setPosition(int(seconds * 1000))
        self.player.play()

    def _stop(self) -> None:
        if self.player.playbackState() != QMediaPlayer.StoppedState:
            self.player.pause()

    def _seek(self, time: Fraction) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState and self.score:
            self.player.setPosition(int(float(time) * 60 / self.score.bpm * 1000))

    def _on_position(self, ms: int) -> None:
        if not self.canvas or not self.score or self.player.playbackState() != QMediaPlayer.PlayingState:
            return
        time = F(ms / 1000 * self.score.bpm / 60).limit_denominator(64)
        if time >= self.score.length:
            self._stop()
            return
        self.canvas.playhead = time
        self.canvas.ensure_visible(time)
        self.canvas.update()

    def _on_play_state(self, state) -> None:
        self.play_btn.setText(t("❚❚  Pause") if state == QMediaPlayer.PlayingState else t("▶  Play"))

    # --- leaving ---------------------------------------------------------
    def _use(self, action: str) -> None:
        if self.advanced.isChecked() and not self._apply_text(quiet=True):
            return
        try:
            text = self.current_text()          # chords kept; Cover removes them when it sings
        except (WriteError, abc_dialect.AbcError) as error:
            QMessageBox.warning(self, t("Score"), t("The score cannot be used yet: {why}", why=str(error)))
            return
        self.dirty = False
        self._stop()
        self.chosen.emit(action, text)
        self.accept()

    def reject(self) -> None:
        if self.dirty and QMessageBox.question(
                self, t("Discard changes?"),
                t("You changed this score. Close without using or saving it?")) != QMessageBox.Yes:
            return
        self._stop()
        super().reject()
