"""AI provider settings: Claude (Anthropic API, key in the OS keychain) or Ollama (local)."""
from __future__ import annotations

import dataclasses

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, \
    QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget

from ...ai.config import CLAUDE_MODELS, EFFORT_LEVELS, RECOMMENDED_OLLAMA_MODELS, AIConfig, \
    claude_credentials_hint, get_api_key, keyring_available, set_api_key
from ...ai.providers import AIError, OllamaProvider
from ..theme import C
from ..workers import run_task

# Typical challenge: ~5K cached input tokens + 3K-9K output tokens (thinking + JSON), before repairs.
_OUT_RANGE = (3_000, 9_000)


def _cost_range(model: str) -> str:
    _, p_in, p_out, _ = CLAUDE_MODELS[model]
    lo = (5_000 * p_in * 0.1 + _OUT_RANGE[0] * p_out) / 1e6
    hi = (5_000 * p_in * 1.25 + _OUT_RANGE[1] * p_out) / 1e6
    fmt = (lambda v: f"${v:.3f}") if hi < 0.05 else (lambda v: f"${v:.2f}")
    return f"≈ {fmt(lo)}–{fmt(hi)} per challenge"


class AISettingsDialog(QDialog):
    def __init__(self, config: AIConfig, parent: QWidget | None = None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("AI Settings")
        self.setMinimumWidth(600)
        self._config = config
        self._remove_key = False

        intro = QLabel(message) if message else None
        if intro:
            intro.setWordWrap(True)
            intro.setStyleSheet(f"color: {C.warning};")

        self.claude_radio = QRadioButton("Claude  — Anthropic API (best quality, paid per use)")
        self.ollama_radio = QRadioButton("Ollama  — local model on this computer (free, offline)")
        group = QButtonGroup(self)
        group.addButton(self.claude_radio)
        group.addButton(self.ollama_radio)

        # ---- Claude page --------------------------------------------------------------------
        claude = QWidget()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-ant-…")
        self.key_state = QLabel()
        self.key_state.setObjectName("Muted")
        self.key_state.setWordWrap(True)
        self.remove_key_button = QPushButton("Remove saved key")
        self.remove_key_button.clicked.connect(self._remove_saved_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.remove_key_button)
        self.model_combo = QComboBox()
        for model_id, (label, *_r) in CLAUDE_MODELS.items():
            self.model_combo.addItem(label, model_id)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(config.claude_model)))
        self.cost_label = QLabel()
        self.cost_label.setObjectName("Muted")
        self.model_combo.currentIndexChanged.connect(self._update_cost)
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(EFFORT_LEVELS)
        self.effort_combo.setCurrentText(config.claude_effort)
        self.effort_combo.setToolTip("How hard Claude thinks. Higher = more careful challenges, more tokens.")
        link = QLabel('Get an API key at <a href="https://console.anthropic.com/settings/keys" '
                      f'style="color:{C.accent}">console.anthropic.com</a>')
        link.setOpenExternalLinks(True)
        form = QFormLayout(claude)
        form.addRow("API key", key_row)
        form.addRow("", self.key_state)
        form.addRow("Model", self.model_combo)
        form.addRow("", self.cost_label)
        form.addRow("Effort", self.effort_combo)
        form.addRow("", link)

        # ---- Ollama page --------------------------------------------------------------------
        ollama = QWidget()
        self.url_edit = QLineEdit(config.ollama_url)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setCurrentText(config.ollama_model)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_ollama_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.ollama_model, 1)
        model_row.addWidget(refresh)
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(4096, 131072)
        self.ctx_spin.setSingleStep(4096)
        self.ctx_spin.setValue(config.ollama_num_ctx)
        self.ctx_spin.setSuffix(" tokens")
        self.unload_check = QCheckBox("Free the model's memory after each challenge (recommended on 8–16 GB Macs)")
        self.unload_check.setChecked(config.ollama_unload_after)
        self.unload_check.setToolTip("A 7B model holds about 5 GB of RAM while loaded. Freeing it keeps your Mac "
                                     "responsive; the next challenge takes 5–15 s longer to start.")
        tip = QLabel(f"Recommended: <b>{RECOMMENDED_OLLAMA_MODELS[0]}</b> (needs ~10 GB RAM) or "
                     f"<b>{RECOMMENDED_OLLAMA_MODELS[1]}</b> for smaller machines. Install with "
                     f"<code>ollama pull {RECOMMENDED_OLLAMA_MODELS[0]}</code>. The schema prompt needs a "
                     "context of at least 12K tokens.")
        tip.setWordWrap(True)
        tip.setObjectName("Muted")
        oform = QFormLayout(ollama)
        oform.addRow("Server URL", self.url_edit)
        oform.addRow("Model", model_row)
        oform.addRow("Context size", self.ctx_spin)
        oform.addRow("", self.unload_check)
        oform.addRow("", tip)

        self.stack = QStackedWidget()
        self.stack.addWidget(claude)
        self.stack.addWidget(ollama)
        self.claude_radio.toggled.connect(lambda on: self.stack.setCurrentIndex(0 if on else 1))
        (self.ollama_radio if config.provider == "ollama" else self.claude_radio).setChecked(True)
        self.stack.setCurrentIndex(1 if config.provider == "ollama" else 0)

        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.border};")
        layout = QVBoxLayout(self)
        if intro:
            layout.addWidget(intro)
        layout.addWidget(self.claude_radio)
        layout.addWidget(self.ollama_radio)
        layout.addWidget(line)
        layout.addWidget(self.stack)
        layout.addLayout(test_row)
        layout.addStretch()
        layout.addWidget(buttons)

        self._update_key_state()
        self._update_cost()
        if config.provider == "ollama":
            self._refresh_ollama_models(quiet=True)

    # ---- results --------------------------------------------------------------------------
    def config(self) -> AIConfig:
        return dataclasses.replace(
            self._config,
            provider="ollama" if self.ollama_radio.isChecked() else "claude",
            claude_model=self.model_combo.currentData(), claude_effort=self.effort_combo.currentText(),
            ollama_url=self.url_edit.text().strip() or "http://localhost:11434",
            ollama_model=self.ollama_model.currentText().strip(), ollama_num_ctx=self.ctx_spin.value(),
            ollama_unload_after=self.unload_check.isChecked())

    def _save(self) -> None:
        typed = self.key_edit.text().strip()
        if typed:
            set_api_key(typed)
        elif self._remove_key:
            set_api_key(None)
        self.config().save()
        self.accept()

    # ---- helpers --------------------------------------------------------------------------
    def _update_key_state(self) -> None:
        where = ("your system keychain (macOS Keychain on a Mac)" if keyring_available()
                 else "memory for this session only (no system keychain found)")
        if get_api_key() and not self._remove_key:
            self.key_state.setText(f"A key is saved in {where}. Leave the field empty to keep it.")
            self.remove_key_button.show()
        else:
            hint = claude_credentials_hint()
            extra = f" Currently using: {hint}." if hint and hint != "saved API key" else ""
            self.key_state.setText(f"Your key is stored in {where}, never in a file.{extra}")
            self.remove_key_button.hide()

    def _remove_saved_key(self) -> None:
        self._remove_key = True
        self._update_key_state()

    def _update_cost(self) -> None:
        self.cost_label.setText(_cost_range(self.model_combo.currentData()))

    def _set_result(self, ok: bool, text: str) -> None:
        self.test_result.setStyleSheet(f"color: {C.success if ok else C.danger};")
        self.test_result.setText(("✓ " if ok else "✗ ") + text)

    def _refresh_ollama_models(self, quiet: bool = False) -> None:
        provider = OllamaProvider(self.config())

        def done(models: list[str]) -> None:
            current = self.ollama_model.currentText()
            self.ollama_model.clear()
            self.ollama_model.addItems(models)
            if current:
                self.ollama_model.setCurrentText(current)
            elif models:
                pref = next((m for m in RECOMMENDED_OLLAMA_MODELS if m in models), models[0])
                self.ollama_model.setCurrentText(pref)
            if not quiet:
                self._set_result(bool(models), f"{len(models)} model(s) installed" if models else
                                 f"Ollama is running but has no models. Run: ollama pull {RECOMMENDED_OLLAMA_MODELS[0]}")

        run_task(self, lambda _t: provider.list_models(), on_result=done,
                 on_error=lambda m, _tb: None if quiet else self._set_result(False, m))

    def _test(self) -> None:
        cfg = self.config()
        self.test_button.setEnabled(False)
        self.test_result.setStyleSheet(f"color: {C.text_muted};")
        self.test_result.setText("Testing…")
        if cfg.provider == "ollama":
            def job(_t):
                models = OllamaProvider(cfg).list_models()
                if cfg.ollama_model and cfg.ollama_model not in models:
                    raise AIError(f"Ollama is running, but '{cfg.ollama_model}' isn't installed. "
                                  f"Run: ollama pull {cfg.ollama_model}")
                return f"Ollama is running · {len(models)} model(s) installed"
        else:
            key = self.key_edit.text().strip() or (None if self._remove_key else get_api_key())

            def job(_t):
                import anthropic
                try:
                    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
                    info = client.models.retrieve(cfg.claude_model)      # free call that checks the key
                except anthropic.AuthenticationError as e:
                    raise AIError("The API key was rejected.") from e
                except anthropic.NotFoundError as e:
                    raise AIError(f"{cfg.claude_model} isn't available to this key.") from e
                except anthropic.APIConnectionError as e:
                    raise AIError("Couldn't reach api.anthropic.com. Check your connection.") from e
                except anthropic.AnthropicError as e:
                    raise AIError(str(e).splitlines()[0] if str(e) else "No API key found.") from e
                return f"Connected · {getattr(info, 'display_name', cfg.claude_model)} is available"

        run_task(self, job, on_result=lambda text: self._set_result(True, text),
                 on_error=lambda m, _tb: self._set_result(False, m),
                 on_finished=lambda: self.test_button.setEnabled(True))
