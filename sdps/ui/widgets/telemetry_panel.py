"""Execution telemetry: metric cards, a live process-memory gauge and an execution log."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, \
    QWidget

from ..theme import C, monospace_font

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except ImportError:  # psutil is optional; memory card then shows "n/a"
    _PROC = None


def process_rss_mb() -> float | None:
    return _PROC.memory_info().rss / 1_048_576 if _PROC else None


@dataclass
class ExecutionMetrics:
    elapsed_ms: float
    rows: int
    columns: int
    peak_memory_mb: float | None = None
    status: str = "OK"


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "—", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        caption = QLabel(label)
        caption.setObjectName("MetricLabel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value: str, color: str | None = None) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {color};" if color else "")


class TelemetryPanel(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")

        title = QLabel("Execution Telemetry")
        title.setObjectName("PanelTitle")
        self.live_mem_label = QLabel()
        self.live_mem_label.setObjectName("Muted")
        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.live_mem_label)

        self.status_card = MetricCard("Status", "Idle")
        self.time_card = MetricCard("Execution time")
        self.rows_card = MetricCard("Rows returned")
        self.mem_card = MetricCard("Peak memory")
        cards = QGridLayout()
        cards.setContentsMargins(12, 0, 12, 8)
        cards.setSpacing(8)
        for i, card in enumerate((self.status_card, self.time_card, self.rows_card, self.mem_card)):
            cards.addWidget(card, 0, i)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(monospace_font(9))
        self.log.setMaximumBlockCount(5_000)   # bounded so long sessions don't grow memory
        self.log.setPlaceholderText("Execution log…")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addWidget(self.log, 1)

        self._mem_timer = QTimer(self, interval=1_000, timeout=self._refresh_live_memory)
        self._mem_timer.start()
        self._refresh_live_memory()

    # ---- public API -----------------------------------------------------------------------
    def mark_running(self) -> None:
        self.status_card.set_value("Running…", C.warning)
        self.time_card.set_value("—")
        self.rows_card.set_value("—")
        self.mem_card.set_value("—")

    def show_metrics(self, m: ExecutionMetrics) -> None:
        ok = m.status == "OK"
        self.status_card.set_value(m.status, C.success if ok else C.danger)
        self.time_card.set_value(_fmt_duration(m.elapsed_ms))
        self.rows_card.set_value(f"{m.rows:,}")
        self.mem_card.set_value("n/a" if m.peak_memory_mb is None else f"{m.peak_memory_mb:,.1f} MB")

    def append_log(self, message: str, level: str = "INFO") -> None:
        color = {"INFO": C.text_muted, "OK": C.success, "WARN": C.warning, "ERROR": C.danger}.get(level, C.text)
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log.appendHtml(f'<span style="color:{C.syn_comment}">{ts}</span> '
                            f'<span style="color:{color}">[{level}]</span> {safe}')
        bar = self.log.verticalScrollBar()          # always show the newest line
        bar.setValue(bar.maximum())

    def _refresh_live_memory(self) -> None:
        rss = process_rss_mb()
        self.live_mem_label.setText("process RSS: n/a" if rss is None else f"process RSS: {rss:,.0f} MB")


def _fmt_duration(ms: float) -> str:
    if ms < 1:
        return f"{ms * 1000:.0f} µs"
    if ms < 1_000:
        return f"{ms:.1f} ms"
    return f"{ms / 1000:.2f} s"
