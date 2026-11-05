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
from PyQt6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, \
    QStackedLayout, QTableView, QVBoxLayout, QWidget

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

        self.empty_label = QLabel("Run a query to see results here.\n\nCtrl+Enter to execute · "
                                  "View → Load Demo Dataset to preview the grid")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._stack = QStackedLayout()
        self._stack.addWidget(self.empty_label)
        self._stack.addWidget(self.table)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(self._stack, 1)

    def show_results(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        self.model.set_data(columns, rows)
        self.table.scrollToTop()
        n = len(rows)
        self.row_badge.setText(f"{n:,} row{'s' if n != 1 else ''}")
        self.shape_label.setText(f"{len(columns)} columns · virtualised")
        self._stack.setCurrentWidget(self.table if columns else self.empty_label)

    def clear(self) -> None:
        self.show_results([], ())
