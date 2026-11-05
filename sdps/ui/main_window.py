"""Main window: split-screen layout, menus, toolbar, status bar and persisted layout state.

    ┌──────────────────────── toolbar ─────────────────────────┐
    │ Schema   ║ AI Challenge      ║ Results grid              │
    │ browser  ║───────────────────║───────────────────────────│
    │          ║ Code editor       ║ Execution telemetry       │
    └─────────────────────── status bar ───────────────────────┘
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QByteArray, QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import QLabel, QMainWindow, QMessageBox, QProgressBar, QSplitter, QToolBar, QVBoxLayout, \
    QWidget

from .. import APP_NAME, ORG_NAME, __version__
from ..db import ConnectionManager, ConnectionStatus, DatabaseConfig, TableInfo
from .dialogs import DatabaseSettingsDialog, SeedDialog
from .theme import C
from .widgets import EditorPanel, PromptPanel, ResultsPanel, TelemetryPanel
from .widgets.results_panel import SyntheticRows
from .widgets.schema_browser import SchemaBrowser
from .widgets.telemetry_panel import ExecutionMetrics, process_rss_mb
from .workers import run_task, wait_for_all

_LAYOUT_KEY = "layout/v2"     # bump when the splitter structure changes


class MainWindow(QMainWindow):
    def __init__(self, manager: ConnectionManager | None = None, *, auto_connect: bool = True,
                 prompt_to_seed: bool = True) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1680, 980)
        self.setMinimumSize(1180, 700)
        self._settings = QSettings(ORG_NAME, APP_NAME)
        self.db = manager or ConnectionManager(DatabaseConfig.load())
        self._prompt_to_seed = prompt_to_seed
        self._db_busy = False
        self._seed_dialog: SeedDialog | None = None
        self.tables: list[TableInfo] = []

        self.schema_browser = SchemaBrowser()
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
        if auto_connect:
            QTimer.singleShot(0, self.connect_database)

    # ---- construction ---------------------------------------------------------------------
    def _build_layout(self) -> None:
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(self.prompt_panel)
        self.left_splitter.addWidget(self.editor_panel)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.results_panel)
        self.right_splitter.addWidget(self.telemetry_panel)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.schema_browser)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 1)
        self._reset_layout()

        for s in (self.left_splitter, self.right_splitter, self.main_splitter):
            s.setChildrenCollapsible(False)
            s.setHandleWidth(6)
        self.main_splitter.setCollapsible(0, True)

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
        self.act_demo = action("Load Demo Grid (1M rows)", self._load_demo_dataset, "Ctrl+D",
                               "Fill the grid with 1,000,000 synthetic rows to test virtualisation")
        self.act_clear = action("Clear Results", self._clear_results, "Ctrl+L")
        self.act_toggle_schema = action("Schema Browser", self._toggle_schema_browser, "Ctrl+B")
        self.act_toggle_schema.setCheckable(True)
        self.act_toggle_schema.setChecked(True)
        self.act_reset_layout = action("Reset Layout", self._reset_layout)
        self.act_quit = action("Quit", self.close, "Ctrl+Q")
        self.act_about = action("About", self._show_about)

        self.act_db_settings = action("Connection Settings…", self.open_database_settings, "Ctrl+,",
                                      "Choose DuckDB or PostgreSQL")
        self.act_seed = action("Generate AdventureWorks Data…", self.open_seed_dialog, "Ctrl+Shift+G",
                               "Build or rebuild the practice dataset")
        self.act_reconnect = action("Reconnect", self.connect_database)
        self.act_refresh_schema = action("Refresh Schema", self.refresh_schema, "F6")
        self.act_disconnect = action("Disconnect", self.disconnect_database)

        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_quit)
        m_db = mb.addMenu("&Database")
        m_db.addActions([self.act_db_settings, self.act_seed])
        m_db.addSeparator()
        m_db.addActions([self.act_refresh_schema, self.act_reconnect, self.act_disconnect])
        m_run = mb.addMenu("&Run")
        m_run.addActions([self.act_run, self.act_new_challenge])
        m_view = mb.addMenu("&View")
        m_view.addActions([self.act_toggle_schema, self.act_demo, self.act_clear])
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
        tb.addActions([self.act_run, self.act_new_challenge])
        tb.addSeparator()
        self.act_db_settings_tb = QAction("Database…", self, triggered=self.open_database_settings)
        self.act_seed_tb = QAction("Generate Data…", self, triggered=self.open_seed_dialog)
        tb.addActions([self.act_db_settings_tb, self.act_seed_tb, self.act_clear])
        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.db_status = QLabel()
        self.db_status.setStyleSheet("padding: 0 8px;")
        self.ai_status = QLabel("●  AI: not configured")
        self.ai_status.setStyleSheet(f"color: {C.text_muted}; padding: 0 8px;")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setRange(0, 0)        # indeterminate
        self.progress.setTextVisible(False)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.db_status)
        sb.addPermanentWidget(self.ai_status)
        self._set_db_indicator("Not connected", C.text_muted)
        sb.showMessage("Ready", 3_000)

    def _connect_signals(self) -> None:
        self.editor_panel.run_requested.connect(self._on_run_requested)
        self.editor_panel.cancel_requested.connect(self._on_cancel_requested)
        self.editor_panel.language_changed.connect(
            lambda lang: self.statusBar().showMessage(f"Language: {lang}", 2_000))
        self.prompt_panel.generate_requested.connect(self._on_generate_requested)
        self.schema_browser.insert_requested.connect(self._insert_into_editor)
        self.schema_browser.refresh_requested.connect(self.refresh_schema)
        self.schema_browser.seed_requested.connect(self.open_seed_dialog)

    # ---- database -------------------------------------------------------------------------
    def _set_db_indicator(self, text: str, color: str, tooltip: str = "") -> None:
        self.db_status.setText(f"●  {text}")
        self.db_status.setStyleSheet(f"color: {color}; padding: 0 8px;")
        self.db_status.setToolTip(tooltip)
        self.db_status.setMinimumWidth(self.db_status.sizeHint().width())   # never truncate

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._db_busy = busy
        self.progress.setVisible(busy)
        for a in (self.act_reconnect, self.act_refresh_schema, self.act_seed, self.act_seed_tb,
                  self.act_db_settings, self.act_db_settings_tb):
            a.setEnabled(not busy)
        if message:
            self.statusBar().showMessage(message)

    def connect_database(self) -> None:
        if self._db_busy:
            return
        cfg = self.db.config
        self._set_busy(True, f"Connecting to {cfg.describe()}…")
        self._set_db_indicator("Connecting…", C.warning)
        self.schema_browser.show_message("Connecting…")
        self.telemetry_panel.append_log(f"Connecting to {cfg.describe()}")

        def job(_task):
            status = self.db.connect()
            return status, self.db.introspect()

        run_task(self, job, on_result=self._on_connected, on_error=self._on_connect_failed,
                 on_finished=lambda: self._set_busy(False))

    def _on_connected(self, result: tuple[ConnectionStatus, list[TableInfo]]) -> None:
        status, tables = result
        self._apply_status(status)
        self._apply_tables(tables)
        self.telemetry_panel.append_log(f"Connected · {status.server_version}", "OK")
        self.statusBar().showMessage("Connected", 3_000)
        if self._prompt_to_seed and not status.seeded and not tables:
            self._prompt_to_seed = False          # ask once per session
            QTimer.singleShot(0, self._offer_first_seed)

    def _on_connect_failed(self, message: str, _tb: str) -> None:
        first = message.splitlines()[0] if message else "unknown error"
        self._set_db_indicator(f"{self.db.config.describe()} · connection failed", C.danger, message)
        self.schema_browser.show_message(f"Could not connect.<br><br><span style='color:{C.danger}'>{first}</span>"
                                         "<br><br>Database → Connection Settings…")
        self.telemetry_panel.append_log(f"Connection failed: {first}", "ERROR")
        self.statusBar().showMessage("Connection failed", 5_000)

    def _apply_status(self, status: ConnectionStatus) -> None:
        if status.seeded and status.metadata:
            m = status.metadata
            orders = int(m.get("orders", 0))
            self._set_db_indicator(f"{status.description} · {m.get('scale', '?')} · {orders:,} orders", C.success,
                                   f"{status.server_version}\nGenerated {m.get('created_at', '')}")
        else:
            self._set_db_indicator(f"{status.description} · no AdventureWorks data", C.warning, status.server_version)

    def _apply_tables(self, tables: list[TableInfo]) -> None:
        self.tables = tables
        self.schema_browser.set_tables(tables)

    def refresh_schema(self) -> None:
        if self._db_busy or not self.db.is_connected:
            if not self.db.is_connected:
                self.connect_database()
            return
        self._set_busy(True, "Refreshing schema…")
        run_task(self, lambda _t: (self.db.status(), self.db.introspect()),
                 on_result=lambda r: (self._apply_status(r[0]), self._apply_tables(r[1]),
                                      self.statusBar().showMessage("Schema refreshed", 2_000)),
                 on_error=self._on_connect_failed, on_finished=lambda: self._set_busy(False))

    def disconnect_database(self) -> None:
        self.db.disconnect()
        self._set_db_indicator("Disconnected", C.text_muted)
        self.schema_browser.show_message("Disconnected.<br><br>Database → Reconnect")
        self.telemetry_panel.append_log("Disconnected")

    def open_database_settings(self) -> None:
        dlg = DatabaseSettingsDialog(self.db.config, self)
        if dlg.exec() == DatabaseSettingsDialog.DialogCode.Accepted:
            cfg = dlg.config()
            cfg.save()
            self.db.disconnect()
            self.db.config = cfg
            self._prompt_to_seed = True
            self.connect_database()

    def open_seed_dialog(self, default_scale: str | None = None) -> None:
        if not self.db.is_connected and not self._db_busy:
            QMessageBox.information(self, "Not connected", "Connect to a database first "
                                    "(Database → Connection Settings…).")
            return
        dlg = SeedDialog(self.db, self, **({"default_scale": default_scale} if default_scale else {}))
        dlg.seeded.connect(self._on_seeded)
        self._seed_dialog = dlg
        dlg.exec()
        self._seed_dialog = None
        if not self.db.is_connected:          # e.g. a cancelled swap: make sure we're connected again
            self.connect_database()

    def _offer_first_seed(self) -> None:
        answer = QMessageBox.question(
            self, "Generate practice data?",
            "Your practice database is empty.\n\nGenerate the AdventureWorks dataset now? "
            "The recommended Medium size (1M orders, ~2.4M order lines) takes a few seconds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.open_seed_dialog()

    def _on_seeded(self, counts: dict) -> None:
        total = sum(counts.values())
        self.telemetry_panel.append_log(f"AdventureWorks generated: {total:,} rows in {len(counts)} tables", "OK")
        self.refresh_schema()

    # ---- editor / run stubs ---------------------------------------------------------------
    def _insert_into_editor(self, text: str) -> None:
        editor = self.editor_panel.editor
        editor.insertPlainText(text)
        editor.setFocus()

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
        self.telemetry_panel.append_log(f"Demo grid bound: {len(rows):,} rows (lazy, zero-copy)", "OK")

    def _clear_results(self) -> None:
        self.results_panel.clear()
        self.telemetry_panel.append_log("Results cleared")

    def _show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> v{__version__}<br><br>Senior-level SQL, Python and "
                          "analytics-engineering practice on production-scale data.")

    # ---- layout / state -------------------------------------------------------------------
    _SPLITTERS = ("main_splitter", "left_splitter", "right_splitter")

    def _toggle_schema_browser(self) -> None:
        visible = not self.schema_browser.isVisible()
        self.schema_browser.setVisible(visible)
        self.act_toggle_schema.setChecked(visible)

    def _restore_state(self) -> None:
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        for name in self._SPLITTERS:
            state = self._settings.value(f"{_LAYOUT_KEY}/{name}")
            if isinstance(state, QByteArray):
                getattr(self, name).restoreState(state)

    def _reset_layout(self) -> None:
        self.schema_browser.setVisible(True)
        if hasattr(self, "act_toggle_schema"):
            self.act_toggle_schema.setChecked(True)
        self.main_splitter.setSizes([270, 700, 690])
        self.left_splitter.setSizes([330, 600])
        self.right_splitter.setSizes([620, 310])

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._settings.setValue("window/geometry", self.saveGeometry())
        for name in self._SPLITTERS:
            self._settings.setValue(f"{_LAYOUT_KEY}/{name}", getattr(self, name).saveState())
        wait_for_all(5_000)
        self.db.disconnect()
        super().closeEvent(event)
