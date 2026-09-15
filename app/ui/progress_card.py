"""Shows a running song: what is happening in words a musician uses, how far
along it is overall, and a way to stop.

The engine reports stages with counts it knows (32 mixing steps, N decode
chunks) and counts it doesn't (tokens until the song ends). The overall bar
blends both: finished stages by their measured share of a typical run, the
current stage by its count when there is a total, or by a curve that eases
towards the end when there isn't - so the bar keeps moving but never claims
to be done early.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from app.i18n import t
from app.jobs import requests as rq
from app.ui import theme


class ProgressCard(QFrame):
    cancel_clicked = Signal()

    def __init__(self, stages: list[tuple[str, str]] | None = None,
                 weights: dict[str, float] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.stages = stages or rq.STAGES
        self.weights = weights or rq.STAGE_WEIGHT
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.title = QLabel("")
        self.title.setStyleSheet("font-size:15px;font-weight:700;")
        top.addWidget(self.title, 1)
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("Mono")
        top.addWidget(self.elapsed)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)

        bottom = QHBoxLayout()
        self.detail = QLabel("")
        self.detail.setObjectName("Mono")
        bottom.addWidget(self.detail, 1)
        self.cancel = QPushButton(t("Stop"))
        self.cancel.setObjectName("Danger")
        self.cancel.setFixedHeight(28)
        self.cancel.clicked.connect(self._cancel)
        bottom.addWidget(self.cancel)
        layout.addLayout(bottom)

        self.steps = QLabel("")
        self.steps.setWordWrap(True)
        self.steps.setTextFormat(Qt.RichText)
        layout.addWidget(self.steps)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.reset()

    # --- state -----------------------------------------------------------
    def reset(self, title: str = "", skip: set[str] | None = None) -> None:
        self._skip = set(skip or ())
        self._started = time.monotonic()
        self._current = ""
        self._done: set[str] = set()
        if not hasattr(self, "_skip"):
            self._skip = set()
        self._fraction = 0.0
        self._stage_started = self._started
        self._last_count = 0
        self._has_total = False
        self.title.setText(title or t("Starting the engine…"))
        self.detail.setText("")
        self.elapsed.setText("0:00")
        self.bar.setValue(0)
        self.cancel.setEnabled(True)
        self.cancel.setVisible(True)
        self.cancel.setText(t("Stop"))
        self._render_steps()

    def start(self, title: str = "", skip: set[str] | None = None) -> None:
        """``skip``: stages this job will not have (a cover has no planning)."""
        self.reset(title, skip)
        self.setVisible(True)
        self._timer.start(500)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel.setVisible(False)

    def _cancel(self) -> None:
        self.cancel.setEnabled(False)
        self.cancel.setText(t("Stopping…"))
        self.cancel_clicked.emit()

    def on_stage(self, event: dict) -> None:
        sid = rq.STAGE_ALIASES.get(event.get("id", ""), event.get("id", ""))
        known = {s for s, _ in self.stages}
        if sid not in known:
            return
        if sid != self._current:
            if self._current:
                self._done.add(self._current)
            self._current = sid
            self._stage_started = time.monotonic()
            self._render_steps()
        if event.get("status") in ("completed", "truncated"):
            self._done.add(sid)
        completed, total = event.get("completed") or 0, event.get("total")
        self._last_count = completed
        self._has_total = bool(total)
        if total:
            self._fraction = min(1.0, completed / total)
        else:
            # Unknown length: ease towards 90% with elapsed time in this stage.
            seconds = time.monotonic() - self._stage_started
            self._fraction = 0.9 * (1 - math.exp(-seconds / 25))
        label = dict(self.stages)[sid]
        self.title.setText(t(label) + "…")
        unit = event.get("unit")
        if unit == "tokens" and completed:
            rate = completed / max(0.1, event.get("elapsed") or 0.1)
            self.detail.setText(t("{n} notes · {rate}/s", n=f"{completed:,}", rate=f"{rate:.0f}"))
        elif total:
            self.detail.setText(f"{completed} / {total}")
        elif event.get("tokens"):
            self.detail.setText(t("{n} notes", n=event["tokens"]))
        else:
            self.detail.setText("")
        self._update_bar()

    def finish(self, title: str) -> None:
        self._done = {s for s, _ in self.stages if s not in self._skip}
        self._current = ""
        self.bar.setValue(1000)
        self.title.setText(title)
        self.detail.setText("")
        self._render_steps()
        self.stop()

    def fail(self, title: str) -> None:
        self.title.setText(title)
        self.title.setStyleSheet(f"font-size:15px;font-weight:700;color:{theme.BAD};")
        self.stop()

    # --- drawing ---------------------------------------------------------
    def _update_bar(self) -> None:
        total_weight = sum(self.weights.get(s, 0.1) for s, _ in self.stages if s not in self._skip) or 1
        done = sum(self.weights.get(s, 0.1) for s in self._done if s not in self._skip)
        current = self.weights.get(self._current, 0.1) * self._fraction if self._current not in self._done else 0
        self.bar.setValue(int(1000 * min(0.99, (done + current) / total_weight)))

    def _tick(self) -> None:
        seconds = int(time.monotonic() - self._started)
        self.elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")
        # Stages with no known length keep easing forward between engine updates.
        if self._current and self._current not in self._done and not self._has_total:
            stage_seconds = time.monotonic() - self._stage_started
            self._fraction = 0.9 * (1 - math.exp(-stage_seconds / 25))
            self._update_bar()

    def _render_steps(self) -> None:
        self.title.setStyleSheet("font-size:15px;font-weight:700;")
        parts = []
        for sid, label in self.stages:
            if sid in self._skip:
                continue
            if sid in self._done:
                parts.append(f"<span style='color:{theme.OK}'>✓ {t(label)}</span>")
            elif sid == self._current:
                parts.append(f"<span style='color:{theme.ACCENT};font-weight:700'>● {t(label)}</span>")
            else:
                parts.append(f"<span style='color:{theme.DIM}'>○ {t(label)}</span>")
        self.steps.setText("<br>".join(parts))
