"""Phase 4: SQL helpers, execution engine, answer checking, Postgres streaming, UI run/check flows."""
import threading
import time

import pytest

from conftest import pg_conninfo, wait_until
from sdps.ai.examples import EXAMPLES
from sdps.db import ConnectionManager
from sdps.execution import ExecutionCancelled, ExecutionError, Executor, check_python, check_sql
from sdps.execution.sqltools import changes_schema, is_plan, render_dbt, returns_rows, split_statements

SQL_REF = EXAMPLES["Advanced SQL"]["reference_solution"]
PY_REF = EXAMPLES["Python / Pandas"]["reference_solution"]
DBT_REF = EXAMPLES["Analytics Engineering / dbt"]["reference_solution"]


@pytest.fixture
def db(tiny_config):
    m = ConnectionManager(tiny_config)
    m.connect()
    yield m
    m.disconnect()


# ───────────────────────────────────────── sqltools ─────────────────────────────────────────
def test_split_statements_respects_quotes_comments_and_dollar_quotes():
    script = ("-- note; not a split\nCREATE INDEX i ON t(x);\n"
              "SELECT 'a;b', \"c;d\" FROM t /* ; */ WHERE y = 'it''s;';\nDO $$ BEGIN PERFORM 1; END $$;\n-- end")
    parts = split_statements(script)
    assert len(parts) == 3
    assert changes_schema(parts[0]) and returns_rows(parts[1]) and not returns_rows(parts[2])


def test_dbt_rendering_and_plan_detection():
    assert render_dbt("{{ config(materialized='view') }}\nSELECT * FROM {{ source('sales','customer') }}") == \
        "SELECT * FROM sales.customer"
    assert is_plan(["explain_key", "explain_value"]) and is_plan(["QUERY PLAN"]) and not is_plan(["a"])


# ────────────────────────────────────────── engine ──────────────────────────────────────────
def test_sql_multi_statement_streams_last_result(db):
    seen = []
    r = Executor(db).run_sql("CREATE TEMP TABLE tmp_x AS SELECT 1 AS a; SELECT * FROM sales.customer",
                             on_columns=lambda cols, rows: seen.append((cols, rows)))
    assert r.statements == 2 and r.row_count == 1500 and not r.truncated and r.schema_changed
    assert seen and seen[0][0] == r.columns and seen[0][1] is r.rows        # UI binds to the growing list
    assert "Statement 1" in r.output and r.peak_rss_mb and r.elapsed_ms > 0


def test_sql_row_cap_truncates(db):
    r = Executor(db, row_cap=1_000, batch_size=300).run_sql("SELECT * FROM sales.sales_order_detail")
    assert r.row_count == 1_000 and r.truncated


def test_sql_errors_name_the_statement(db):
    with pytest.raises(ExecutionError, match="Statement 2 of 2.*nope"):
        Executor(db).run_sql("SELECT 1; SELECT nope FROM sales.customer")
    with pytest.raises(ExecutionError, match="only contains comments"):
        Executor(db).run_sql("-- just a comment")


def test_explain_analyze_produces_plan(db):
    r = Executor(db).run_sql("EXPLAIN ANALYZE SELECT territory_id, count(*) FROM sales.sales_order_header GROUP BY 1")
    assert r.plan and len(r.plan.splitlines()) > 5


def test_dbt_model_runs_with_sources(db):
    r = Executor(db).run_sql(DBT_REF)
    assert r.row_count > 5_000 and "line_amount" in r.columns


