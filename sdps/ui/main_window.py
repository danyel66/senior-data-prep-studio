"""Main window: split-screen layout, menus, toolbar, status bar and persisted layout state.

    ┌───────────────── toolbar ─────────────────┐
    │ AI Challenge       ║ Results grid         │
    │────────────────────║──────────────────────│
    │ Code editor        ║ Execution telemetry  │
    └──────────────── status bar ───────────────┘
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QByteArray, QSettings, Qt
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import QLabel, QMainWindow, QMessageBox, QProgressBar, QSplitter, QToolBar, \
    QWidget, QVBoxLayout

from .. import APP_NAME, ORG_NAME, __version__
from .widgets import EditorPanel, PromptPanel, ResultsPanel, TelemetryPanel
from .widgets.results_panel import SyntheticRows
from .widgets.telemetry_panel import ExecutionMetrics, process_rss_mb


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1600, 960)
        self.setMinimumSize(1100, 680)
        self._settings = QSettings(ORG_NAME, APP_NAME)

        self.prompt_panel = PromptPanel()
        self.editor_panel = EditorPanel()
        self.results_panel = ResultsPanel()
        self.telemetry_panel = TelemetryPanel()

        self._build_layout()
        self._build_actions()
        self._build_status_bar()
        self._connect_signals()
        self._restore_state()

        self.telemetry_panel.append_log(f"{APP_NAME} v{__version__} started", "OK")
        self.telemetry_panel.append_log("Database engine: not connected (Phase 2)", "WARN")

    # ---- construction ---------------------------------------------------------------------
    def _build_layout(self) -> None:
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(self.prompt_panel)
        self.left_splitter.addWidget(self.editor_panel)
        self.left_splitter.setSizes([330, 600])

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.results_panel)
        self.right_splitter.addWidget(self.telemetry_panel)
        self.right_splitter.setSizes([620, 310])

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setSizes([760, 840])

        for s in (self.left_splitter, self.right_splitter, self.main_splitter):
            s.setChildrenCollapsible(False)
            s.setHandleWidth(6)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.main_splitter)
        self.setCentralWidget(central)

    def _build_actions(self) -> None:
        def action(text: str, slot, shortcut: str | None = None, tip: str = "") -> QAction:
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setStatusTip(tip)
            a.triggered.connect(slot)
            return a

        self.act_run = action("Run", self.editor_panel._emit_run, "F5", "Execute the current script")
        self.act_new_challenge = action("New Challenge", self.prompt_panel.generate_button.click,
                                        "Ctrl+G", "Generate a new AI challenge")
        self.act_demo = action("Load Demo Dataset (1M rows)", self._load_demo_dataset, "Ctrl+D",
                               "Fill the grid with 1,000,000 synthetic rows to test virtualisation")
        self.act_clear = action("Clear Results", self._clear_results, "Ctrl+L")
        self.act_reset_layout = action("Reset Layout", self._reset_layout)
        self.act_quit = action("Quit", self.close, "Ctrl+Q")
        self.act_about = action("About", self._show_about)

        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_quit)
        m_run = mb.addMenu("&Run")
        m_run.addActions([self.act_run, self.act_new_challenge])
        m_view = mb.addMenu("&View")
        m_view.addActions([self.act_demo, self.act_clear])
        m_view.addSeparator()
        m_view.addAction(self.act_reset_layout)
        mb.addMenu("&Help").addAction(self.act_about)

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setObjectName("MainToolbar")
        brand = QLabel(f"  ◆  {APP_NAME}  ")
        brand.setStyleSheet("font-weight: 700; font-size: 11pt;")
        tb.addWidget(brand)
        tb.addSeparator()
        tb.addActions([self.act_run, self.act_new_challenge, self.act_demo, self.act_clear])
        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.db_status = QLabel("●  DB: disconnected")
        self.db_status.setStyleSheet("color: #f5a524; padding: 0 8px;")
        self.ai_status = QLabel("●  AI: not configured")
        self.ai_status.setStyleSheet("color: #8a93a6; padding: 0 8px;")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setRange(0, 0)        # indeterminate
        self.progress.setTextVisible(False)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.db_status)
        sb.addPermanentWidget(self.ai_status)
        sb.showMessage("Ready", 3_000)

    def _connect_signals(self) -> None:
        self.editor_panel.run_requested.connect(self._on_run_requested)
        self.editor_panel.cancel_requested.connect(self._on_cancel_requested)
        self.editor_panel.language_changed.connect(
            lambda lang: self.statusBar().showMessage(f"Language: {lang}", 2_000))
        self.prompt_panel.generate_requested.connect(self._on_generate_requested)

    # ---- slots (Phase 1 stubs; real engines are injected in later phases) -----------------
    def _on_run_requested(self, language: str, code: str) -> None:
        lines = code.count("\n") + 1
        self.telemetry_panel.append_log(f"Run requested: {language}, {lines} lines, {len(code):,} chars")
        self.telemetry_panel.append_log("Execution engine not available yet (Phase 4)", "WARN")
        self.statusBar().showMessage("Execution engine arrives in Phase 4", 4_000)

    def _on_cancel_requested(self) -> None:
        self.telemetry_panel.append_log("Cancel requested", "WARN")

    def _on_generate_requested(self, category: str, difficulty: str) -> None:
        self.telemetry_panel.append_log(f"Challenge requested: {category} / {difficulty}")
        self.statusBar().showMessage("AI generator arrives in Phase 3", 4_000)

    def _load_demo_dataset(self) -> None:
        self.telemetry_panel.mark_running()
        start = time.perf_counter()
        rows = SyntheticRows(1_000_000)
        self.results_panel.show_results(SyntheticRows.COLUMNS, rows)
        elapsed = (time.perf_counter() - start) * 1000
        self.telemetry_panel.show_metrics(ExecutionMetrics(
            elapsed_ms=elapsed, rows=len(rows), columns=len(SyntheticRows.COLUMNS),
            peak_memory_mb=process_rss_mb(), status="OK"))
        self.telemetry_panel.append_log(f"Demo dataset bound: {len(rows):,} rows (lazy, zero-copy)", "OK")

    def _clear_results(self) -> None:
        self.results_panel.clear()
        self.telemetry_panel.append_log("Results cleared")

    def _show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> v{__version__}<br><br>Senior-level SQL, Python and "
                          "analytics-engineering practice on production-scale data.")

    # ---- state persistence ----------------------------------------------------------------
    _SPLITTERS = ("main_splitter", "left_splitter", "right_splitter")

    def _restore_state(self) -> None:
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        for name in self._SPLITTERS:
            state = self._settings.value(f"window/{name}")
            if isinstance(state, QByteArray):
                getattr(self, name).restoreState(state)

    def _reset_layout(self) -> None:
        self.main_splitter.setSizes([760, 840])
        self.left_splitter.setSizes([330, 600])
        self.right_splitter.setSizes([620, 310])

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._settings.setValue("window/geometry", self.saveGeometry())
        for name in self._SPLITTERS:
            self._settings.setValue(f"window/{name}", getattr(self, name).saveState())
        super().closeEvent(event)
