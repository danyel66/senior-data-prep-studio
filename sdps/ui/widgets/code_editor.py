"""Code editor with line-number gutter, current-line highlight, smart indentation and
pluggable SQL/Python highlighting, plus the `EditorPanel` that wraps it with a toolbar."""
from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QKeySequence, QPainter, QPaintEvent, QResizeEvent, \
    QShortcut, QTextCursor, QTextFormat
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, \
    QTextEdit, QVBoxLayout, QWidget

from ..theme import C, monospace_font
from .syntax_highlighter import PythonHighlighter, SqlHighlighter


class Language(str, Enum):
    SQL = "SQL"
    PYTHON = "Python"


STARTER_CODE = {
    Language.SQL: (
        "-- Senior Data Prep Studio · SQL workspace\n"
        "-- Ctrl+Enter to run (execution engine arrives in Phase 4)\n\n"
        "WITH monthly_sales AS (\n"
        "    SELECT\n"
        "        date_trunc('month', soh.order_date) AS month,\n"
        "        st.name                             AS territory,\n"
        "        SUM(soh.total_due)                  AS revenue\n"
        "    FROM sales.sales_order_header soh\n"
        "    JOIN sales.sales_territory    st USING (territory_id)\n"
        "    GROUP BY 1, 2\n"
        ")\n"
        "SELECT\n"
        "    month,\n"
        "    territory,\n"
        "    revenue,\n"
        "    revenue - LAG(revenue) OVER w                       AS mom_delta,\n"
        "    SUM(revenue) OVER (w ROWS UNBOUNDED PRECEDING)      AS running_total\n"
        "FROM monthly_sales\n"
        "WINDOW w AS (PARTITION BY territory ORDER BY month)\n"
        "ORDER BY territory, month;\n"
    ),
    Language.PYTHON: (
        "# Senior Data Prep Studio · Python workspace\n"
        "# Available when you run: pd, np, sql(query) -> DataFrame. Assign your answer to `result`.\n\n"
        "def top_customers(orders: pd.DataFrame, n: int = 10) -> pd.DataFrame:\n"
        "    \"\"\"Vectorised top-N customers by lifetime value.\"\"\"\n"
        "    return (\n"
        "        orders.astype({\"customer_id\": \"int32\", \"total_due\": \"float32\"})\n"
        "              .groupby(\"customer_id\", observed=True)[\"total_due\"]\n"
        "              .sum()\n"
        "              .nlargest(n)\n"
        "              .reset_index(name=\"ltv\")\n"
        "    )\n\n"
        "result = top_customers(sql(\"SELECT customer_id, total_due FROM sales.sales_order_header\"))\n"
    ),
}


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        self._editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit is block-based and virtualised, so it stays responsive on large scripts."""

    run_requested = pyqtSignal()
    INDENT = " " * 4

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(monospace_font(11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._highlighter = None
        self._gutter = _LineNumberArea(self)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_gutter_width()
        self._highlight_current_line()

    # ---- language -------------------------------------------------------------------------
    def set_language(self, language: Language) -> None:
        if self._highlighter is not None:
            self._highlighter.setDocument(None)
        cls = SqlHighlighter if language is Language.SQL else PythonHighlighter
        self._highlighter = cls(self.document())

    # ---- gutter ---------------------------------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event: QPaintEvent) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(C.surface))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(C.text if number == current else C.syn_comment))
                painter.drawText(0, top, self._gutter.width() - 8, height,
                                 Qt.AlignmentFlag.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    def _highlight_current_line(self) -> None:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(C.current_line))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])
        self._gutter.update()

    # ---- editing behaviour ----------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        mods = e.modifiers()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods & Qt.KeyboardModifier.ControlModifier:
            self.run_requested.emit()
            return
        if e.key() == Qt.Key.Key_Tab and not self.textCursor().hasSelection():
            self.insertPlainText(self.INDENT)
            return
        if e.key() == Qt.Key.Key_Backtab:
            self._dedent_current_line()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._newline_with_indent()
            return
        super().keyPressEvent(e)

    def _newline_with_indent(self) -> None:
        line = self.textCursor().block().text()
        indent = line[: len(line) - len(line.lstrip())]
        if line.rstrip().endswith((":", "(", "[", "{")):
            indent += self.INDENT
        self.insertPlainText("\n" + indent)

    def _dedent_current_line(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        text = cursor.block().text()
        n = min(len(self.INDENT), len(text) - len(text.lstrip(" ")))
        for _ in range(n):
            cursor.deleteChar()


class EditorPanel(QFrame):
    """Editor + header toolbar (language selector, run/cancel buttons)."""

    run_requested = pyqtSignal(str, str)     # (language, code)
    cancel_requested = pyqtSignal()
    language_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self._buffers: dict[Language, str] = dict(STARTER_CODE)

        title = QLabel("Workspace")
        title.setObjectName("PanelTitle")
        self.language_combo = QComboBox()
        self.language_combo.addItems([lang.value for lang in Language])
        self.language_combo.setToolTip("Execution language")
        self.cursor_label = QLabel("Ln 1, Col 1")
        self.cursor_label.setObjectName("Muted")
        self.cursor_label.setMinimumWidth(self.cursor_label.fontMetrics().horizontalAdvance("Ln 99999, Col 999") + 4)

        self.run_button = QPushButton("▶  Run")
        self.run_button.setObjectName("Primary")
        self.run_button.setToolTip("Execute (Ctrl+Enter)")
        self.cancel_button = QPushButton("■  Cancel")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.setEnabled(False)

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 6)
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(self.language_combo)
        header.addStretch()
        header.addWidget(self.cursor_label)
        header.addSpacing(8)
        header.addWidget(self.cancel_button)
        header.addWidget(self.run_button)

        self.editor = CodeEditor()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.editor, 1)

        self._language = Language.SQL
        self.editor.set_language(self._language)
        self.editor.setPlainText(self._buffers[self._language])

        self.language_combo.currentTextChanged.connect(self._on_language_changed)
        self.run_button.clicked.connect(self._emit_run)
        self.editor.run_requested.connect(self._emit_run)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.editor.cursorPositionChanged.connect(self._update_cursor_label)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self, activated=self._toggle_language)

    @property
    def language(self) -> Language:
        return self._language

    def code(self) -> str:
        """Selected text if any, otherwise the whole buffer (like most SQL IDEs)."""
        sel = self.editor.textCursor().selectedText()
        return (sel or self.editor.toPlainText()).replace(" ", "\n")

    def load_text(self, language: str, text: str) -> None:
        """Switch language and replace the buffer as one undoable edit (Ctrl+Z restores your code)."""
        if language != self._language.value:
            self.language_combo.setCurrentText(language)
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.language_combo.setEnabled(not running)

    def _emit_run(self) -> None:
        if self.run_button.isEnabled():
            self.run_requested.emit(self._language.value, self.code())

    def _on_language_changed(self, text: str) -> None:
        self._buffers[self._language] = self.editor.toPlainText()   # keep a buffer per language
        self._language = Language(text)
        self.editor.set_language(self._language)
        self.editor.setPlainText(self._buffers[self._language])
        self.language_changed.emit(text)

    def _toggle_language(self) -> None:
        self.language_combo.setCurrentIndex(1 - self.language_combo.currentIndex())

    def _update_cursor_label(self) -> None:
        c = self.editor.textCursor()
        self.cursor_label.setText(f"Ln {c.blockNumber() + 1}, Col {c.positionInBlock() + 1}")
