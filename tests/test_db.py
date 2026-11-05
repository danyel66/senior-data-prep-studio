"""Database layer: seed correctness, realism properties, manager, config, cancellation, Postgres."""
import json
import threading

import duckdb
import pytest

from conftest import pg_conninfo
from integrity import duplicate_pk_report, orphan_report
from sdps.db import ConnectionManager, DatabaseConfig, DatabaseError, SeedCancelled, Seeder
from sdps.db.sql_files import constraint_statements, schema_statements


@pytest.fixture(scope="module")
def con(tiny_db):
    c = duckdb.connect(str(tiny_db), read_only=True)
    yield c
    c.close()


def scalar(con, sql):
    return con.execute(sql).fetchall()[0][0]


def test_ddl_parses_into_statements():
    stmts = schema_statements()
    assert sum(s.startswith("CREATE TABLE") for s in stmts) == 29
    assert len(constraint_statements({"fk"})) == 38


def test_all_tables_populated(con):
    empty = [t for (t,) in con.execute(
        "SELECT schema_name || '.' || table_name FROM duckdb_tables() WHERE estimated_size = 0").fetchall()]
    assert empty == []


def test_referential_integrity(con):
    assert orphan_report(lambda q: scalar(con, q)) == {}
    assert duplicate_pk_report(lambda q: scalar(con, q)) == {}


def test_header_totals_match_lines(con):
    assert scalar(con, """
        SELECT count(*) FROM sales.sales_order_header h
        JOIN (SELECT sales_order_id, sum(line_total) s FROM sales.sales_order_detail GROUP BY 1) d USING (sales_order_id)
        WHERE h.sub_total <> d.s OR h.total_due <> h.sub_total + h.tax_amt + h.freight""") == 0
    assert scalar(con, "SELECT count(*) FROM sales.sales_order_header h WHERE NOT EXISTS "
                       "(SELECT 1 FROM sales.sales_order_detail d WHERE d.sales_order_id = h.sales_order_id)") == 0


def test_customer_is_person_xor_store(con):
    assert scalar(con, "SELECT count(*) FROM sales.customer WHERE (person_id IS NULL) = (store_id IS NULL)") == 0


def test_org_tree_single_root_and_fully_reachable(con):
    assert scalar(con, "SELECT count(*) FROM humanresources.employee WHERE manager_id IS NULL") == 1
    reachable = scalar(con, """
        WITH RECURSIVE t AS (SELECT business_entity_id FROM humanresources.employee WHERE manager_id IS NULL
                             UNION ALL SELECT e.business_entity_id FROM humanresources.employee e
                             JOIN t ON e.manager_id = t.business_entity_id)
        SELECT count(*) FROM t""")
    assert reachable == scalar(con, "SELECT count(*) FROM humanresources.employee")


def test_scd2_histories_have_one_current_row(con):
    assert scalar(con, "SELECT count(*) FROM (SELECT business_entity_id FROM humanresources.employee_department_history "
                       "WHERE end_date IS NULL GROUP BY 1 HAVING count(*) <> 1)") == 0
    assert scalar(con, "SELECT count(*) FROM humanresources.employee_department_history "
                       "WHERE end_date IS NOT NULL AND end_date < start_date") == 0
    assert scalar(con, """SELECT count(*) FROM production.product p
                          JOIN production.product_list_price_history h ON h.product_id = p.product_id AND h.end_date IS NULL
                          WHERE h.list_price <> p.list_price""") == 0


def test_realistic_skew(con):
    # top two territories hold far more than 2/10 of orders
    top2 = scalar(con, """SELECT sum(n) / (SELECT count(*) FROM sales.sales_order_header) FROM (
                            SELECT count(*) n FROM sales.sales_order_header GROUP BY territory_id ORDER BY n DESC LIMIT 2)""")
    assert top2 > 0.35
    # many customers never order; one customer orders a lot
    assert scalar(con, "SELECT count(*) FROM sales.customer c WHERE NOT EXISTS "
                       "(SELECT 1 FROM sales.sales_order_header h WHERE h.customer_id = c.customer_id)") > 50
    # volume grows year over year
    years = [n for _, n in con.execute("SELECT year(order_date), count(*) FROM sales.sales_order_header "
                                       "GROUP BY 1 ORDER BY 1").fetchall()]
    assert years == sorted(years) and len(years) == 4


