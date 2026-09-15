"""Settings: language, how hard to push the graphics card, and repairs."""
from __future__ import annotations

import shutil
import subprocess
import sys

from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from app import gpu, i18n, paths
from app.config import Config
from app.i18n import N, t
from app.ui.widgets import heading, open_folder, panel

PERFORMANCE = [
    ("auto", N("Automatic (recommended)")),
    ("fast", N("Fastest - keep everything on the card (24 GB cards)")),
    ("balanced", N("Balanced - park part of the model in system memory")),
    ("low", N("Lowest memory - slower, for long songs or 12 GB cards")),
]
MODE_NAMES = {"fast": N("Fastest"), "balanced": N("Balanced"), "low": N("Lowest memory")}
BACKENDS = [("torch", N("Fast (CUDA graphs)")), ("torch-eager", N("Safe (slower, most compatible)"))]
DECODERS = [("standard", N("Standard - best for listening")), ("legacy", N("Benchmark (paper evaluation)"))]


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.language_changed = False
        self.setWindowTitle(t("Settings"))
        self.resize(560, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        layout.addWidget(heading(t("Language")))
        self.language = QComboBox()
        for code, name in i18n.available().items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.current())))
        layout.addWidget(self.language)

        layout.addWidget(heading(t("Graphics card")))
        card = gpu.detect()
        level, sentence = gpu.verdict(card)
        info = QLabel(sentence)
        info.setObjectName("Good" if level == "ok" else "Warning")
        info.setWordWrap(True)
        layout.addWidget(info)
        from app.jobs.requests import LEGACY_PERFORMANCE

        current = cfg.get("performance")
        self.performance = self._combo(PERFORMANCE, LEGACY_PERFORMANCE.get(current, current))
        auto = gpu.auto_memory(card)
        self.performance.setItemText(0, t("Automatic (recommended)") + f"  ·  {t(MODE_NAMES[auto])}")
        note = QLabel(t("If a song runs out of memory, Gokuk retries it once in Lowest memory mode."))
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(self.performance)
        layout.addWidget(note)
        layout.addWidget(QLabel(t("Speed mode")))
        self.backend = self._combo(BACKENDS, cfg.get("backend"))
        layout.addWidget(self.backend)
        layout.addWidget(QLabel(t("Audio decoder")))
        self.decoder = self._combo(DECODERS, cfg.get("decoder"))
        if not (paths.models_dir() / "YuE2-Vae-legacy" / "model.safetensors").is_file():
            self.decoder.model().item(1).setEnabled(False)
        layout.addWidget(self.decoder)

        layout.addSpacing(6)
        layout.addWidget(heading(t("Maintenance")))
        box, inner = panel(12, 8)
        row = QHBoxLayout()
        repair = QPushButton(t("Repair / add components…"))
        repair.setToolTip(t("Opens Gokuk Setup"))
        repair.clicked.connect(self._setup)
        row.addWidget(repair)
        logs = QPushButton(t("Open log folder"))
        logs.clicked.connect(lambda: open_folder(paths.logs_dir()))
        row.addWidget(logs)
        clean = QPushButton(t("Clear temporary files"))
        clean.clicked.connect(self._clean)
        row.addWidget(clean)
        inner.addLayout(row)
        where = QLabel(str(paths.root()))
        where.setObjectName("Mono")
        inner.addWidget(where)
        layout.addWidget(box)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(t("Cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(t("Save"))
        save.setObjectName("Primary")
        save.setMinimumWidth(140)
        save.setFixedHeight(40)
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _combo(self, options, current) -> QComboBox:
        combo = QComboBox()
        for value, label in options:
            combo.addItem(t(label), value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def _save(self) -> None:
        self.cfg.set("performance", self.performance.currentData())
        self.cfg.set("backend", self.backend.currentData())
        self.cfg.set("decoder", self.decoder.currentData())
        self.cfg.save()
        code = self.language.currentData()
        if code != i18n.current():
            i18n.load(code)
            i18n.remember(code)
            self.language_changed = True
        self.accept()

    def _setup(self) -> None:
        launch_setup()

    def _clean(self) -> None:
        for name in ("tmp", "jobs", "scores"):
            shutil.rmtree(paths.cache_dir() / name, ignore_errors=True)
        QMessageBox.information(self, t("Settings"), t("Temporary files cleared."))


def launch_setup() -> bool:
    target = paths.setup_exe()
    try:
        if target.suffix == ".py":
            subprocess.Popen([sys.executable, str(target)], cwd=str(paths.root()))
        else:
            subprocess.Popen([str(target)], cwd=str(paths.root()))
        return True
    except OSError:
        return False
