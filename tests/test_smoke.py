"""Headless UI tests (QT_QPA_PLATFORM=offscreen is set in conftest)."""
import pytest

from conftest import wait_until
from sdps.db import ConnectionManager, DatabaseConfig
from sdps.ui.main_window import MainWindow
from sdps.ui.widgets.code_editor import Language
from sdps.ui.widgets.results_panel import ResultsTableModel, SyntheticRows


@pytest.fixture
def window(qapp, tiny_config):
    w = MainWindow(ConnectionManager(tiny_config), prompt_to_seed=False)
    w.show()
    wait_until(lambda: w.tables)            # background connect + introspection finished
    yield w
    w.close()


def test_layout_has_five_panels(window):
    assert window.main_splitter.count() == 3
    assert window.main_splitter.widget(0) is window.schema_browser
    assert window.left_splitter.widget(0) is window.prompt_panel
    assert window.left_splitter.widget(1) is window.editor_panel
    assert window.right_splitter.widget(0) is window.results_panel
    assert window.right_splitter.widget(1) is window.telemetry_panel


def test_connects_in_background_and_populates_schema(window):
    assert len(window.tables) == 29
    tree = window.schema_browser.tree
    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == \
        ["humanresources", "person", "production", "purchasing", "sales"]
    assert "tiny" in window.db_status.text() and "5,000 orders" in window.db_status.text()


def test_schema_filter_and_insert(window):
    sb = window.schema_browser
    sb.filter_edit.setText("territory")
    visible_tables = [sb.tree.topLevelItem(i).child(j).text(0)
                      for i in range(sb.tree.topLevelItemCount()) for j in range(sb.tree.topLevelItem(i).childCount())
                      if not sb.tree.topLevelItem(i).child(j).isHidden()]
    assert "sales_territory" in visible_tables and "sales_order_header" in visible_tables  # via territory_id column
    assert "department" not in visible_tables
    sb.filter_edit.clear()
    window.editor_panel.editor.setPlainText("")
    sb.insert_requested.emit("sales.sales_order_header")
    assert window.editor_panel.editor.toPlainText() == "sales.sales_order_header"


def test_empty_database_shows_generate_link(qapp, tmp_path):
    w = MainWindow(ConnectionManager(DatabaseConfig(duckdb_path=str(tmp_path / "e.duckdb"))), prompt_to_seed=False)
    wait_until(lambda: not w._db_busy and w.db.is_connected)
    assert "Generate AdventureWorks data" in w.schema_browser.message.text()
    assert "no AdventureWorks data" in w.db_status.text()
    w.close()


def test_connection_failure_is_reported_not_raised(qapp, tmp_path):
    cfg = DatabaseConfig(duckdb_path=str(tmp_path / "missing_dir" / "x" / "\0bad.duckdb"))
    w = MainWindow(ConnectionManager(cfg), prompt_to_seed=False)
    wait_until(lambda: "failed" in w.db_status.text())
    assert not w.db.is_connected
    w.close()


def test_seed_dialog_builds_and_swaps_database(qapp, tmp_path):
    from sdps.ui.dialogs import SeedDialog
    m = ConnectionManager(DatabaseConfig(duckdb_path=str(tmp_path / "fresh.duckdb")))
    m.connect()
    dlg = SeedDialog(m)
    dlg.select_scale("tiny")
    got = []
    dlg.seeded.connect(got.append)
    dlg.start_button.click()
    wait_until(lambda: got, timeout=60)
    assert got[0]["sales.sales_order_header"] == 5000
    assert m.status().seeded and not (tmp_path / "fresh.duckdb.building").exists()
    m.disconnect()


def test_language_switch_keeps_separate_buffers(window):
    ed = window.editor_panel
    ed.editor.setPlainText("SELECT 1;")
    ed.language_combo.setCurrentText(Language.PYTHON.value)
    assert ed.language is Language.PYTHON
    assert "sql(" in ed.editor.toPlainText() and "con.sql" not in ed.editor.toPlainText()
    ed.language_combo.setCurrentText(Language.SQL.value)
    assert ed.editor.toPlainText() == "SELECT 1;"


def test_run_signal_emits_language_and_code(window):
    received = []
    window.editor_panel.run_requested.connect(lambda lang, code: received.append((lang, code)))
    window.editor_panel.editor.setPlainText("SELECT 42;")
    window.editor_panel.run_button.click()
    assert received[-1] == ("SQL", "SELECT 42;")


def test_model_is_lazy_for_large_results(qapp):
    model = ResultsTableModel()
    model.set_data(SyntheticRows.COLUMNS, SyntheticRows(1_000_000))
    assert model.total_rows == 1_000_000
    assert model.rowCount() == ResultsTableModel.FETCH_BATCH
    model.fetchMore()
    assert model.rowCount() == 2 * ResultsTableModel.FETCH_BATCH
    assert model.data(model.index(0, 0)) == "43,659"
