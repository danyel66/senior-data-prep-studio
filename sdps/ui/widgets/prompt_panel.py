"""AI challenge panel: pick category / difficulty / topic, generate, then read the challenge,
reveal hints one at a time, and reveal or insert the verified reference solution."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextBrowser, \
    QVBoxLayout, QWidget

from ...ai.challenge import CATEGORIES, DIFFICULTIES, TOPICS, Challenge
from ..theme import C

ANY_TOPIC = "Any topic"

WELCOME = """## Welcome to your practice studio

Choose a **category**, a **difficulty** and optionally a **topic**, then click **New Challenge**.

The AI acts as a *Senior Principal Data Architect*: it reads your database's real schema and data \
profile, writes a production-style problem, and **verifies its own reference solution against your \
database** before showing it to you.

- **Hints** are revealed one at a time.
- **Show Solution** reveals the reference answer, its explanation and interview follow-ups.
- Your challenges are saved in **Challenge → Recent Challenges**.
"""


class PromptPanel(QFrame):
    generate_requested = pyqtSignal(str, str, str)   # (category, difficulty, topic or "")
    cancel_requested = pyqtSignal()
    insert_solution_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.challenge: Challenge | None = None
        self.hints_revealed = 0
        self.solution_shown = False
        self._busy = False

        title = QLabel("AI Challenge")
        title.setObjectName("PanelTitle")
        self.badge = QLabel("Senior Principal Data Architect")
        self.badge.setObjectName("Badge")
        self.generate_button = QPushButton("✦  New Challenge")
        self.generate_button.setObjectName("Primary")
        self.generate_button.setToolTip("Generate a new challenge (Ctrl+G)")
        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 4)
        header.addWidget(title)
        header.addWidget(self.badge)
        header.addStretch()
        header.addWidget(self.generate_button)

        self.category_combo = QComboBox()
        self.category_combo.addItems(CATEGORIES)
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(DIFFICULTIES)
        self.topic_combo = QComboBox()
        self.topic_combo.setEditable(True)
        self.topic_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.topic_combo.setToolTip("Pick a focus area or type your own")
        self.topic_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.category_combo.currentTextChanged.connect(self._fill_topics)
        self._fill_topics(self.category_combo.currentText())
        pickers = QHBoxLayout()
        pickers.setContentsMargins(12, 0, 12, 6)
        pickers.addWidget(self.category_combo)
        pickers.addWidget(self.difficulty_combo)
        pickers.addWidget(self.topic_combo, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.linkActivated.connect(lambda _: self.settings_requested.emit())
        self.status.setContentsMargins(14, 6, 14, 6)
        self.status.hide()

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.document().setDocumentMargin(14)

        self.hint_button = QPushButton("💡  Hint")
        self.hint_button.setToolTip("Reveal the next hint (Ctrl+Shift+H)")
        self.hint_button.clicked.connect(self.next_hint)
        self.solution_button = QPushButton("Show Solution")
        self.solution_button.clicked.connect(self.show_solution)
        self.insert_button = QPushButton("Insert Solution")
        self.insert_button.setToolTip("Put the reference solution in the editor (undo with Ctrl+Z)")
        self.insert_button.clicked.connect(self.insert_solution_requested)
        self.meta = QLabel()
        self.meta.setObjectName("Muted")
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 6, 12, 8)
        footer.addWidget(self.hint_button)
        footer.addWidget(self.solution_button)
        footer.addWidget(self.insert_button)
        footer.addStretch()
        footer.addWidget(self.meta)
        self.footer = QWidget()
        self.footer.setLayout(footer)
        self.footer.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(pickers)
        layout.addWidget(self.status)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.footer)

        self.generate_button.clicked.connect(self._on_generate_clicked)
        self.browser.setMarkdown(WELCOME)

    # ---- public API -----------------------------------------------------------------------
    def selection(self) -> tuple[str, str, str]:
        topic = self.topic_combo.currentText().strip()
        return (self.category_combo.currentText(), self.difficulty_combo.currentText(),
                "" if topic == ANY_TOPIC else topic)

    def set_provider_label(self, text: str) -> None:
        self.badge.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.generate_button.setText("■  Cancel" if busy else "✦  New Challenge")
        self.generate_button.setObjectName("Danger" if busy else "Primary")
        self.generate_button.style().unpolish(self.generate_button)
        self.generate_button.style().polish(self.generate_button)
        for w in (self.category_combo, self.difficulty_combo, self.topic_combo):
            w.setEnabled(not busy)
        if busy:
            self.set_status("Starting…")

    def set_status(self, text: str, color: str | None = None) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;")
        prefix = "⏳  " if self._busy else ""
        self.status.setText(f'<span style="color:{color or C.text_muted}">{prefix}{safe}</span>')
        self.status.show()

    def show_error(self, message: str, settings_hint: bool = False) -> None:
        safe = message.replace("&", "&amp;").replace("<", "&lt;")
        link = f' &nbsp;<a href="settings" style="color:{C.accent}">Open AI Settings…</a>' if settings_hint else ""
        self.status.setText(f'<span style="color:{C.danger}">✗  {safe}</span>{link}')
        self.status.show()

    def clear_status(self) -> None:
        self.status.hide()

    def show_challenge(self, challenge: Challenge) -> None:
        self.challenge = challenge
        self.hints_revealed = 0
        self.solution_shown = False
        self.clear_status()
        for combo, value in ((self.category_combo, challenge.category), (self.difficulty_combo, challenge.difficulty)):
            i = combo.findText(value)
            if i >= 0:
                combo.blockSignals(combo is not self.category_combo)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)
        self._render()
        self.footer.show()
        if not challenge.verified and challenge.verification_notes:
            notes = " · ".join(challenge.verification_notes)
            safe = notes.replace("&", "&amp;").replace("<", "&lt;")
            self.status.setText(f'<span style="color:{C.warning}">⚠  The reference solution could not be '
                                f'verified against your database: {safe}</span>')
            self.status.show()

    def next_hint(self) -> None:
        if self.challenge and self.hints_revealed < len(self.challenge.hints):
            self.hints_revealed += 1
            self._render(scroll_to_end=True)

    def show_solution(self) -> None:
        if self.challenge and not self.solution_shown:
            self.solution_shown = True
            self.hints_revealed = len(self.challenge.hints)
            self._render(scroll_to_end=True)

    # ---- internals ------------------------------------------------------------------------
    def _on_generate_clicked(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        else:
            self.generate_requested.emit(*self.selection())

    def _fill_topics(self, category: str) -> None:
        current = self.topic_combo.currentText()
        self.topic_combo.clear()
        self.topic_combo.addItem(ANY_TOPIC)
        self.topic_combo.addItems(TOPICS.get(category, []))
        if current and current != ANY_TOPIC and current in TOPICS.get(category, []):
            self.topic_combo.setCurrentText(current)

    def _render(self, scroll_to_end: bool = False) -> None:
        ch = self.challenge
        if ch is None:
            return
        self.browser.setMarkdown(ch.to_markdown(self.hints_revealed, self.solution_shown))
        if scroll_to_end:
            sb = self.browser.verticalScrollBar()
            sb.setValue(sb.maximum())
        total = len(ch.hints)
        self.hint_button.setText(f"💡  Hint {self.hints_revealed}/{total}")
        self.hint_button.setEnabled(self.hints_revealed < total and not self.solution_shown)
        self.solution_button.setEnabled(not self.solution_shown)
        badge = (f'<span style="color:{C.success}">✓ verified on your database</span>' if ch.verified else
                 f'<span style="color:{C.warning}">⚠ solution not verified</span>')
        self.meta.setToolTip("" if ch.verified else "\n".join(ch.verification_notes))
        cost = "" if ch.cost_usd is None else (" · free (local)" if ch.cost_usd == 0 else f" · ${ch.cost_usd:.3f}")
        self.meta.setText(f"{badge} · {ch.model}{cost}")