def test_online_orders_have_no_sales_person_and_resellers_do(con):
    assert scalar(con, "SELECT count(*) FROM sales.sales_order_header WHERE online_order_flag AND sales_person_id IS NOT NULL") == 0
    assert scalar(con, "SELECT count(*) FROM sales.sales_order_header WHERE NOT online_order_flag AND sales_person_id IS NULL") == 0


def test_generation_is_deterministic(tmp_path, con):
    other = tmp_path / "again.duckdb"
    Seeder("tiny").build_duckdb(other)
    c2 = duckdb.connect(str(other), read_only=True)
    q = "SELECT sum(hash(sales_order_id, product_id, line_total)) FROM sales.sales_order_detail"
    assert scalar(c2, q) == scalar(con, q)
    c2.close()


def test_seed_can_be_cancelled(tmp_path):
    ev = threading.Event()
    ev.set()
    with pytest.raises(SeedCancelled):
        Seeder("tiny", cancel_event=ev).build_duckdb(tmp_path / "x.duckdb")


def test_progress_is_monotonic_and_completes(tmp_path):
    seen = []
    Seeder("tiny", progress=lambda f, m: seen.append(f)).build_duckdb(tmp_path / "p.duckdb")
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


# ───────────────────────────────── manager / config ─────────────────────────────────
def test_manager_connect_introspect_and_sessions(tiny_config):
    m = ConnectionManager(tiny_config)
    status = m.connect()
    assert status.connected and status.seeded and status.metadata["scale"] == "tiny"
    tables = m.introspect()
    assert len(tables) == 29
    detail = next(t for t in tables if t.qualified_name == "sales.sales_order_detail")
    assert detail.row_count > 5000 and detail.comment and len(detail.columns) == 9
    s = m.open_session()
    assert s.execute("SELECT count(*) FROM sales.sales_order_header").rows == [(5000,)]
    with pytest.raises(DatabaseError):
        m.execute("SELECT * FROM does_not_exist")
    m.execute("SELECT 1")      # connection still usable after an error
    m.disconnect()
    assert not m.is_connected


def test_manager_reports_unseeded_database(tmp_path):
    m = ConnectionManager(DatabaseConfig(duckdb_path=str(tmp_path / "empty.duckdb")))
    status = m.connect()
    assert status.connected and not status.seeded and m.introspect() == []
    m.disconnect()


def test_install_duckdb_file_swaps_and_reconnects(tiny_config, tmp_path):
    m = ConnectionManager(DatabaseConfig(duckdb_path=str(tmp_path / "live.duckdb")))
    assert not m.connect().seeded
    status = m.install_duckdb_file(tiny_config.duckdb_path)
    assert status.seeded and len(m.introspect()) == 29
    m.disconnect()


def test_config_round_trip_never_persists_password():
    cfg = DatabaseConfig(backend="postgres", pg_host="db.example", pg_password="hunter2")
    cfg.save()
    raw = DatabaseConfig.path().read_text()
    assert "hunter2" not in raw and json.loads(raw)["database"]["pg_host"] == "db.example"
    loaded = DatabaseConfig.load()
    assert loaded.backend == "postgres" and loaded.pg_password == ""
    DatabaseConfig().save()


# ───────────────────────────────────── postgres ─────────────────────────────────────
@pytest.mark.skipif(not pg_conninfo(), reason="set SDPS_TEST_PG to run PostgreSQL tests")
def test_postgres_seed_and_backend():
    from psycopg.conninfo import conninfo_to_dict
    p = conninfo_to_dict(pg_conninfo())
    cfg = DatabaseConfig(backend="postgres", pg_host=p.get("host", "localhost"), pg_port=int(p.get("port", 5432)),
                         pg_user=p.get("user", "postgres"), pg_database=p.get("dbname", "postgres"),
                         pg_password=p.get("password", ""))
    counts = Seeder("tiny").build_postgres(cfg.pg_conninfo())
    m = ConnectionManager(cfg)
    status = m.connect()
    assert status.seeded and status.server_version.startswith("PostgreSQL")
    assert m.execute("SELECT count(*) FROM sales.sales_order_detail").rows[0][0] == counts["sales.sales_order_detail"]
    fks = m.execute("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE contype = 'f' AND nspname IN ('person','humanresources','production','purchasing','sales')")
    assert fks.rows[0][0] == 38          # Postgres itself validated every foreign key
    assert any(t.qualified_name == "sales.customer" for t in m.introspect())
    m.disconnect()
