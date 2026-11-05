"""Database connection settings: DuckDB (default, embedded) or PostgreSQL (server)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, \
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, \
    QWidget

from ...db import ConnectionManager, DatabaseConfig
from ..theme import C
from ..workers import run_task


class DatabaseSettingsDialog(QDialog):
    def __init__(self, config: DatabaseConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Database Settings")
        self.setMinimumWidth(560)
        self._config = config

        # ---- backend choice ------------------------------------------------------------------
        self.duck_radio = QRadioButton("DuckDB  — embedded, zero setup (recommended)")
        self.pg_radio = QRadioButton("PostgreSQL  — connect to a running server")
        group = QButtonGroup(self)
        group.addButton(self.duck_radio)
        group.addButton(self.pg_radio)

        # ---- DuckDB form ----------------------------------------------------------------------
        duck = QWidget()
        self.duck_path = QLineEdit(config.duckdb_path)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.duck_path, 1)
        path_row.addWidget(browse)
        self.duck_memory = QComboBox()
        self.duck_memory.setEditable(True)
        self.duck_memory.addItems(["", "2GB", "4GB", "8GB", "16GB"])
        self.duck_memory.setCurrentText(config.duckdb_memory_limit)
        self.duck_memory.lineEdit().setPlaceholderText("default (80% of RAM)")
        self.duck_threads = QSpinBox()
        self.duck_threads.setRange(0, 256)
        self.duck_threads.setSpecialValueText("auto (all cores)")
        self.duck_threads.setValue(config.duckdb_threads)
        duck_form = QFormLayout(duck)
        duck_form.addRow("Database file", path_row)
        duck_form.addRow("Memory limit", self.duck_memory)
        duck_form.addRow("Threads", self.duck_threads)

        # ---- Postgres form --------------------------------------------------------------------
        pg = QWidget()
        self.pg_host = QLineEdit(config.pg_host)
        self.pg_port = QSpinBox()
        self.pg_port.setRange(1, 65535)
        self.pg_port.setValue(config.pg_port)
        self.pg_db = QLineEdit(config.pg_database)
        self.pg_user = QLineEdit(config.pg_user)
        self.pg_password = QLineEdit(config.pg_password)
        self.pg_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.pg_password.setPlaceholderText("optional: falls back to PGPASSWORD / .pgpass")
        self.pg_ssl = QComboBox()
        self.pg_ssl.addItems(["prefer", "disable", "allow", "require", "verify-ca", "verify-full"])
        self.pg_ssl.setCurrentText(config.pg_sslmode)
        pg_form = QFormLayout(pg)
        pg_form.addRow("Host", self.pg_host)
        pg_form.addRow("Port", self.pg_port)
        pg_form.addRow("Database", self.pg_db)
        pg_form.addRow("User", self.pg_user)
        pg_form.addRow("Password", self.pg_password)
        pg_form.addRow("SSL mode", self.pg_ssl)
        note = QLabel("The password is kept in memory for this session only and is never written to disk.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        pg_form.addRow("", note)

        self.stack = QStackedWidget()
        self.stack.addWidget(duck)
        self.stack.addWidget(pg)
        self.duck_radio.toggled.connect(lambda on: self.stack.setCurrentIndex(0 if on else 1))
        (self.pg_radio if config.backend == "postgres" else self.duck_radio).setChecked(True)
        self.stack.setCurrentIndex(1 if config.backend == "postgres" else 0)

        # ---- test + buttons -------------------------------------------------------------------
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save && Connect")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.border};")
        layout = QVBoxLayout(self)
        layout.addWidget(self.duck_radio)
        layout.addWidget(self.pg_radio)
        layout.addWidget(line)
        layout.addWidget(self.stack)
        layout.addLayout(test_row)
        layout.addStretch()
        layout.addWidget(buttons)

    def config(self) -> DatabaseConfig:
        return dataclasses.replace(
            self._config,
            backend="postgres" if self.pg_radio.isChecked() else "duckdb",
            duckdb_path=self.duck_path.text().strip() or self._config.duckdb_path,
            duckdb_memory_limit=self.duck_memory.currentText().strip(),
            duckdb_threads=self.duck_threads.value(),
            pg_host=self.pg_host.text().strip(), pg_port=self.pg_port.value(),
            pg_database=self.pg_db.text().strip(), pg_user=self.pg_user.text().strip(),
            pg_password=self.pg_password.text(), pg_sslmode=self.pg_ssl.currentText())

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DuckDB database file", self.duck_path.text(),
                                              "DuckDB files (*.duckdb *.db);;All files (*)",
                                              options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.duck_path.setText(str(Path(path)))

    def _test(self) -> None:
        cfg = self.config()
        self.test_button.setEnabled(False)
        self.test_result.setStyleSheet(f"color: {C.text_muted};")
        self.test_result.setText("Connecting…")

        def ok(version: str) -> None:
            self.test_result.setStyleSheet(f"color: {C.success};")
            self.test_result.setText(f"✓ Connected · {version}")

        def err(message: str, _tb: str) -> None:
            self.test_result.setStyleSheet(f"color: {C.danger};")
            self.test_result.setText("✗ " + message.splitlines()[0][:300])
            self.test_result.setToolTip(message)

        run_task(self, lambda _t: ConnectionManager.test(cfg), on_result=ok, on_error=err,
                 on_finished=lambda: self.test_button.setEnabled(True))
