"""AI challenge display. Phase 3 plugs the LLM generator into `generate_requested`."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, \
    QVBoxLayout, QWidget

CATEGORIES = ["Advanced SQL", "Python / Pandas", "Analytics Engineering / dbt"]
DIFFICULTIES = ["Senior", "Staff", "Principal"]

PLACEHOLDER_PROMPT = """
<h3 style="margin:0 0 6px 0;">Territory revenue momentum with skew-aware windows</h3>
<p style="color:#8a93a6;margin:0 0 10px 0;">Advanced SQL · Senior · ~25 min</p>
<p>The finance team reports that the <code>sales.sales_order_header</code> table (≈ 30M rows) is
heavily skewed: two territories account for 70% of orders. Produce a monthly revenue report per
territory that includes:</p>
<ol>
  <li>Month-over-month revenue delta and a 3-month moving average.</li>
  <li>A running total that resets at each fiscal year (fiscal year starts July 1).</li>
  <li>The rank of each territory within the month, with ties handled deterministically.</li>
</ol>
<p><b>Follow-up:</b> explain which index (or DuckDB zone-map/ordering strategy) you'd add, and show the
<code>EXPLAIN ANALYZE</code> output before and after.</p>
<p style="color:#8a93a6;"><i>Placeholder challenge · the AI generator is wired up in Phase 3.</i></p>
"""


class PromptPanel(QFrame):
    generate_requested = pyqtSignal(str, str)   # (category, difficulty)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")

        title = QLabel("AI Challenge")
        title.setObjectName("PanelTitle")
        self.badge = QLabel("Senior Principal Data Architect")
        self.badge.setObjectName("Badge")
        self.category_combo = QComboBox()
        self.category_combo.addItems(CATEGORIES)
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(DIFFICULTIES)
        self.generate_button = QPushButton("✦  New Challenge")
        self.generate_button.setObjectName("Primary")

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 6)
        header.addWidget(title)
        header.addWidget(self.badge)
        header.addStretch()
        header.addWidget(self.category_combo)
        header.addWidget(self.difficulty_combo)
        header.addWidget(self.generate_button)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.document().setDocumentMargin(14)
        self.browser.document().setDefaultStyleSheet(
            "code { background-color:#1c212c; color:#ffcb6b; } li { margin-bottom:4px; }")
        self.browser.setHtml(PLACEHOLDER_PROMPT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.browser, 1)

        self.generate_button.clicked.connect(
            lambda: self.generate_requested.emit(self.category_combo.currentText(),
                                                 self.difficulty_combo.currentText()))

    def set_prompt_html(self, html: str) -> None:
        self.browser.setHtml(html)

    def set_prompt_markdown(self, markdown: str) -> None:
        self.browser.setMarkdown(markdown)

    def set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.generate_button.setText("Generating…" if busy else "✦  New Challenge")
