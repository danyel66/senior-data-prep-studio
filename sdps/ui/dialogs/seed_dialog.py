"""Generate the AdventureWorks dataset into the configured database, in the background."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, \
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget

from ...db import DEFAULT_SCALE, SCALES, ConnectionManager, SeedCancelled, Seeder
from ..theme import C, monospace_font
from ..workers import Task, run_task

_PG_SLOWDOWN = 5.5     # COPY + index build vs. pure DuckDB generation (measured)


def _fmt_count(n: float) -> str:
    return f"{n / 1e6:,.1f}M" if n >= 1e6 else f"{n / 1e3:,.0f}K"


def _fmt_secs(s: float) -> str:
    return f"~{s:.0f} s" if s < 90 else f"~{s / 60:.0f} min"


class SeedDialog(QDialog):
    seeded = pyqtSignal(dict)         # table -> row count

    def __init__(self, manager: ConnectionManager, parent: QWidget | None = None, *,
                 default_scale: str = DEFAULT_SCALE):
        super().__init__(parent)
        self.setWindowTitle("Generate AdventureWorks Data")
        self.setMinimumWidth(620)
        self._manager = manager
        self._task: Task | None = None
        self._started = 0.0
        self._disk_blocked: set[str] = set()
        cfg = manager.config
        is_pg = cfg.backend == "postgres"

        intro = QLabel("Builds a production-style AdventureWorks dataset (29 tables across person, "
                       "humanresources, production, purchasing and sales) with realistic skew, history "
                       "tables and data-quality quirks. Generation is deterministic.")
        intro.setWordWrap(True)
        target = QLabel(f"<b>Target:</b> {cfg.describe()}" +
                        ("" if is_pg else f"<br><span style='color:{C.text_muted}'>{cfg.duckdb_path}</span>"))
        target.setWordWrap(True)
        warning = QLabel(
            "⚠  Existing schemas person, humanresources, production, purchasing and sales in this "
            "database will be <b>dropped and recreated</b>." if is_pg else
            "The current database file is replaced only after the new one is fully built, so cancelling is safe.")
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {C.warning if is_pg else C.text_muted};")

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.scale_group = QButtonGroup(self)
        free_mb = self._free_disk_mb()
        for row, scale in enumerate(SCALES.values()):
            radio = QRadioButton(scale.label)
            radio.setProperty("scale", scale.key)
            radio.setChecked(scale.key == default_scale)
            self.scale_group.addButton(radio)
            secs = scale.est_seconds * (_PG_SLOWDOWN if is_pg else 1)
            detail = QLabel(f"{scale.orders:,} orders · ~{_fmt_count(scale.est_order_lines)} lines · "
                            f"{_fmt_secs(secs)} · ~{scale.est_disk_mb:,} MB")
            detail.setObjectName("Muted")
            if free_mb is not None and scale.est_disk_mb * 2 > free_mb:
                radio.setEnabled(False)
                self._disk_blocked.add(scale.key)
                detail.setText(detail.text() + "  (not enough free disk)")
            grid.addWidget(radio, row, 0)
            grid.addWidget(detail, row, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar { max-height: 8px; }")
        self.status = QLabel("Ready.")
        self.status.setObjectName("Muted")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(monospace_font(9))
        self.log.setMinimumHeight(140)

        self.start_button = QPushButton("Generate")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self._close_or_cancel)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addSpacing(4)
        layout.addWidget(target)
        layout.addWidget(warning)
        layout.addSpacing(6)
        layout.addLayout(grid)
        layout.addSpacing(6)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)

    # ---- helpers ---------------------------------------------------------------------------
    def selected_scale(self) -> str:
        btn = self.scale_group.checkedButton()
        return btn.property("scale") if btn else DEFAULT_SCALE

    def select_scale(self, key: str) -> None:
        for b in self.scale_group.buttons():
            if b.property("scale") == key:
                b.setChecked(True)

    @property
    def running(self) -> bool:
        return self._task is not None

    def _free_disk_mb(self) -> float | None:
        if self._manager.config.backend != "duckdb":
            return None
        try:
            folder = Path(self._manager.config.duckdb_path).parent
            folder.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(folder).free / 1e6
        except OSError:
            return None

    def _append(self, text: str, color: str | None = None) -> None:
        elapsed = time.perf_counter() - self._started if self._started else 0
        line = f"[{elapsed:6.1f}s] {text}"
        self.log.appendHtml(f'<span style="color:{color}">{line}</span>' if color else line.replace("<", "&lt;"))

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        for b in self.scale_group.buttons():
            b.setEnabled(not running and b.property("scale") not in self._disk_blocked)
        self.close_button.setText("Cancel" if running else "Close")

    # ---- run ------------------------------------------------------------------------------
    def _start(self) -> None:
        scale = self.selected_scale()
        cfg = self._manager.config
        seeder = Seeder(scale)
        self._started = time.perf_counter()
        self.log.clear()
        self._append(f"Generating '{scale}' dataset into {cfg.describe()}")
        self.progress.setValue(0)
        self._set_running(True)

        if cfg.backend == "duckdb":
            tmp = Path(cfg.duckdb_path + ".building")

            def job(task: Task):
                seeder._progress = task.report
                return seeder.build_duckdb(tmp)
        else:
            tmp = None
            conninfo = cfg.pg_conninfo()

            def job(task: Task):
                seeder._progress = task.report
                return seeder.build_postgres(conninfo)

        self._task = run_task(self, job, on_result=lambda counts: self._done(counts, tmp),
                              on_error=lambda m, tb: self._failed(m, tb, tmp),
                              on_progress=self._on_progress)
        self._task.on_cancel(seeder.cancel)

    def _on_progress(self, frac: float, message: str) -> None:
        self.progress.setValue(int(frac * 1000))
        self.status.setText(message)
        if message.endswith(("✓", ")")) or "COPY" in message:
            self._append(message)

    def _done(self, counts: dict, tmp: Path | None) -> None:
        if tmp is not None:
            self._append("Swapping in the new database file…")
            try:
                self._manager.install_duckdb_file(tmp)
            except Exception as e:  # noqa: BLE001
                self._failed(f"Built successfully but could not replace the database file: {e}", "", tmp)
                return
        self._task = None
        self._set_running(False)
        total = sum(counts.values())
        self.progress.setValue(1000)
        secs = time.perf_counter() - self._started
        self.status.setText(f"Done: {total:,} rows across {len(counts)} tables in {secs:.1f}s.")
        self._append(f"✓ Done: {total:,} rows across {len(counts)} tables", C.success)
        self.start_button.setText("Regenerate")
        self.seeded.emit(counts)

    def _failed(self, message: str, tb: str, tmp: Path | None) -> None:
        self._task = None
        self._set_running(False)
        if tmp is not None:
            for p in (tmp, Path(str(tmp) + ".wal")):
                p.unlink(missing_ok=True)
        cancelled = SeedCancelled.__name__ in tb or "Interrupt" in message
        if cancelled:
            self.status.setText("Cancelled. Your existing database was left unchanged.")
            self._append("Cancelled", C.warning)
        else:
            self.status.setText("Failed. See the log below.")
            self._append("✗ " + message, C.danger)

    def _close_or_cancel(self) -> None:
        if self._task is not None:
            self.status.setText("Cancelling…")
            self._task.cancel()
        else:
            self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._task is not None:
            if QMessageBox.question(self, "Cancel generation?",
                                    "Data generation is still running. Cancel it?") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._task.cancel()
            self._task.wait(15_000)
        super().closeEvent(event)
