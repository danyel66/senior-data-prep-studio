"""Virtualised results grid.

`ResultsTableModel` never materialises Qt items. It reads cells on demand from any
`Sequence` of row tuples and exposes rows in batches via `canFetchMore`/`fetchMore`,
so a 10M-row result costs the view nothing until the user scrolls. Phase 4 will feed it
Arrow/pandas-backed row sequences produced off the UI thread.
"""
from __future__ import annotations

import datetime as dt
import random
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, \
    QStackedLayout, QTabWidget, QTableView, QTextBrowser, QVBoxLayout, QWidget

from ..theme import C, monospace_font

_ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


class ResultsTableModel(QAbstractTableModel):
    FETCH_BATCH = 2_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns: list[str] = []
        self._rows: Sequence[Sequence[Any]] = ()
        self._loaded = 0           # rows currently exposed to the view

    # ---- public API -----------------------------------------------------------------------
    def set_data(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        self.beginResetModel()
        self._columns = list(columns)
        self._rows = rows
        self._loaded = min(len(rows), self.FETCH_BATCH)
        self.endResetModel()

    def clear(self) -> None:
        self.set_data([], ())

    def sync(self) -> None:
        """Expose rows appended to the underlying (growing) list since the last call."""
        if self._loaded < min(len(self._rows), self.FETCH_BATCH):
            self.fetchMore()

    @property
    def total_rows(self) -> int:
        return len(self._rows)

    # ---- Qt model interface ---------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._loaded

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802
        return not parent.isValid() and self._loaded < len(self._rows)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:  # noqa: N802
        if parent.isValid():
            return
        n = min(self.FETCH_BATCH, len(self._rows) - self._loaded)
        if n <= 0:
            return
        self.beginInsertRows(QModelIndex(), self._loaded, self._loaded + n - 1)
        self._loaded += n
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self._rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return _format(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) \
                else _ALIGN_LEFT
        if role == Qt.ItemDataRole.ForegroundRole and value is None:
            return QColor(C.text_muted)
        if role == Qt.ItemDataRole.ToolTipRole:
            return None if value is None else str(value)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._columns[section] if section < len(self._columns) else None
        return section + 1


def _format(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, Decimal):
        return f"{value:,}"
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


class SyntheticRows(Sequence):
    """Deterministic, zero-storage row generator for demoing the virtualised grid at scale."""

    COLUMNS = ["sales_order_id", "order_date", "territory", "customer_id", "sub_total", "tax_amt",
               "total_due", "online_order"]
    _TERRITORIES = ["Northwest", "Northeast", "Central", "Southwest", "Southeast", "Canada",
                    "France", "Germany", "Australia", "United Kingdom"]
    _EPOCH = dt.date(2022, 1, 1)

    def __init__(self, n: int):
        self._n = n

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, i):  # type: ignore[override]
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(self._n))]
        if not 0 <= i < self._n:
            raise IndexError(i)
        rng = random.Random(i)
        sub_total = round(rng.lognormvariate(6, 1.2), 2)
        tax = round(sub_total * 0.08, 2)
        return (
            43_659 + i,
            self._EPOCH + dt.timedelta(days=i * 1_461 // self._n),
            self._TERRITORIES[min(int(rng.paretovariate(1.2)) - 1, 9)],   # deliberately skewed
            11_000 + rng.randrange(19_000),
            sub_total,
            tax,
            round(sub_total + tax + 0.025 * sub_total, 2),
            None if i % 97 == 0 else rng.random() < 0.6,
        )


class ResultsPanel(QFrame):
    """Tabs: Results (virtualised grid) · Output (messages / prints / errors) · Plan · Check."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")

        title = QLabel("Results")
        title.setObjectName("PanelTitle")
        self.row_badge = QLabel("0 rows")
        self.row_badge.setObjectName("Badge")
        self.shape_label = QLabel("")
        self.shape_label.setObjectName("Muted")

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 6)
        header.addWidget(title)
        header.addWidget(self.row_badge)
        header.addStretch()
        header.addWidget(self.shape_label)

        self.model = ResultsTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setFont(monospace_font(10))
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)   # never ResizeToContents: O(rows)
        hh.setDefaultSectionSize(130)
        hh.setStretchLastSection(True)
        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)        # uniform rows = O(1) layout
        vh.setDefaultSectionSize(24)

        self.empty_label = QLabel("Run a query to see results here.\n\nCtrl+Enter runs your code · "
                                  "Ctrl+Shift+Enter checks your answer")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid_page = QWidget()
        self._stack = QStackedLayout(grid_page)
        self._stack.addWidget(self.empty_label)
        self._stack.addWidget(self.table)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(monospace_font(10))
        self.plan = QPlainTextEdit()
        self.plan.setReadOnly(True)
        self.plan.setFont(monospace_font(10))
        self.plan.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.check = QTextBrowser()
        self.check.document().setDocumentMargin(14)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(grid_page, "Results")
        self.tabs.addTab(self.output, "Output")
        self.tabs.addTab(self.plan, "Plan")
        self.tabs.addTab(self.check, "Check")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        self._truncated = False

    # ---- grid ---------------------------------------------------------------------------------
    def show_results(self, columns: Sequence[str], rows: Sequence[Sequence[Any]], truncated: bool = False) -> None:
        self.model.set_data(columns, rows)
        self.table.scrollToTop()
        self._truncated = truncated
        self.update_count()
        self._stack.setCurrentWidget(self.table if columns else self.empty_label)
        if columns:
            self.tabs.setCurrentIndex(0)

    def update_count(self, streaming: bool = False) -> None:
        self.model.sync()
        n = self.model.total_rows
        more = "+" if self._truncated else ("…" if streaming else "")
        self.row_badge.setText(f"{n:,}{more} row{'s' if n != 1 else ''}")
        cols = self.model.columnCount()
        note = " · display limited to first 100,000 rows" if self._truncated else ""
        self.shape_label.setText(f"{cols} columns{note}" if cols else "")

    def set_truncated(self, truncated: bool) -> None:
        self._truncated = truncated
        self.update_count()

    def clear(self) -> None:
        self.show_results([], ())
        self.output.clear()
        self.plan.clear()
        self.check.clear()

    # ---- other tabs ---------------------------------------------------------------------------
    def show_output(self, text: str, error: bool = False, focus: bool = False) -> None:
        if error:
            safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self.output.appendHtml(f'<pre style="color:{C.danger}; margin:0">{safe}</pre>')
        else:
            self.output.appendPlainText(text)
        if focus or error:
            self.tabs.setCurrentWidget(self.output)

    def show_plan(self, text: str) -> None:
        self.plan.setPlainText(text)
        self.tabs.setCurrentWidget(self.plan)

    def show_check(self, html: str) -> None:
        self.check.setHtml(html)
        self.tabs.setCurrentWidget(self.check)

    def show_check_result(self, res) -> None:
        """Render a sdps.execution.CheckResult in the Check tab."""
        self.show_check(render_check_html(res))


def _esc(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _sample_table(title: str, columns: list[str], rows: list[tuple], color: str) -> str:
    if not rows:
        return ""
    cols = columns if columns and len(columns) == len(rows[0]) else [f"col {i + 1}" for i in range(len(rows[0]))]
    head = "".join(f'<th align="left" style="padding:3px 10px;color:{C.text_muted}">{_esc(c)}</th>' for c in cols)
    body = "".join("<tr>" + "".join(f'<td style="padding:2px 10px;font-family:monospace">{_esc(_format(v))}</td>'
                                    for v in row) + "</tr>" for row in rows)
    return (f'<p style="color:{color};margin:14px 0 4px 0"><b>{_esc(title)}</b></p>'
            f'<table cellspacing="0" style="border:1px solid {C.border}">'
            f'<tr style="background:{C.surface_alt}">{head}</tr>{body}</table>')


def render_check_html(res) -> str:
    color = C.success if res.passed else C.warning
    icon = "✓" if res.passed else "✗"
    parts = [f'<p style="font-size:15pt;color:{color};margin:0 0 6px 0"><b>{icon} {_esc(res.headline)}</b></p>']
    if res.expected_rows or res.user_rows:
        parts.append(f'<p style="color:{C.text_muted};margin:0 0 8px 0">Your rows: {res.user_rows:,} · '
                     f'expected: {res.expected_rows:,}'
                     + (f" · checked in {res.elapsed_ms / 1000:.1f}s" if res.elapsed_ms else "") + "</p>")
    if res.details:
        parts.append("<ul style='margin-top:4px'>" + "".join(f"<li>{_esc(d)}</li>" for d in res.details) + "</ul>")
    parts.append(_sample_table("Expected rows missing from your answer (sample)", res.columns, res.missing, C.warning))
    parts.append(_sample_table("Rows in your answer that shouldn't be there (sample)", res.columns, res.unexpected,
                               C.danger))
    if not res.passed and not res.missing and not res.unexpected and not res.details:
        parts.append(f"<p style='color:{C.text_muted}'>Compare your output with the task requirements.</p>")
    if not res.passed:
        parts.append(f"<p style='color:{C.text_muted};margin-top:12px'>Numbers are compared at 4 decimal places. "
                     "Use Hint or Show Solution in the challenge panel if you're stuck.</p>")
    return "".join(parts)
