"""The Gokuk Setup window.

One screen, read top to bottom, like EasyAI Setup: this computer, what to
include, how big that is, then Install. The download size is the most
prominent number on the page - agreeing to 17 GB by accident would be a
miserable surprise.

Setup installs into its own folder. Gokuk is portable, so the runtimes and
models have to sit beside Gokuk.exe; to put Gokuk somewhere else, move the
whole folder (before or after installing - either works).

While installing, the options give way to progress: an overall bar with the
step, amount, speed and time left; a bar for the current file; a checklist of
steps; and the raw log behind a Details switch for anyone who wants it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout,
    QWidget,
)

from app import gpu as gpu_info
from app import i18n, paths
from app.i18n import t
from app.install_state import InstallState
from app.ui import theme
from app.ui.widgets import Switch, heading, open_folder, panel
from setup.catalog import Catalog, Component, human_bytes
from setup.download import format_eta, free_space
from setup.steps import Installer, Overall, Report

#: Folders that make a poor home for 17 GB of models that get rewritten.
RISKY_PLACES = ("onedrive", "dropbox", "google drive", "icloud", "program files", "windows")


class InstallWorker(QThread):
    """Runs the install off the interface thread."""

    step = Signal(str)
    progress = Signal(object)
    log = Signal(str)
    finished_report = Signal(object)

    def __init__(self, catalog: Catalog, root: Path, components: list[str], token: str,
                 clean: bool, parent=None):
        super().__init__(parent)
        self._args = (catalog, root, components, token, clean)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        catalog, root, components, token, clean = self._args
        installer = Installer(catalog, root, components, on_step=self.step.emit,
                              on_progress=self.progress.emit, on_log=self.log.emit,
                              should_stop=lambda: self._cancelled, hf_token=token,
                              clean_wheels=clean)
        self.finished_report.emit(installer.run())


class ComponentCard(QFrame):
    """One switchable component, with what it costs and whether it is there."""

    changed = Signal()

    def __init__(self, component: Component, catalog: Catalog, installed: bool, parent=None):
        super().__init__(parent)
        self.component = component
        self.setObjectName("Panel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.tick = Switch(t(component.label))
        self.tick.setChecked(component.default or component.required or installed)
        self.tick.setEnabled(not component.required)
        self.tick.toggled.connect(lambda _: self.changed.emit())
        text.addWidget(self.tick)
        detail = QLabel(t(component.detail) + ("  ·  " + t("required") if component.required else ""))
        detail.setObjectName("Hint")
        detail.setWordWrap(True)
        text.addWidget(detail)
        layout.addLayout(text, 1)

        if installed:
            state = QLabel(t("Installed ✓"))
            state.setObjectName("Good")
            layout.addWidget(state)
        size = QLabel(human_bytes(catalog.component_bytes(component.id)))
        size.setObjectName("MonoAccent")
        layout.addWidget(size)

    @property
    def checked(self) -> bool:
        return self.tick.isChecked()


class StepList(QWidget):
    """✓ done, ● now, ○ to come."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._labels: list[QLabel] = []

    def set_steps(self, names: list[str]) -> None:
        for label in self._labels:
            label.deleteLater()
        self._labels = []
        for name in names:
            label = QLabel()
            label.setProperty("name", name)
            self._layout.addWidget(label)
            self._labels.append(label)
        self.set_current(0)

    def count(self) -> int:
        return len(self._labels)

    def set_current(self, index: int, failed: bool = False) -> None:
        for i, label in enumerate(self._labels):
            name = label.property("name")
            if i < index:
                label.setText(f"✓  {name}")
                label.setStyleSheet(f"color:{theme.OK};font-size:12.5px;")
            elif i == index:
                mark = "✕" if failed else "●"
                colour = theme.BAD if failed else theme.ACCENT
                label.setText(f"{mark}  {name}")
                label.setStyleSheet(f"color:{colour};font-size:12.5px;font-weight:700;")
            else:
                label.setText(f"○  {name}")
                label.setStyleSheet(f"color:{theme.DIM};font-size:12.5px;")


