"""Main window: split-screen layout, menus, toolbar, status bar and persisted layout state.

    ┌──────────────────────── toolbar ─────────────────────────┐
    │ Schema   ║ AI Challenge      ║ Results grid              │
    │ browser  ║───────────────────║───────────────────────────│
    │          ║ Code editor       ║ Execution telemetry       │
    └─────────────────────── status bar ───────────────────────┘
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QByteArray, QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import QLabel, QMainWindow, QMessageBox, QProgressBar, QSplitter, QToolBar, QVBoxLayout, \
    QWidget

from .. import APP_NAME, ORG_NAME, __version__
from ..ai import AICancelled, AIConfig, AIError, Challenge, ChallengeGenerator, ChallengeStore, \
    GenerationResult, OllamaProvider, claude_credentials_hint
from ..db import ConnectionManager, ConnectionStatus, DatabaseConfig, DatabaseError, TableInfo
from ..execution import ExecutionCancelled, ExecutionError, Executor, check_python, check_sql
from ..progress import SOLVED, ProgressStore
from .dialogs import AISettingsDialog, DatabaseSettingsDialog, ProgressDialog, SeedDialog
from .theme import C
from .widgets import EditorPanel, PromptPanel, ResultsPanel, TelemetryPanel
from .widgets.results_panel import SyntheticRows
from .widgets.schema_browser import SchemaBrowser
from .widgets.telemetry_panel import ExecutionMetrics, process_rss_mb
from .workers import run_task, wait_for_all

_LAYOUT_KEY = "layout/v2"     # bump when the splitter structure changes


class _RunBridge(QObject):
    """Carries 'first rows are available' from the worker thread to the UI thread."""
    columns_ready = pyqtSignal(object, object)       # (columns, growing row list)


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
        self.ai_config = AIConfig.load()
        self.challenges = ChallengeStore()
        self._generator: ChallengeGenerator | None = None
        self._executor: Executor | None = None
        self._run_started = 0.0
        self._bridge = _RunBridge(self)
        self._bridge.columns_ready.connect(self.results_panel_columns_ready)
        self._live_timer = QTimer(self, interval=200, timeout=self._update_live_telemetry)
        self.progress_store = ProgressStore()
        self._run_context: tuple | None = None
        self._loading_editor = False
        self._last_activity = time.monotonic()
        self._draft_timer = QTimer(self, singleShot=True, interval=1_200, timeout=self._save_draft)
        self._activity_timer = QTimer(self, interval=15_000, timeout=self._count_active_time)
        self._activity_timer.start()

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
        self._apply_ai_config()
        if recent := self.challenges.all():
            self._activate_challenge(recent[0])
        self._sync_checkable()
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
        self.act_check = action("Check Answer", self.editor_panel._emit_check, "Ctrl+Shift+Return",
                                "Run and compare with the reference solution")
        self.act_cancel_run = action("Cancel Run", self.cancel_execution, None)
        self.act_new_challenge = action("New Challenge", self._new_challenge_shortcut, "Ctrl+G",
                                        "Generate a new AI challenge")
        self.act_hint = action("Reveal Next Hint", self.prompt_panel.next_hint, "Ctrl+Shift+H")
        self.act_solution = action("Show Solution", self.prompt_panel.show_solution, "Ctrl+Shift+S")
        self.act_insert_solution = action("Insert Solution into Editor", self._insert_solution)
        self.act_progress = action("My Progress…", self.open_progress, "Ctrl+Shift+P",
                                   "Solved challenges, time spent and weakest topics")
        self.act_reset_code = action("Reset My Code to Starter", self._reset_code)
        self.act_ai_settings = action("AI Settings…", self.open_ai_settings, None,
                                      "Choose Claude or Ollama and set your API key")
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
        m_challenge = mb.addMenu("&Challenge")
        m_challenge.addActions([self.act_new_challenge, self.act_hint, self.act_solution, self.act_insert_solution,
                                self.act_reset_code])
        m_challenge.addSeparator()
        m_challenge.addAction(self.act_progress)
        self.recent_menu = m_challenge.addMenu("Recent Challenges")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m_challenge.addSeparator()
        m_challenge.addAction(self.act_ai_settings)
        m_run = mb.addMenu("&Run")
        m_run.addActions([self.act_run, self.act_check, self.act_cancel_run])
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
        self.act_ai_settings_tb = QAction("AI Settings…", self, triggered=self.open_ai_settings)
        self.act_progress_tb = QAction("Progress", self, triggered=self.open_progress)
        tb.addActions([self.act_progress_tb, self.act_db_settings_tb, self.act_seed_tb, self.act_ai_settings_tb,
                       self.act_clear])
        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.db_status = QLabel()
        self.db_status.setStyleSheet("padding: 0 8px;")
        self.ai_status = QLabel()
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
        self.editor_panel.run_requested.connect(self.run_code)
        self.editor_panel.check_requested.connect(self.check_answer)
        self.editor_panel.cancel_requested.connect(self.cancel_execution)
        self.editor_panel.language_changed.connect(
            lambda lang: self.statusBar().showMessage(f"Language: {lang}", 2_000))
        self.prompt_panel.generate_requested.connect(self.generate_challenge)
        self.prompt_panel.cancel_requested.connect(self.cancel_challenge)
        self.prompt_panel.insert_solution_requested.connect(self._insert_solution)
        self.prompt_panel.settings_requested.connect(self.open_ai_settings)
        self.prompt_panel.hint_revealed.connect(self._on_hint_revealed)
        self.prompt_panel.solution_revealed.connect(self._on_solution_revealed)
        self.editor_panel.editor.textChanged.connect(self._on_editor_changed)
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

    # ---- AI challenges ----------------------------------------------------------------------
    def _set_ai_indicator(self, text: str, color: str, tooltip: str = "") -> None:
        self.ai_status.setText(f"●  AI: {text}")
        self.ai_status.setStyleSheet(f"color: {color}; padding: 0 8px;")
        self.ai_status.setToolTip(tooltip)
        self.ai_status.setMinimumWidth(self.ai_status.sizeHint().width())

    def _apply_ai_config(self) -> None:
        cfg = self.ai_config
        self.prompt_panel.set_provider_label(cfg.describe())
        if cfg.provider == "claude":
            source = claude_credentials_hint()
            if source:
                self._set_ai_indicator(cfg.describe(), C.success, f"Credentials: {source}")
            else:
                self._set_ai_indicator(f"{cfg.describe()} · no API key", C.warning, "Challenge → AI Settings…")
            return
        self._set_ai_indicator(f"{cfg.describe()} · checking…", C.text_muted)
        provider = OllamaProvider(cfg)

        def ok(models: list[str]) -> None:
            if cfg.ollama_model and cfg.ollama_model not in models:
                self._set_ai_indicator(f"{cfg.describe()} · model not installed", C.warning,
                                       f"Run: ollama pull {cfg.ollama_model}")
            else:
                self._set_ai_indicator(cfg.describe(), C.success, cfg.ollama_url)

        run_task(self, lambda _t: provider.list_models(), on_result=ok,
                 on_error=lambda m, _tb: self._set_ai_indicator("Ollama offline", C.danger, m))

    def open_ai_settings(self, message: str = "") -> bool:
        dlg = AISettingsDialog(self.ai_config, self, message=message if isinstance(message, str) else "")
        if dlg.exec() != AISettingsDialog.DialogCode.Accepted:
            return False
        self.ai_config = dlg.config()
        self._apply_ai_config()
        self.telemetry_panel.append_log(f"AI provider: {self.ai_config.describe()}")
        return True

    def _new_challenge_shortcut(self) -> None:
        if self._generator is None:
            self.generate_challenge(*self.prompt_panel.selection())

    def generate_challenge(self, category: str, difficulty: str, topic: str = "") -> None:
        if self._generator is not None:
            return
        if not self.db.is_connected:
            self.prompt_panel.show_error("Connect to a database first (Database → Connection Settings…).")
            return
        if self.ai_config.provider == "claude" and not claude_credentials_hint():
            if not self.open_ai_settings("Add your Anthropic API key to generate challenges with Claude, "
                                         "or switch to a local Ollama model."):
                return
            if self.ai_config.provider == "claude" and not claude_credentials_hint():
                return
        generator = ChallengeGenerator(self.db, self.ai_config, self.challenges)
        self._generator = generator
        self.prompt_panel.set_busy(True)
        self.act_new_challenge.setEnabled(False)
        label = f"{category} · {difficulty}" + (f" · {topic}" if topic else "")
        self.telemetry_panel.append_log(f"Generating challenge ({label}) with {self.ai_config.describe()}")
        started = time.perf_counter()

        def job(task):
            try:
                return generator.generate(category, difficulty, topic or None,
                                          progress=lambda msg: task.report(0.0, msg))
            except (AIError, AICancelled) as e:
                return e                    # delivered to on_result with its type intact

        run_task(self, job, on_result=lambda r: self._on_challenge_result(r, started),
                 on_error=lambda m, _tb: self._on_challenge_result(AIError(f"Unexpected error: {m}"), started),
                 on_progress=lambda _f, msg: self.prompt_panel.set_status(msg))

    def cancel_challenge(self) -> None:
        if self._generator is not None:
            self.prompt_panel.set_status("Cancelling…")
            self._generator.cancel()

    def _on_challenge_result(self, result, started: float) -> None:
        self._generator = None
        self.prompt_panel.set_busy(False)
        self.act_new_challenge.setEnabled(True)
        if isinstance(result, AICancelled):
            self.prompt_panel.set_status("Cancelled.")
            self.telemetry_panel.append_log("Challenge generation cancelled", "WARN")
            return
        if isinstance(result, AIError):
            self.prompt_panel.show_error(str(result), result.settings_hint)
            self.telemetry_panel.append_log(f"Challenge generation failed: {result}", "ERROR")
            if self.ai_config.provider == "ollama" and result.settings_hint:
                self._set_ai_indicator("Ollama unavailable", C.danger, str(result))
            return
        assert isinstance(result, GenerationResult)
        ch = result.challenge
        self.challenges.add(ch)
        self._activate_challenge(ch)
        cost = "" if result.cost_usd is None else f" · ${result.cost_usd:.4f}"
        verdict = "verified" if ch.verified else "NOT verified: " + "; ".join(ch.verification_notes)
        self.telemetry_panel.append_log(
            f"Challenge ready: “{ch.title}” · {ch.model} · {result.output_tokens:,} output tokens{cost} · "
            f"{result.attempts} attempt(s) · {time.perf_counter() - started:.1f}s · {verdict}",
            "OK" if ch.verified else "WARN")
        self._apply_ai_config()

    def _insert_solution(self) -> None:
        ch = self.prompt_panel.challenge
        if ch is None:
            return
        self.prompt_panel.show_solution()
        self.editor_panel.load_text(ch.language, ch.reference_solution.strip() + "\n")
        self.statusBar().showMessage("Reference solution inserted (Ctrl+Z to undo)", 4_000)

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        items = self.challenges.all()[:20]
        if not items:
            self.recent_menu.addAction("No challenges yet").setEnabled(False)
        statuses = self.progress_store.statuses([c.id for c in items])
        for ch in items:
            mark = statuses[ch.id].icon
            act = self.recent_menu.addAction(f"{mark}  {ch.title}  ·  {ch.category} · {ch.difficulty}")
            act.triggered.connect(lambda _=False, c=ch: self._open_challenge(c))

    def _open_challenge(self, ch: Challenge) -> None:
        self._activate_challenge(ch)

    # ---- progress & drafts ---------------------------------------------------------------
    def _activate_challenge(self, ch: Challenge) -> None:
        """Show a challenge with its saved state: revealed hints, solution flag and your saved code."""
        self._save_draft()                                    # keep the code of the challenge we leave
        st = self.progress_store.status(ch.id)
        self.prompt_panel.show_challenge(ch, st.hints_used, st.solution_viewed)
        self._sync_checkable()
        draft = self.progress_store.get_draft(ch.id)
        code = draft[1] if draft and draft[0] == ch.language else ch.editor_starter()
        self._load_editor(ch.language, code)
        self.progress_store.mark_opened(ch.id)
        self._update_progress_badge()

    def _load_editor(self, language: str, code: str) -> None:
        self._loading_editor = True
        try:
            self.editor_panel.load_text(language, code)
        finally:
            self._loading_editor = False

    def _on_editor_changed(self) -> None:
        self._last_activity = time.monotonic()
        if not self._loading_editor and self.prompt_panel.challenge is not None:
            self._draft_timer.start()

    def _save_draft(self) -> None:
        self._draft_timer.stop()
        ch = self.prompt_panel.challenge
        if ch is None or self.editor_panel.language.value != ch.language:
            return
        code = self.editor_panel.editor.toPlainText()
        if code.strip() and code != ch.editor_starter():
            self.progress_store.save_draft(ch.id, ch.language, code)

    def _reset_code(self) -> None:
        ch = self.prompt_panel.challenge
        if ch is None:
            return
        self.progress_store.delete_draft(ch.id)
        self._load_editor(ch.language, ch.editor_starter())
        self.statusBar().showMessage("Editor reset to the starter (Ctrl+Z to undo)", 4_000)

    def _on_hint_revealed(self, n: int) -> None:
        if ch := self.prompt_panel.challenge:
            self.progress_store.mark_hints(ch.id, n)
            self._last_activity = time.monotonic()
            self._update_progress_badge()

    def _on_solution_revealed(self) -> None:
        if ch := self.prompt_panel.challenge:
            self.progress_store.mark_solution_viewed(ch.id)
            self._update_progress_badge()

    def _update_progress_badge(self) -> None:
        if ch := self.prompt_panel.challenge:
            st = self.progress_store.status(ch.id)
            checks = f" · {st.attempts} check{'s' if st.attempts != 1 else ''}" if st.attempts else ""
            self.prompt_panel.set_progress(st.icon, st.label + checks, st.state == SOLVED)

    def _count_active_time(self) -> None:
        """Every 15 s: count it as practice if a challenge is open, the window is active and you did
        something in the last 3 minutes (so leaving the app open doesn't inflate your time)."""
        ch = self.prompt_panel.challenge
        if ch is not None and self.isActiveWindow() and time.monotonic() - self._last_activity < 180:
            self.progress_store.add_time(ch.id, 15)

    def open_progress(self) -> None:
        self._save_draft()
        dlg = ProgressDialog(self.progress_store, self.challenges.all(), self)
        dlg.open_challenge.connect(lambda cid: (c := self.challenges.get(cid)) and self._activate_challenge(c))
        dlg.practise_topic.connect(self._practise_topic)
        dlg.exec()

    def _practise_topic(self, category: str, topic: str) -> None:
        panel = self.prompt_panel
        panel.category_combo.setCurrentText(category)
        panel.topic_combo.setCurrentText(topic or "Any topic")
        panel.set_status(f"Ready to practise “{topic or category}”: click New Challenge.")

    # ---- editor / run stubs ---------------------------------------------------------------
    def _insert_into_editor(self, text: str) -> None:
        editor = self.editor_panel.editor
        editor.insertPlainText(text)
        editor.setFocus()

    # ---- execution -----------------------------------------------------------------------
    def _sync_checkable(self) -> None:
        self.editor_panel.set_checkable(self.prompt_panel.challenge is not None)

    def run_code(self, language: str, code: str) -> None:
        self._start_execution(language, code, check=False)

    def check_answer(self, language: str, code: str) -> None:
        self._start_execution(language, code, check=True)

    def cancel_execution(self) -> None:
        if self._executor is not None:
            self.statusBar().showMessage("Cancelling…")
            self._executor.cancel()

    def _start_execution(self, language: str, code: str, *, check: bool) -> None:
        if self._executor is not None:
            return
        rp = self.results_panel
        if not self.db.is_connected:
            rp.show_output("Not connected to a database (Database → Connection Settings…).", error=True)
            return
        challenge = self.prompt_panel.challenge if check else None
        if check:
            if challenge is None:
                rp.show_output("Generate or open a challenge first, then Check compares your answer with it.", error=True)
                return
            if challenge.language != language:
                rp.show_output(f"This challenge expects {challenge.language}; the editor is set to {language}.",
                               error=True)
                return
        executor = Executor(self.db)
        self._executor = executor
        self._last_activity = time.monotonic()
        self._run_context = (challenge, language, code) if check else None
        rp.clear()
        self.editor_panel.set_running(True)
        self.telemetry_panel.mark_running()
        self.progress.show()
        self._run_started = time.perf_counter()
        self._live_timer.start()
        lines = code.count("\n") + 1
        self.telemetry_panel.append_log(("Checking answer" if check else "Running") + f": {language}, {lines} lines")
        bridge = self._bridge

        def job(_task):
            try:
                if language == "SQL":
                    run = executor.run_sql(code, on_columns=lambda cols, rows: bridge.columns_ready.emit(cols, rows))
                    verdict = (check_sql(self.db, code, challenge.reference_solution, challenge.order_matters)
                               if check else None)
                else:
                    run = executor.run_python(code)
                    verdict = (check_python(executor, run.dataframe, challenge.reference_solution,
                                            challenge.order_matters) if check else None)
                return run, verdict
            except (ExecutionError, ExecutionCancelled, DatabaseError) as e:
                return e

        run_task(self, job, on_result=self._on_execution_done,
                 on_error=lambda m, tb: self._on_execution_done(ExecutionError(m, tb)))

    def results_panel_columns_ready(self, columns, rows) -> None:
        if self._executor is not None:
            self.results_panel.show_results(columns, rows)

    def _update_live_telemetry(self) -> None:
        ex = self._executor
        if ex is None:
            return
        rss = ex.sampler.current_mb if ex.sampler else None
        self.telemetry_panel.show_live((time.perf_counter() - self._run_started) * 1000, ex.rows_so_far, rss)
        if self.results_panel.model.columnCount():
            self.results_panel.update_count(streaming=True)

    def _on_execution_done(self, result) -> None:
        self._live_timer.stop()
        self._executor = None
        self.editor_panel.set_running(False)
        self.progress.hide()
        rp, tp = self.results_panel, self.telemetry_panel
        elapsed = (time.perf_counter() - self._run_started) * 1000
        if isinstance(result, ExecutionCancelled):
            rp.update_count()
            rp.show_output("Cancelled.")
            tp.show_metrics(ExecutionMetrics(elapsed, rp.model.total_rows, rp.model.columnCount(), None, "Cancelled"))
            tp.append_log("Run cancelled", "WARN")
            self.statusBar().showMessage("Cancelled", 3_000)
            return
        if isinstance(result, (ExecutionError, DatabaseError)):
            details = getattr(result, "details", str(result))
            rp.show_output(details, error=True)
            tp.show_metrics(ExecutionMetrics(elapsed, 0, 0, None, "Error"))
            tp.append_log(str(result).splitlines()[0][:300], "ERROR")
            return
        run, verdict = result
        if run.columns:
            if rp.model._rows is not run.rows:
                rp.show_results(run.columns, run.rows, run.truncated)
            else:
                rp.set_truncated(run.truncated)
        if run.output:
            rp.show_output(run.output, focus=not run.columns and verdict is None)
        if run.plan:
            rp.show_plan(run.plan)
        status = "OK"
        if verdict is not None:
            rp.show_check_result(verdict)
            status = "Correct" if verdict.passed else "Not quite"
        tp.show_metrics(ExecutionMetrics(run.elapsed_ms, run.row_count, len(run.columns), run.peak_rss_mb, status,
                                         run.mem_delta_mb, run.truncated))
        mem = "" if run.peak_rss_mb is None else f" · peak {run.peak_rss_mb:,.0f} MB (+{run.mem_delta_mb or 0:,.0f})"
        tp.append_log(f"{run.language}: {run.row_count:,}{'+' if run.truncated else ''} rows in "
                      f"{run.elapsed_ms:,.0f} ms{mem}", "OK")
        if verdict is not None:
            tp.append_log(("✓ " if verdict.passed else "✗ ") + verdict.headline, "OK" if verdict.passed else "WARN")
            if self._run_context and self._run_context[0] is not None:
                ch, language, code = self._run_context
                first_solve = verdict.passed and self.progress_store.status(ch.id).state != SOLVED
                self.progress_store.record_attempt(ch.id, language, code, verdict.passed, verdict.headline,
                                                   verdict.user_rows, verdict.expected_rows, run.elapsed_ms)
                self._update_progress_badge()
                if first_solve:
                    self.statusBar().showMessage(f"Solved “{ch.title}”! See Challenge → My Progress.", 6_000)
        if run.schema_changed:
            self.refresh_schema()

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
        self._ensure_on_screen()
        for name in self._SPLITTERS:
            state = self._settings.value(f"{_LAYOUT_KEY}/{name}")
            if isinstance(state, QByteArray):
                getattr(self, name).restoreState(state)

    def _ensure_on_screen(self) -> None:
        """A geometry saved with another monitor setup can put the window off-screen: pull it back."""
        from PyQt6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        frame = self.frameGeometry()
        visible = any(s.availableGeometry().intersected(frame).width() >= 200 and
                      s.availableGeometry().intersected(frame).height() >= 100 for s in screens)
        if visible or not screens:
            return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(area.center() - self.rect().center())

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
        self._save_draft()
        if self._generator is not None:
            self._generator.cancel()
        if self._executor is not None:
            self._executor.cancel()
        self._live_timer.stop()
        wait_for_all(5_000)
        self.db.disconnect()
        super().closeEvent(event)
