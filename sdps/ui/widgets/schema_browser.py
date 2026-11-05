"""Schema browser: schemas → tables (row counts) → columns (types), with filter and quick inserts."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QStackedLayout, \
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ...db import TableInfo
from ..theme import C

ROLE_INSERT = Qt.ItemDataRole.UserRole          # text inserted on double-click
ROLE_TABLE = Qt.ItemDataRole.UserRole + 1       # qualified table name (table items only)


def _compact(n: int | None) -> str:
    if n is None:
        return ""
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}".replace(".0", "")
    return str(n)


class SchemaBrowser(QFrame):
    insert_requested = pyqtSignal(str)        # text to insert at the editor cursor
    refresh_requested = pyqtSignal()
    seed_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")

        title = QLabel("Schema")
        title.setObjectName("PanelTitle")
        self.count_badge = QLabel("")
        self.count_badge.setObjectName("Badge")
        self.count_badge.hide()
        self.refresh_button = QToolButton()
        self.refresh_button.setText("⟳")
        self.refresh_button.setToolTip("Refresh schema (F6)")
        self.refresh_button.clicked.connect(self.refresh_requested)
        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 8, 6)
        header.addWidget(title)
        header.addWidget(self.count_badge)
        header.addStretch()
        header.addWidget(self.refresh_button)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter tables / columns…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(8, 0, 8, 6)
        filter_row.addWidget(self.filter_edit)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setStyleSheet(f"QTreeWidget {{ background: {C.surface}; border: none; }}"
                                f"QTreeWidget::item {{ padding: 2px 0; }}"
                                f"QTreeWidget::item:selected {{ background: {C.selection}; }}")

        self.message = QLabel()
        self.message.setObjectName("Muted")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.linkActivated.connect(lambda _: self.seed_requested.emit())
        self.message.setContentsMargins(16, 16, 16, 16)

        self._stack = QStackedLayout()
        self._stack.addWidget(self.message)
        self._stack.addWidget(self.tree)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(filter_row)
        layout.addLayout(self._stack, 1)
        self.show_message("Connecting…")

    # ---- public API -----------------------------------------------------------------------
    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self._stack.setCurrentWidget(self.message)
        self.count_badge.hide()

    def show_empty_database(self) -> None:
        self.show_message('This database has no tables yet.<br><br>'
                          f'<a href="seed" style="color:{C.accent}">Generate AdventureWorks data…</a>')

    def set_tables(self, tables: list[TableInfo]) -> None:
        if not tables:
            self.show_empty_database()
            return
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)
        muted = QBrush(QColor(C.text_muted))
        schemas: dict[str, QTreeWidgetItem] = {}
        for t in tables:
            if t.schema not in schemas:
                item = QTreeWidgetItem([t.schema, ""])
                item.setFont(0, bold)
                item.setData(0, ROLE_INSERT, t.schema)
                schemas[t.schema] = item
                self.tree.addTopLevelItem(item)
            label = f"{t.name}  (view)" if t.kind == "view" else t.name
            titem = QTreeWidgetItem([label, _compact(t.row_count)])
            titem.setForeground(1, muted)
            titem.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            titem.setData(0, ROLE_INSERT, t.qualified_name)
            titem.setData(0, ROLE_TABLE, t.qualified_name)
            tip = f"<b>{t.qualified_name}</b>"
            if t.row_count is not None:
                tip += f"<br>{t.row_count:,} rows"
            if t.comment:
                tip += f"<br><br>{t.comment}"
            titem.setToolTip(0, tip)
            for col in t.columns:
                citem = QTreeWidgetItem([col.name, col.data_type])
                citem.setForeground(1, muted)
                citem.setData(0, ROLE_INSERT, col.name)
                citem.setToolTip(0, f"{col.name} {col.data_type}{'' if col.nullable else ' NOT NULL'}")
                titem.addChild(citem)
            schemas[t.schema].addChild(titem)
        for item in schemas.values():
            item.setExpanded(True)
        self.count_badge.setText(f"{len(tables)} tables")
        self.count_badge.show()
        self._stack.setCurrentWidget(self.tree)
        self._apply_filter(self.filter_edit.text())

    # ---- internals ------------------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            schema = self.tree.topLevelItem(i)
            any_table = False
            for j in range(schema.childCount()):
                table = schema.child(j)
                table_hit = not needle or needle in table.text(0).lower()
                col_hit = False
                for k in range(table.childCount()):
                    col = table.child(k)
                    hit = bool(needle) and needle in col.text(0).lower()
                    col.setHidden(bool(needle) and not hit and not table_hit)
                    col_hit |= hit
                visible = table_hit or col_hit
                table.setHidden(not visible)
                if needle and col_hit and not table_hit:
                    table.setExpanded(True)
                any_table |= visible
            schema.setHidden(not any_table)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        text = item.data(0, ROLE_INSERT)
        if text:
            self.insert_requested.emit(text)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        name = item.data(0, ROLE_INSERT)
        table = item.data(0, ROLE_TABLE)
        if table:
            cols = ", ".join(item.child(k).text(0) for k in range(item.childCount()))
            menu.addAction(QAction("Insert SELECT * … LIMIT 100", menu, triggered=lambda: self.insert_requested.emit(
                f"SELECT *\nFROM {table}\nLIMIT 100;\n")))
            menu.addAction(QAction("Insert SELECT with all columns", menu, triggered=lambda: self.insert_requested.emit(
                f"SELECT {cols}\nFROM {table};\n")))
            menu.addAction(QAction("Insert row count query", menu, triggered=lambda: self.insert_requested.emit(
                f"SELECT count(*) FROM {table};\n")))
            menu.addSeparator()
        menu.addAction(QAction(f"Insert “{name}”", menu, triggered=lambda: self.insert_requested.emit(name)))
        menu.addAction(QAction("Copy name", menu, triggered=lambda: QGuiApplication.clipboard().setText(name)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