def test_sql_cancel_is_fast(db):
    ex = Executor(db)
    threading.Timer(0.3, ex.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(ExecutionCancelled):
        ex.run_sql("SELECT count(*) FROM range(5000000000) a(x) WHERE x % 7 = 3")
    assert time.monotonic() - t0 < 5


def test_python_run_with_sql_helper_and_print(db):
    code = ('df = sql("SELECT territory_id, total_due FROM sales.sales_order_header")\n'
            'print("loaded", len(df))\nresult = df.groupby("territory_id")["total_due"].sum()')
    r = Executor(db).run_python(code)
    assert r.output == "loaded 5000\n"
    assert r.columns == ["territory_id", "total_due"] and r.row_count == 10      # Series -> frame, index reset
    assert isinstance(r.rows[0][0], int) and isinstance(r.rows[0][1], float)    # numpy scalars -> Python


def test_python_errors_point_at_user_line(db):
    with pytest.raises(ExecutionError, match="Line 2: ZeroDivisionError"):
        Executor(db).run_python("x = 1\ny = x / 0")
    with pytest.raises(ExecutionError, match="Syntax error.*Line 1"):
        Executor(db).run_python("result = (")
    with pytest.raises(ExecutionError, match="sql\\(\\) query"):
        Executor(db).run_python('result = sql("SELECT nope FROM sales.customer")')


def test_python_non_dataframe_result_is_reported(db):
    r = Executor(db).run_python("result = 42")
    assert r.dataframe is None and "not a DataFrame" in r.output


def test_python_infinite_loop_can_be_cancelled(db):
    ex = Executor(db)
    threading.Timer(0.3, ex.cancel).start()
    with pytest.raises(ExecutionCancelled):
        ex.run_python("while True:\n    pass")


# ───────────────────────────────────────── checking ─────────────────────────────────────────
@pytest.mark.parametrize("user, passed, fragment", [
    (SQL_REF, True, "Correct"),
    (SQL_REF.replace("WHERE h.status <> 6", "WHERE true"), False, "missing"),
    (SQL_REF.replace("ORDER BY customers_for_half_revenue, territory", "ORDER BY territory DESC"), False, "wrong order"),
    (SQL_REF.replace("t.name AS territory", "t.name AS region").replace(
        "ORDER BY customers_for_half_revenue, territory", "ORDER BY customers_for_half_revenue, region"), True, "Correct"),
    ("SELECT 1 AS a", False, "Different columns"),
    ("SELEC 1", False, "error"),
    ("CREATE TEMP TABLE z AS SELECT 1", False, "changes data"),
])
def test_check_sql(db, user, passed, fragment):
    res = check_sql(db, user, SQL_REF, order_matters=True)
    assert res.passed is passed and fragment.lower() in res.headline.lower(), (res.headline, res.details)
    if not passed and "missing" in fragment:
        assert res.missing and res.unexpected and res.columns


def test_check_sql_tolerates_rounding_and_column_order(db):
    user = ("SELECT count(*) AS n, territory_id FROM sales.customer GROUP BY territory_id")
    ref = "SELECT territory_id, CAST(count(*) AS DOUBLE) + 0.00001 AS n FROM sales.customer GROUP BY territory_id"
    res = check_sql(db, user, ref, order_matters=False)
    assert res.passed and any("different order" in d for d in res.details)


def test_check_dbt_model(db):
    assert check_sql(db, DBT_REF, DBT_REF, order_matters=False).passed


def test_check_python(db):
    good = Executor(db).run_python(PY_REF)
    assert check_python(Executor(db), good.dataframe, PY_REF, order_matters=True).passed
    wrong = Executor(db).run_python(PY_REF.replace("> 3 *", "> 2 *"))
    res = check_python(Executor(db), wrong.dataframe, PY_REF, order_matters=True)
    assert not res.passed and res.missing and isinstance(res.missing[0][0], int)
    shuffled = Executor(db).run_python(PY_REF + "\nresult = result.iloc[::-1].reset_index(drop=True)")
    assert "order" in check_python(Executor(db), shuffled.dataframe, PY_REF, order_matters=True).headline
    assert check_python(Executor(db), shuffled.dataframe, PY_REF, order_matters=False).passed
    assert not check_python(Executor(db), None, PY_REF, order_matters=True).passed


# ───────────────────────────────────────── postgres ─────────────────────────────────────────
@pytest.mark.skipif(not pg_conninfo(), reason="set SDPS_TEST_PG to run PostgreSQL tests")
def test_postgres_streaming_and_check():
    from psycopg.conninfo import conninfo_to_dict
    from sdps.db import DatabaseConfig, Seeder
    p = conninfo_to_dict(pg_conninfo())
    cfg = DatabaseConfig(backend="postgres", pg_host=p.get("host", "localhost"), pg_port=int(p.get("port", 5432)),
                         pg_user=p.get("user", "postgres"), pg_database=p.get("dbname", "postgres"))
    m = ConnectionManager(cfg)
    m.connect()
    if not m.status().seeded:
        Seeder("tiny").build_postgres(cfg.pg_conninfo())
    r = Executor(m, row_cap=1_000, batch_size=250).run_sql("SELECT * FROM sales.sales_order_detail")
    assert r.row_count == 1_000 and r.truncated
    plan = Executor(m).run_sql("EXPLAIN ANALYZE SELECT count(*) FROM sales.customer")
    assert plan.plan and "Aggregate" in plan.plan
    assert check_sql(m, SQL_REF, SQL_REF, order_matters=True).passed
    assert not check_sql(m, SQL_REF.replace("WHERE h.status <> 6", "WHERE true"), SQL_REF, True).passed
    py = Executor(m).run_python(PY_REF)
    assert check_python(Executor(m), py.dataframe, PY_REF, True).passed
    m.disconnect()


# ──────────────────────────────────────────── UI ────────────────────────────────────────────
@pytest.fixture
def window(qapp, tiny_config):
    from sdps.ui.main_window import MainWindow
    w = MainWindow(ConnectionManager(tiny_config), prompt_to_seed=False)
    w.show()
    wait_until(lambda: w.tables)
    yield w
    w.close()


def run_and_wait(w, language, code, check=False):
    w.editor_panel.load_text(language, code)
    (w.editor_panel.check_button if check else w.editor_panel.run_button).click()
    wait_until(lambda: w._executor is None and w.editor_panel.run_button.isEnabled(), timeout=60)


def test_ui_runs_sql_and_shows_metrics(window):
    run_and_wait(window, "SQL", "SELECT * FROM sales.customer")
    rp, tp = window.results_panel, window.telemetry_panel
    assert rp.model.total_rows == 1500 and rp.row_badge.text() == "1,500 rows"
    assert tp.status_card.value_label.text() == "OK" and tp.rows_card.value_label.text() == "1,500"
    assert "MB" in tp.mem_card.value_label.text()


def test_ui_shows_errors_in_output_tab(window):
    run_and_wait(window, "SQL", "SELECT nope FROM sales.customer")
    rp = window.results_panel
    assert rp.tabs.currentWidget() is rp.output and "nope" in rp.output.toPlainText()
    assert window.telemetry_panel.status_card.value_label.text() == "Error"


def test_ui_check_answer_against_challenge(window):
    from sdps.ai import Challenge
    from sdps.ai.examples import example_json
    ch = Challenge.from_model_output(example_json("Advanced SQL"))
    window._open_challenge(ch)
    assert window.editor_panel.check_button.isEnabled()
    run_and_wait(window, "SQL", SQL_REF.replace("WHERE h.status <> 6", "WHERE true"), check=True)
    rp = window.results_panel
    assert rp.tabs.currentWidget() is rp.check and "Not quite" in rp.check.toPlainText()
    assert window.telemetry_panel.status_card.value_label.text() == "Not quite"
    run_and_wait(window, "SQL", SQL_REF, check=True)
    assert "Correct" in rp.check.toPlainText()


def test_ui_python_run_and_check(window):
    from sdps.ai import Challenge
    from sdps.ai.examples import example_json
    window._open_challenge(Challenge.from_model_output(example_json("Python / Pandas")))
    run_and_wait(window, "Python", PY_REF, check=True)
    assert "Correct" in window.results_panel.check.toPlainText()
    assert window.results_panel.model.columnCount() == 5


def test_ui_cancel_long_query(window):
    window.editor_panel.load_text("SQL", "SELECT count(*) FROM range(5000000000) a(x) WHERE x % 7 = 3")
    window.editor_panel.run_button.click()
    wait_until(lambda: window._executor is not None)
    time.sleep(0.3)
    window.editor_panel.cancel_button.click()
    wait_until(lambda: window._executor is None, timeout=15)
    assert window.telemetry_panel.status_card.value_label.text() == "Cancelled"


def test_ui_ddl_refreshes_schema(window):
    run_and_wait(window, "SQL", "CREATE TABLE sales.my_practice AS SELECT 1 AS x; SELECT * FROM sales.my_practice")
    wait_until(lambda: any(t.name == "my_practice" for t in window.tables) and not window._db_busy)