class SetupWindow(QMainWindow):
    def __init__(self, catalog: Catalog, root: Path | None = None):
        super().__init__()
        self.catalog = catalog
        self.root = Path(root or paths.root())
        self.worker: InstallWorker | None = None
        self._current_step = 0
        self.gpu = gpu_info.detect()
        self.setWindowTitle(t("Gokuk Setup"))
        self.resize(820, 860)
        self.setMinimumSize(700, 640)
        self._build()

    # --- layout ----------------------------------------------------------
    def _build(self) -> None:
        state = InstallState.load()
        self.stack = QStackedWidget()
        self.stack.addWidget(self._options_page(state))
        self.stack.addWidget(self._progress_page())
        self.setCentralWidget(self.stack)
        self._recalculate()

    def _options_page(self, state: InstallState) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 20, 26, 22)
        layout.setSpacing(12)

        # Language first: someone who cannot read the window must still find it.
        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel(t("Gokuk Setup"))
        title.setStyleSheet("font-size:22px;font-weight:800;")
        title_col.addWidget(title)
        blurb = QLabel(t("Downloads the music engine and its models. Run this once before "
                         "opening Gokuk - everything stays inside this folder."))
        blurb.setObjectName("Hint")
        blurb.setWordWrap(True)
        title_col.addWidget(blurb)
        top.addLayout(title_col, 1)
        self.language = QComboBox()
        for code, name in i18n.available().items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.current())))
        self.language.currentIndexChanged.connect(self._change_language)
        top.addWidget(self.language, 0, Qt.AlignTop)
        layout.addLayout(top)

        # --- this computer ---------------------------------------------------
        layout.addSpacing(4)
        layout.addWidget(heading(t("This computer")))
        box, inner = panel(14, 6)
        level, sentence = gpu_info.verdict(self.gpu)
        icon = {"ok": "✅", "warn": "⚠", "bad": "⛔"}[level]
        self.gpu_label = QLabel(f"{icon}  {sentence}")
        self.gpu_label.setObjectName({"ok": "Good", "warn": "Warning", "bad": "Warning"}[level])
        if level == "bad":
            self.gpu_label.setStyleSheet(f"color:{theme.BAD};")
        self.gpu_label.setWordWrap(True)
        inner.addWidget(self.gpu_label)

        folder_row = QHBoxLayout()
        folder = QLabel(str(self.root))
        folder.setObjectName("Mono")
        folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder_row.addWidget(folder, 1)
        show = QPushButton(t("Open folder"))
        show.clicked.connect(lambda: open_folder(self.root))
        folder_row.addWidget(show)
        inner.addLayout(folder_row)
        self.space = QLabel("")
        self.space.setObjectName("Hint")
        self.space.setWordWrap(True)
        inner.addWidget(self.space)
        layout.addWidget(box)

        # --- what ------------------------------------------------------------
        layout.addSpacing(4)
        layout.addWidget(heading(t("What to include")))
        self.cards: list[ComponentCard] = []
        for component in self.catalog.components:
            card = ComponentCard(component, self.catalog, component.id in state.components)
            card.changed.connect(self._recalculate)
            self.cards.append(card)
            layout.addWidget(card)

        # --- total -----------------------------------------------------------
        layout.addSpacing(6)
        self.total = QLabel("")
        self.total.setStyleSheet(f"font-size:26px;font-weight:800;color:{theme.ACCENT};")
        layout.addWidget(self.total)
        self.total_hint = QLabel("")
        self.total_hint.setObjectName("Hint")
        self.total_hint.setWordWrap(True)
        layout.addWidget(self.total_hint)
        self.warnings = QLabel("")
        self.warnings.setObjectName("Warning")
        self.warnings.setWordWrap(True)
        layout.addWidget(self.warnings)

        # --- licence and options ---------------------------------------------
        self.licence = Switch(t("I understand the models are licensed CC BY-NC 4.0 - "
                                "non-commercial use only."))
        self.licence.setStyleSheet(self.licence.styleSheet().replace("font-weight:600", "font-weight:400"))
        self.licence.toggled.connect(self._recalculate)
        layout.addWidget(self.licence)
        self.clean = Switch(t("Delete installer files afterwards (saves about 6 GB)"))
        self.clean.setStyleSheet(self.licence.styleSheet())
        self.clean.setChecked(True)
        layout.addWidget(self.clean)

        token_row = QHBoxLayout()
        token_label = QLabel(t("HuggingFace key"))
        token_label.setObjectName("Hint")
        token_row.addWidget(token_label)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText(t("optional - only if downloads are refused (hf_…)"))
        token_row.addWidget(self.token, 1)
        layout.addLayout(token_row)

        layout.addStretch(1)
        self.install_btn = QPushButton(t("Install"))
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._start)
        layout.addWidget(self.install_btn)

        self.launch_now = QPushButton(t("Open Gokuk"))
        self.launch_now.clicked.connect(self._launch)
        self.launch_now.setVisible(state.ready)
        layout.addWidget(self.launch_now)

        scroll.setWidget(page)
        return scroll

    def _progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)

        self.p_title = QLabel(t("Installing Gokuk"))
        self.p_title.setStyleSheet("font-size:22px;font-weight:800;")
        layout.addWidget(self.p_title)
        self.p_sub = QLabel(t("You can leave this running. If it stops for any reason, run Setup "
                              "again - it carries on from where it left off."))
        self.p_sub.setObjectName("Hint")
        self.p_sub.setWordWrap(True)
        layout.addWidget(self.p_sub)

        box, inner = panel(18, 8)
        self.step_label = QLabel("")
        self.step_label.setStyleSheet("font-size:15px;font-weight:700;")
        inner.addWidget(self.step_label)
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setFixedHeight(10)
        inner.addWidget(self.overall)
        numbers = QHBoxLayout()
        self.amount = QLabel("")
        self.amount.setObjectName("MonoAccent")
        numbers.addWidget(self.amount)
        numbers.addStretch(1)
        self.speed = QLabel("")
        self.speed.setObjectName("Mono")
        numbers.addWidget(self.speed)
        inner.addLayout(numbers)

        inner.addSpacing(6)
        self.file_label = QLabel("")
        self.file_label.setObjectName("Mono")
        inner.addWidget(self.file_label)
        self.file_bar = QProgressBar()
        self.file_bar.setRange(0, 1000)
        self.file_bar.setFixedHeight(5)
        inner.addWidget(self.file_bar)
        layout.addWidget(box)

        layout.addWidget(heading(t("Steps")))
        self.steps = StepList()
        layout.addWidget(self.steps)

        details_row = QHBoxLayout()
        self.details = Switch(t("Details"))
        self.details.setStyleSheet(self.details.styleSheet().replace("font-size:14px", "font-size:12px"))
        details_row.addWidget(self.details)
        details_row.addStretch(1)
        layout.addLayout(details_row)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(f"font-family:'{theme.MONO_FONT}';font-size:11px;color:{theme.MUTED};")
        self.log.setVisible(False)
        self.details.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log, 1)
        layout.addStretch(0)

        buttons = QHBoxLayout()
        self.cancel_btn = QPushButton(t("Cancel"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)
        self.folder_btn = QPushButton(t("Show folder"))
        self.folder_btn.clicked.connect(lambda: open_folder(self.root))
        self.folder_btn.setVisible(False)
        buttons.addWidget(self.folder_btn)
        self.again_btn = QPushButton(t("Back"))
        self.again_btn.clicked.connect(self._back)
        self.again_btn.setVisible(False)
        buttons.addWidget(self.again_btn)
        self.launch_btn = QPushButton(t("Launch Gokuk"))
        self.launch_btn.setObjectName("Primary")
        self.launch_btn.setMinimumWidth(220)
        self.launch_btn.clicked.connect(self._launch)
        self.launch_btn.setVisible(False)
        buttons.addWidget(self.launch_btn)
        layout.addLayout(buttons)
        return page

    # --- choices ---------------------------------------------------------
    def _selected(self) -> list[str]:
        return [c.component.id for c in self.cards if c.checked]

    def _todo_bytes(self) -> int:
        installer = Installer(self.catalog, self.root, self._selected())
        return sum(i.size for i in installer.plan())

    def _recalculate(self) -> None:
        todo = self._todo_bytes()
        free = free_space(self.root)
        if todo:
            self.total.setText(t("{size} to download", size=human_bytes(todo)))
            self.total_hint.setText(t("Largest files first - the main model alone is 7 GB. "
                                      "On a 100 Mbit connection allow about 30 minutes."))
        else:
            self.total.setText(t("Everything is installed"))
            self.total_hint.setText(t("Press Install to check the files again, or open Gokuk."))
        self.space.setText(t("{free} free on this drive", free=human_bytes(free)))

        notes = []
        if free and todo and free < todo * 1.2:
            notes.append(t("⚠  Not enough room - this needs about {size}.", size=human_bytes(todo * 1.2)))
        low = str(self.root).lower()
        if any(place in low for place in RISKY_PLACES):
            notes.append(t("⚠  This folder is synced or protected by Windows. Move the Gokuk folder "
                           "somewhere like D:\\Gokuk first."))
        if len(str(self.root)) > 120:
            notes.append(t("⚠  The folder path is very long - some files may fail. A shorter path is safer."))
        if any(ord(c) > 127 for c in str(self.root)):
            notes.append(t("⚠  The folder path has non-English characters - if installing fails, "
                           "move the folder to a plain path like D:\\Gokuk."))
        self.warnings.setText("\n".join(notes))
        level, _ = gpu_info.verdict(self.gpu)
        self.install_btn.setEnabled(self.licence.isChecked())
        self.install_btn.setToolTip("" if self.licence.isChecked()
                                    else t("Tick the licence box first"))
        if level == "bad":
            self.install_btn.setText(t("Install anyway"))

    def _change_language(self) -> None:
        code = self.language.currentData()
        if not code or code == i18n.current():
            return
        keep = (self._selected(), self.licence.isChecked(), self.clean.isChecked(), self.token.text())
        i18n.load(code)
        i18n.remember(code)
        self.setWindowTitle(t("Gokuk Setup"))
        self._build()
        for card in self.cards:
            if not card.component.required:
                card.tick.setChecked(card.component.id in keep[0])
        self.licence.setChecked(keep[1])
        self.clean.setChecked(keep[2])
        self.token.setText(keep[3])

    # --- running ---------------------------------------------------------
    def _start(self) -> None:
        components = self._selected()
        installer = Installer(self.catalog, self.root, components)
        todo = installer.plan()
        names = [t("Checking free space")] + [installer._describe(i) for i in todo] + \
                [t("Checking the installation")]
        self.steps.set_steps(names)
        self.log.clear()
        self.step_label.setText(t("Starting…"))
        self.overall.setValue(0)
        self.file_bar.setValue(0)
        self.amount.setText("")
        self.speed.setText("")
        self.file_label.setText("")
        self.p_title.setText(t("Installing Gokuk"))
        self._set_running(True)
        self.stack.setCurrentIndex(1)

        self.worker = InstallWorker(self.catalog, self.root, components, self.token.text().strip(),
                                    self.clean.isChecked())
        self.worker.step.connect(self._on_step)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.log.append)
        self.worker.finished_report.connect(self._on_done)
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        self.launch_btn.setVisible(False)
        self.again_btn.setVisible(not running)
        self.folder_btn.setVisible(not running)

    def _cancel(self) -> None:
        if self.worker:
            self.cancel_btn.setEnabled(False)
            self.step_label.setText(t("Stopping…"))
            self.worker.cancel()

    def _on_step(self, message: str) -> None:
        self.log.append(f"— {message}")

    def _on_progress(self, p: Overall) -> None:
        self._current_step = p.step - 1
        self.steps.set_current(self._current_step)
        self.step_label.setText(t("Step {n} of {total} · {label}", n=p.step, total=p.steps, label=p.label))
        self.overall.setValue(int(p.fraction * 1000))
        if p.total:
            self.amount.setText(t("{done} of {total}", done=human_bytes(p.done), total=human_bytes(p.total)))
        if p.speed and p.file and p.file.done < p.file.total:
            self.speed.setText(f"{human_bytes(p.speed)}/s   {format_eta(p.eta_seconds)}")
        if p.file:
            name = Path(p.file.name).name
            self.file_label.setText(f"{name}   {human_bytes(p.file.done)} / {human_bytes(p.file.total)}")
            self.file_bar.setValue(int(p.file.fraction * 1000))
        else:
            self.file_label.setText("")
            self.file_bar.setValue(0)

    def _on_done(self, report: Report) -> None:
        self._set_running(False)
        self.worker = None
        self.speed.setText("")
        if report.ok:
            self.steps.set_current(self.steps.count())
            self.overall.setValue(1000)
            self.p_title.setText(t("Gokuk is ready"))
            self.step_label.setText(t("Everything is installed."))
            self.p_sub.setText(t("Installed {n} items, {k} were already there.",
                                 n=len(report.installed), k=len(report.skipped)))
            self.launch_btn.setVisible(True)
            self.again_btn.setVisible(False)
        elif report.cancelled:
            self.p_title.setText(t("Stopped"))
            self.step_label.setText(t("Nothing is lost - run Setup again to carry on."))
        else:
            self.steps.set_current(self._current_step, failed=True)
            self.p_title.setText(t("Setup could not finish"))
            self.step_label.setText(t("Press Back and Install to try again - finished files are kept."))
            self.details.setChecked(True)
            QMessageBox.warning(self, t("Setup could not finish"), "\n\n".join(report.errors[:3]))

    def _back(self) -> None:
        self._build()

    def _launch(self) -> None:
        target = paths.app_exe()
        try:
            if target.suffix == ".py":
                subprocess.Popen([sys.executable, str(target)], cwd=str(self.root))
            else:
                subprocess.Popen([str(target)], cwd=str(self.root))
        except OSError as exc:
            QMessageBox.warning(self, t("Gokuk Setup"), t("Could not start Gokuk: {e}", e=exc))
            return
        self.close()

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, t("Stop installing?"),
                                    t("Setup is still downloading. Stop now? Everything so far is kept.")) \
                    != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(15000)
        event.accept()
