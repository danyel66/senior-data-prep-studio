"""Headless smoke tests for the Phase 1 UI skeleton (run with QT_QPA_PLATFORM=offscreen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from sdps.app import create_app  # noqa: E402
from sdps.ui.main_window import MainWindow  # noqa: E402
from sdps.ui.widgets.code_editor import Language  # noqa: E402
from sdps.ui.widgets.results_panel import ResultsTableModel, SyntheticRows  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = create_app([])  # keep a reference: a GC'd QApplication aborts the process
    w = MainWindow()
    w.show()
    yield w
    w.close()


def test_layout_has_four_panels(window):
    assert window.main_splitter.count() == 2
    assert window.left_splitter.widget(0) is window.prompt_panel
    assert window.left_splitter.widget(1) is window.editor_panel
    assert window.right_splitter.widget(0) is window.results_panel
    assert window.right_splitter.widget(1) is window.telemetry_panel


def test_language_switch_keeps_separate_buffers(window):
    ed = window.editor_panel
    ed.editor.setPlainText("SELECT 1;")
    ed.language_combo.setCurrentText(Language.PYTHON.value)
    assert ed.language is Language.PYTHON
    assert "import pandas" in ed.editor.toPlainText()
    ed.language_combo.setCurrentText(Language.SQL.value)
    assert ed.editor.toPlainText() == "SELECT 1;"


def test_run_signal_emits_language_and_code(window):
    received = []
    window.editor_panel.run_requested.connect(lambda lang, code: received.append((lang, code)))
    window.editor_panel.editor.setPlainText("SELECT 42;")
    window.editor_panel.run_button.click()
    assert received[-1] == ("SQL", "SELECT 42;")


def test_model_is_lazy_for_large_results(window):
    model = ResultsTableModel()
    rows = SyntheticRows(1_000_000)
    model.set_data(SyntheticRows.COLUMNS, rows)
    assert model.total_rows == 1_000_000
    assert model.rowCount() == ResultsTableModel.FETCH_BATCH   # only first batch exposed
    model.fetchMore()
    assert model.rowCount() == 2 * ResultsTableModel.FETCH_BATCH
    assert model.data(model.index(0, 0)) == "43,659"


def test_demo_dataset_binds_fast(window):
    window._load_demo_dataset()
    assert window.results_panel.model.total_rows == 1_000_000
    assert window.results_panel.row_badge.text() == "1,000,000 rows"
