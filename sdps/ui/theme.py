"""Modern dark theme: design tokens, a Fusion QPalette, and a global stylesheet.

All colours live in `Colors` so widgets (e.g. the syntax highlighter) can share them.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    bg: str = "#0f1117"           # window background
    surface: str = "#161a23"      # panels
    surface_alt: str = "#1c212c"  # inputs, alternating rows
    border: str = "#2a3040"
    text: str = "#d7dce5"
    text_muted: str = "#8a93a6"
    accent: str = "#4f8cff"
    accent_hover: str = "#6aa0ff"
    success: str = "#3ecf8e"
    warning: str = "#f5a524"
    danger: str = "#f2555a"
    selection: str = "#28406e"
    current_line: str = "#1a1f2b"

    # Syntax colours
    syn_keyword: str = "#c792ea"
    syn_function: str = "#82aaff"
    syn_string: str = "#c3e88d"
    syn_number: str = "#f78c6c"
    syn_comment: str = "#5c6680"
    syn_operator: str = "#89ddff"
    syn_type: str = "#ffcb6b"
    syn_decorator: str = "#ff9cac"


C = Colors()


def monospace_font(point_size: int = 11) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    for family in ("JetBrains Mono", "Cascadia Code", "Fira Code", "Consolas", "Menlo"):
        if family in QFontDatabase.families():
            font = QFont(family)
            break
    font.setPointSize(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _palette() -> QPalette:
    p = QPalette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    p.setColor(role.Window, QColor(C.bg))
    p.setColor(role.WindowText, QColor(C.text))
    p.setColor(role.Base, QColor(C.surface))
    p.setColor(role.AlternateBase, QColor(C.surface_alt))
    p.setColor(role.ToolTipBase, QColor(C.surface_alt))
    p.setColor(role.ToolTipText, QColor(C.text))
    p.setColor(role.PlaceholderText, QColor(C.text_muted))
    p.setColor(role.Text, QColor(C.text))
    p.setColor(role.Button, QColor(C.surface_alt))
    p.setColor(role.ButtonText, QColor(C.text))
    p.setColor(role.BrightText, QColor(C.danger))
    p.setColor(role.Link, QColor(C.accent))
    p.setColor(role.Highlight, QColor(C.selection))
    p.setColor(role.HighlightedText, QColor("#ffffff"))
    for r in (role.Text, role.ButtonText, role.WindowText):
        p.setColor(group.Disabled, r, QColor(C.text_muted))
    return p


STYLESHEET = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {C.bg}; }}
QToolTip {{ background: {C.surface_alt}; color: {C.text}; border: 1px solid {C.border}; padding: 4px; }}

/* ---- Panels ---- */
QFrame#Panel {{ background: {C.surface}; border: 1px solid {C.border}; border-radius: 8px; }}
QLabel#PanelTitle {{ color: {C.text}; font-weight: 600; font-size: 10.5pt; }}
QLabel#PanelSubtitle, QLabel#Muted {{ color: {C.text_muted}; }}
QLabel#Badge {{ background: {C.surface_alt}; color: {C.text_muted}; border: 1px solid {C.border};
               border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; }}

/* ---- Metric cards ---- */
QFrame#MetricCard {{ background: {C.surface_alt}; border: 1px solid {C.border}; border-radius: 6px; }}
QLabel#MetricValue {{ color: {C.text}; font-size: 15pt; font-weight: 600; }}
QLabel#MetricLabel {{ color: {C.text_muted}; font-size: 8.5pt; text-transform: uppercase; }}

/* ---- Splitters ---- */
QSplitter::handle {{ background: {C.bg}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {C.accent}; }}

/* ---- Buttons ---- */
QPushButton, QToolButton {{ background: {C.surface_alt}; color: {C.text}; border: 1px solid {C.border};
                            border-radius: 5px; padding: 5px 12px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {C.accent}; }}
QPushButton:disabled {{ color: {C.text_muted}; }}
QPushButton#Primary {{ background: {C.accent}; border-color: {C.accent}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {C.accent_hover}; }}
QPushButton#Danger {{ border-color: {C.danger}; color: {C.danger}; }}
QPushButton#Danger:disabled, QPushButton#Primary:disabled {{ background: {C.surface_alt};
                            border-color: {C.border}; color: {C.text_muted}; }}

/* ---- Inputs ---- */
QComboBox, QLineEdit {{ background: {C.surface_alt}; border: 1px solid {C.border}; border-radius: 5px;
                        padding: 4px 8px; color: {C.text}; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {C.accent}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {C.text_muted}; background: {C.surface}; border-color: {C.surface_alt}; }}
QComboBox QAbstractItemView {{ background: {C.surface_alt}; border: 1px solid {C.border};
                               selection-background-color: {C.selection}; }}
QPlainTextEdit, QTextBrowser {{ background: {C.surface}; border: none; color: {C.text};
                                selection-background-color: {C.selection}; }}

/* ---- Radio buttons / check boxes ---- */
QRadioButton, QCheckBox {{ spacing: 8px; }}
QRadioButton::indicator, QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {C.text_muted};
                                                 background: {C.surface_alt}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{ border-color: {C.accent}; }}
QRadioButton::indicator:checked {{ background: #ffffff; border: 4px solid {C.accent}; width: 8px; height: 8px; }}
QCheckBox::indicator:checked {{ background: {C.accent}; border-color: {C.accent}; }}
QRadioButton::indicator:disabled, QCheckBox::indicator:disabled {{ border-color: {C.border}; background: {C.surface}; }}

/* ---- Tabs ---- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C.text_muted}; padding: 6px 14px; border: none;
                border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {C.text}; border-bottom: 2px solid {C.accent}; }}
QTabBar::tab:hover {{ color: {C.text}; }}

/* ---- Data grid ---- */
QTableView {{ background: {C.surface}; alternate-background-color: {C.surface_alt}; border: none;
              gridline-color: {C.border}; selection-background-color: {C.selection}; }}
QHeaderView::section {{ background: {C.surface_alt}; color: {C.text_muted}; border: none;
                        border-right: 1px solid {C.border}; border-bottom: 1px solid {C.border};
                        padding: 4px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {C.surface_alt}; border: none; }}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {C.border}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {C.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---- Chrome ---- */
QMenuBar {{ background: {C.bg}; color: {C.text}; }}
QMenuBar::item:selected {{ background: {C.surface_alt}; }}
QMenu {{ background: {C.surface}; border: 1px solid {C.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {C.selection}; }}
QMenu::separator {{ height: 1px; background: {C.border}; margin: 4px 8px; }}
QToolBar {{ background: {C.bg}; border: none; border-bottom: 1px solid {C.border}; spacing: 6px; padding: 4px 8px; }}
QStatusBar {{ background: {C.surface}; color: {C.text_muted}; border-top: 1px solid {C.border}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{ background: {C.surface_alt}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {C.accent}; border-radius: 3px; }}
"""


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(STYLESHEET)
