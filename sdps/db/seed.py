"""AdventureWorks generator.

Data is generated *inside DuckDB* with set-based SQL (no Python row loops for the large tables),
so 10M orders / ~25M order lines build in well under a minute on a laptop. Randomness comes from
`hash(key, salt)`, which makes the dataset deterministic for a given scale and DuckDB version.

Built-in realism for senior-level practice:
  * Territory skew: two territories hold ~46% of customers (and more of the revenue).
  * Customer skew: order frequency is power-law distributed, many customers never order.
  * Product skew: popularity follows a power law over ~300 sellable products.
  * Growth trend: order volume increases linearly over 2021-2024.
  * Resellers (stores) place large, multi-line, volume-discounted orders; individuals buy online.
  * Type-2 history tables (prices, departments), a manager_id org tree, weekday-only FX rates.
  * Quirks to discover: NULL-heavy columns, customers with no orders, people with 0 or 2 emails,
    discontinued products that still appear on later orders.

PostgreSQL targets are built by generating in DuckDB first and streaming each table with COPY.
"""
from __future__ import annotations

import datetime as dt
import os
import random
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from . import reference_data as ref
from .sql_files import constraint_statements, schema_statements

ProgressFn = Callable[[float, str], None]
SEED_VERSION = "1"
START_DATE = dt.date(2021, 1, 1)
DAYS = 1461                       # 2021-01-01 .. 2024-12-31
END_DATE = START_DATE + dt.timedelta(days=DAYS - 1)


@dataclass(frozen=True)
class Scale:
    key: str
    label: str
    orders: int
    customers: int
    employees: int
    sales_people: int
    vendors: int
    purchase_orders: int
    est_seconds: int              # measured DuckDB build time, 4 cores (rough)
    est_disk_mb: int

    @property
    def est_order_lines(self) -> int:
        return int(self.orders * 2.44)


SCALES: dict[str, Scale] = {s.key: s for s in [
    Scale("tiny", "Tiny (dev / tests)", 5_000, 1_500, 120, 13, 40, 600, 1, 5),
    Scale("small", "Small", 100_000, 25_000, 290, 17, 104, 4_000, 2, 20),
    Scale("medium", "Medium (recommended)", 1_000_000, 200_000, 600, 40, 250, 20_000, 6, 100),
    Scale("large", "Large", 10_000_000, 1_500_000, 2_000, 120, 600, 150_000, 35, 860),
    Scale("xl", "XL (production-scale)", 30_000_000, 4_000_000, 5_000, 300, 1_000, 400_000, 120, 2_600),
]}
DEFAULT_SCALE = "medium"

SCHEMAS = ["person", "humanresources", "production", "purchasing", "sales"]


class SeedCancelled(Exception):
    pass


# ───────────────────────────────────────── helpers ─────────────────────────────────────────
def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def _sql_list(values: list[str]) -> str:
    return "[" + ", ".join(_lit(v) for v in values) + "]"


def _insert_rows(con, table: str, rows: list[tuple], batch: int = 500) -> None:
    for i in range(0, len(rows), batch):
        values = ",\n".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in rows[i:i + batch])
        con.execute(f"INSERT INTO {table} VALUES {values}")


def _weighted_case(expr: str, weights: list[tuple[int, float]]) -> str:
    """CASE expression mapping a uniform [0,1) expression onto ids by cumulative weight."""
    parts, cum = [], 0.0
    for value, w in weights[:-1]:
        cum += w
        parts.append(f"WHEN {expr} < {cum:.6f} THEN {value}")
    return f"CASE {' '.join(parts)} ELSE {weights[-1][0]} END"


# ───────────────────────────────────────── seeder ──────────────────────────────────────────
class Seeder:
    def __init__(self, scale: Scale | str, progress: ProgressFn | None = None,
                 cancel_event: threading.Event | None = None):
        self.scale = SCALES[scale] if isinstance(scale, str) else scale
        self._progress = progress or (lambda f, m: None)
        self._cancel = cancel_event or threading.Event()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Thread-safe: stops between steps and interrupts the running statement."""
        self._cancel.set()
        with self._lock:
            if self._con is not None:
                try:
                    self._con.interrupt()
                except Exception:
                    pass

    # ---- public entry points ---------------------------------------------------------------
    def build_duckdb(self, path: str | os.PathLike, *, span: tuple[float, float] = (0.0, 1.0)) -> dict:
        """Build a complete database at `path` (overwritten). Returns per-table row counts."""
        path = Path(path)
        for p in (path, Path(str(path) + ".wal")):
            p.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        with self._lock:
            self._con = con
        try:
            self._run_steps(con, span)
            return self._row_counts(con)
        except duckdb.InterruptException as e:
            raise SeedCancelled() from e
        finally:
            with self._lock:
                self._con = None
            con.close()

    def build_postgres(self, conninfo: str, work_dir: str | None = None) -> dict:
        """Generate in a scratch DuckDB file, then (re)create the schemas in Postgres and COPY data in."""
        import psycopg  # optional dependency

        with tempfile.TemporaryDirectory(dir=work_dir, prefix="sdps_seed_") as tmp:
            duck_path = Path(tmp) / "stage.duckdb"
            counts = self.build_duckdb(duck_path, span=(0.0, 0.55))
            self._report(0.56, "Connecting to PostgreSQL…")
            src = duckdb.connect(str(duck_path), read_only=True)
            try:
                with psycopg.connect(conninfo, autocommit=True, connect_timeout=10) as pg:
                    self._load_postgres(src, pg, counts, Path(tmp))
            finally:
                src.close()
        return counts

    # ---- step runner -----------------------------------------------------------------------
    def _report(self, frac: float, msg: str) -> None:
        self._progress(max(0.0, min(1.0, frac)), msg)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise SeedCancelled()

    def _run_steps(self, con, span: tuple[float, float]) -> None:
        s = self.scale
        steps: list[tuple[str, float, Callable]] = [
            ("Creating schema", 1, self._create_schema),
            ("Reference data", 1, self._reference_data),
            ("Products & price history", 1, self._products),
            ("Employees & org hierarchy", 1, self._employees),
            ("Customers, people, stores, addresses", 3 + s.customers / 200_000, self._customers),
            ("Vendors & purchase orders", 1 + s.purchase_orders / 50_000, self._purchasing),
            ("Sales orders (staging)", 2 + s.orders / 400_000, self._orders_stage),
            ("Sales order lines", 4 + s.orders / 80_000, self._order_lines),
            ("Sales order headers", 2 + s.orders / 250_000, self._order_headers),
            ("Constraints, statistics & checkpoint", 1 + s.orders / 500_000, self._finalize),
        ]
        total = sum(w for _, w, _ in steps)
        lo, hi = span
        done = 0.0
        con.execute("CREATE OR REPLACE TEMP MACRO rnd(k, s) AS CAST(hash(k, s) % 1000000000 AS DOUBLE) / 1e9")
        con.execute("CREATE OR REPLACE TEMP MACRO ri(k, s, n) AS CAST(hash(k, s) % CAST(n AS UBIGINT) AS INTEGER)")
        for label, weight, fn in steps:
            self._check_cancel()
            self._report(lo + (hi - lo) * done / total, f"{label}…")
            t0 = time.perf_counter()
            fn(con)
            done += weight
            self._report(lo + (hi - lo) * done / total, f"{label} ✓ ({time.perf_counter() - t0:.1f}s)")

    # ---- steps -----------------------------------------------------------------------------
    def _create_schema(self, con) -> None:
        for stmt in schema_statements():
            con.execute(stmt)
        con.execute("CREATE TABLE sdps_metadata (key VARCHAR PRIMARY KEY, value VARCHAR)")

    def _reference_data(self, con) -> None:
        _insert_rows(con, "person.country_region", ref.COUNTRIES)
        _insert_rows(con, "sales.sales_territory", [t[:4] for t in ref.TERRITORIES])
        _insert_rows(con, "person.state_province",
                     [(i + 1, code, country, name, terr) for i, (code, country, name, terr, _) in enumerate(ref.STATES)])
        con.execute("CREATE TEMP TABLE _cities (state_province_id INTEGER, cities VARCHAR)")
        _insert_rows(con, "_cities", [(i + 1, s[4]) for i, s in enumerate(ref.STATES)])
        _insert_rows(con, "humanresources.department", ref.DEPARTMENTS)
        _insert_rows(con, "production.product_category", ref.CATEGORIES)
        _insert_rows(con, "production.product_subcategory", [s[:3] for s in ref.SUBCATEGORIES])
        _insert_rows(con, "production.location", ref.LOCATIONS)
        _insert_rows(con, "purchasing.ship_method", ref.SHIP_METHODS)
        _insert_rows(con, "sales.special_offer",
                     [(o[0], o[1], o[2], o[3], o[4], START_DATE, END_DATE, o[5], o[6]) for o in ref.SPECIAL_OFFERS])
        _insert_rows(con, "sales.currency", ref.CURRENCIES)
        rates = ", ".join(f"({i}, {_lit(c)}, {base})" for i, (c, base) in enumerate(ref.RATE_CURRENCIES))
        n = len(ref.RATE_CURRENCIES)
        con.execute(f"""
            INSERT INTO sales.currency_rate
            SELECT d * {n} + c.idx + 1, DATE '{START_DATE}' + CAST(d AS INTEGER), 'USD', c.code,
                   round(c.base * (1 + 0.06 * sin(d / 120.0 + c.idx) + 0.01 * (rnd(d * 10 + c.idx, 'fx') - 0.5)), 6),
                   round(c.base * (1 + 0.06 * sin((d + 0.5) / 120.0 + c.idx) + 0.01 * (rnd(d * 10 + c.idx, 'fx2') - 0.5)), 6)
            FROM range({DAYS}) r(d), (VALUES {rates}) c(idx, code, base)
            WHERE isodow(DATE '{START_DATE}' + CAST(d AS INTEGER)) < 6
            ORDER BY 1
        """)

    def _products(self, con) -> None:
        rng = random.Random(20210101)
        products, history, inventory = [], [], []
        sell_start = dt.date(2020, 6, 1)
        # raw parts: not sold, half purchased from vendors
        for k in range(ref.N_RAW_PARTS):
            pid = k + 1
            base = ref.RAW_PART_NAMES[k % len(ref.RAW_PART_NAMES)]
            name = base if k < len(ref.RAW_PART_NAMES) else f"{base} {k // len(ref.RAW_PART_NAMES) + 1:02d}"
            cost = round(rng.uniform(0.2, 80.0), 4)
            products.append((pid, name, f"RP-{pid:04d}", rng.random() < 0.5, False, None,
                             rng.choice([4, 8, 500, 800, 1000]), rng.choice([3, 6, 375, 600, 750]),
                             cost, 0.0, None, None, None, sell_start, None, None))
        # finished goods, spread over subcategories by assortment weight
        subcats = [s for s in ref.SUBCATEGORIES for _ in range(s[4])]
        for k in range(ref.N_FINISHED_PRODUCTS):
            pid = ref.FINISHED_PRODUCT_ID_START + k
            sub_id, cat_id, _, model, _ = subcats[k % len(subcats)]
            lo, hi, made = ref.CATEGORY_ECONOMICS[cat_id]
            tier_idx = rng.randrange(3)
            tier = ["LL", "ML", "HL"][tier_idx]
            price = round(lo + (hi - lo) * (tier_idx / 3 + rng.random() / 3), 2)
            color = rng.choice(ref.COLORS) if cat_id != 2 or rng.random() < 0.5 else None
            size = (rng.choice(ref.BIKE_SIZES) if sub_id in (1, 2, 3, 12, 14, 16)
                    else rng.choice(ref.APPAREL_SIZES) if cat_id == 3 and sub_id not in (19, 23) else None)
            weight = round(rng.uniform(17, 30), 2) if cat_id == 1 else (
                round(rng.uniform(1, 4), 2) if size and cat_id == 2 else None)
            name = f"{model} {tier}-{100 + (k * 37) % 900}" + (f", {color}" if color else "") + (f", {size}" if size else "")
            discontinued = rng.random() < 0.05
            end = dt.date(2024, rng.randint(1, 9), 1) if discontinued else None
            products.append((pid, name[:50], f"{model[:2].upper()}-{pid:04d}", made, True, color,
                             rng.choice([100, 500]), rng.choice([75, 375]),
                             round(price * rng.uniform(0.55, 0.72), 4), price, size, weight, sub_id,
                             sell_start, end, end))
            for year in range(2021, 2025):            # type-2 price history: +3% per year
                history.append((pid, dt.date(year, 1, 1) if year > 2021 else sell_start,
                                None if year == 2024 else dt.date(year, 12, 31),
                                round(price * 1.03 ** (year - 2024), 2)))
        loc_ids = [loc[0] for loc in ref.LOCATIONS]
        for p in products:
            base = rng.randrange(len(loc_ids))
            for m in range(1 + rng.randrange(3)):
                inventory.append((p[0], loc_ids[(base + m * 5) % len(loc_ids)], chr(65 + rng.randrange(25)),
                                  rng.randrange(61), rng.randrange(800)))
        _insert_rows(con, "production.product", products)
        _insert_rows(con, "production.product_list_price_history", history)
        _insert_rows(con, "production.product_inventory", inventory)
        # helper tables for the generators
        con.execute("""
            CREATE TEMP TABLE _sellable AS
            SELECT CAST(row_number() OVER (ORDER BY hash(product_id, 'popularity')) AS INTEGER) AS prank,
                   product_id, CAST(list_price AS DOUBLE) AS list_price
            FROM production.product WHERE finished_goods_flag
        """)
        con.execute("""
            CREATE TEMP TABLE _purchasable AS
            SELECT CAST(row_number() OVER (ORDER BY product_id) - 1 AS INTEGER) AS idx, product_id,
                   CAST(standard_cost AS DOUBLE) AS standard_cost
            FROM production.product WHERE NOT make_flag
        """)

    def _employees(self, con) -> None:
        s = self.scale
        E, S = s.employees, s.sales_people
        other_depts = [d[0] for d in ref.DEPARTMENTS
                       if d[0] not in (ref.CEO_DEPARTMENT, ref.SALES_DEPARTMENT, ref.PURCHASING_DEPARTMENT)]
        con.execute(f"""
            CREATE TEMP TABLE _emp AS
            SELECT i AS id,
                   CASE WHEN i = 1 THEN NULL
                        WHEN i > {E - S} THEN 1 + ri(i, 'mgr', {E - S})          -- reps report to non-reps
                        ELSE 1 + CAST(floor(rnd(i, 'mgr') * (i - 1)) AS INTEGER) -- random recursive tree
                   END AS manager_id,
                   i > {E - S} AS is_sales,
                   CASE WHEN i = 1 THEN {ref.CEO_DEPARTMENT}
                        WHEN i > {E - S} THEN {ref.SALES_DEPARTMENT}
                        WHEN i % 25 = 2 THEN {ref.PURCHASING_DEPARTMENT}
                        WHEN rnd(i, 'dept') < 0.55 THEN {ref.PRODUCTION_DEPARTMENT}
                        ELSE {other_depts}[1 + ri(i, 'dept2', {len(other_depts)})]
                   END AS department_id,
                   DATE '2008-01-01' + ri(i, 'hire', 5800) AS hire_date
            FROM range(1, {E + 1}) t(i)
        """)
        con.execute("""
            CREATE TEMP TABLE _lvl AS
            WITH RECURSIVE tree(id, lvl) AS (
                SELECT id, 0 FROM _emp WHERE manager_id IS NULL
                UNION ALL
                SELECT e.id, t.lvl + 1 FROM _emp e JOIN tree t ON e.manager_id = t.id
            ) SELECT id, lvl FROM tree
        """)
        first, last = _sql_list(ref.FIRST_NAMES), _sql_list(ref.LAST_NAMES)
        con.execute(f"""
            CREATE TEMP TABLE _people AS
            SELECT e.id AS be_id, CASE WHEN e.is_sales THEN 'SP' ELSE 'EM' END AS person_type,
                   {first}[1 + ri(e.id, 'fn', {len(ref.FIRST_NAMES)})] AS first_name,
                   {last}[1 + ri(e.id, 'ln', {len(ref.LAST_NAMES)})] AS last_name,
                   CASE WHEN rnd(e.id, 'terr') < 0.9 THEN 1 ELSE 4 END AS territory_id
            FROM _emp e
        """)
        con.execute(f"""
            INSERT INTO humanresources.employee
            SELECT e.id, CAST(100000000 + hash(e.id, 'nid') % 899999999 AS VARCHAR),
                   'adventure-works\\' || lower(p.first_name) || e.id, e.manager_id, l.lvl,
                   CASE WHEN l.lvl = 0 THEN 'Chief Executive Officer'
                        WHEN e.is_sales THEN 'Sales Representative'
                        WHEN l.lvl = 1 THEN 'Vice President of ' || d.name
                        WHEN l.lvl = 2 THEN d.name || ' Director'
                        WHEN l.lvl = 3 THEN d.name || ' Manager'
                        WHEN l.lvl = 4 THEN d.name || ' Supervisor'
                        ELSE d.name || ' ' || ['Technician', 'Specialist', 'Assistant', 'Analyst'][1 + ri(e.id, 'jt', 4)]
                   END,
                   e.hire_date - CAST(365.25 * (20 + ri(e.id, 'age', 35)) AS INTEGER),
                   CASE WHEN rnd(e.id, 'ms') < 0.5 THEN 'M' ELSE 'S' END,
                   CASE WHEN rnd(e.id, 'g') < 0.5 THEN 'M' ELSE 'F' END,
                   e.hire_date, l.lvl <= 3 OR e.is_sales,
                   ri(e.id, 'vac', 100), 20 + ri(e.id, 'sick', 60), rnd(e.id, 'cur') > 0.02
            FROM _emp e JOIN _lvl l USING (id) JOIN _people p ON p.be_id = e.id
            JOIN humanresources.department d USING (department_id)
            ORDER BY e.id
        """)
        con.execute(f"""
            INSERT INTO humanresources.employee_department_history
            WITH x AS (
                SELECT id, department_id, hire_date,
                       rnd(id, 'xfer') < 0.3 AND id > 1 AS moved,
                       1 + (department_id + ri(id, 'pd', 15)) % 16 AS prev_dept,
                       hire_date + 1 + ri(id, 'xd', greatest(1, DATE '{END_DATE}' - hire_date - 1)) AS moved_on
                FROM _emp
            )
            SELECT id, prev_dept, hire_date, moved_on - 1 FROM x WHERE moved
            UNION ALL
            SELECT id, department_id, CASE WHEN moved THEN moved_on ELSE hire_date END, NULL FROM x
            ORDER BY 1, 3
        """)
        con.execute(f"""
            INSERT INTO humanresources.employee_pay_history
            WITH x AS (
                SELECT e.id, e.hire_date, l.lvl, e.is_sales, unnest(range(1 + ri(e.id, 'npay', 4))) AS k
                FROM _emp e JOIN _lvl l USING (id)
            )
            SELECT id, hire_date + CAST(k * 365 + ri(id * 10 + k, 'pd', 60) AS INTEGER),
                   round(CASE WHEN lvl = 0 THEN 125.0 WHEN is_sales THEN 23.0 WHEN lvl = 1 THEN 60.0
                              WHEN lvl = 2 THEN 45.0 WHEN lvl = 3 THEN 32.0 WHEN lvl = 4 THEN 24.0
                              ELSE 10.0 + 10 * rnd(id, 'base') END * pow(1.04 + 0.03 * rnd(id, 'raise'), k), 4),
                   CASE WHEN lvl <= 3 THEN 2 ELSE 1 END
            FROM x
            WHERE hire_date + CAST(k * 365 + ri(id * 10 + k, 'pd', 60) AS INTEGER) <= DATE '{END_DATE}'
            ORDER BY 1, 2
        """)
        con.execute(f"""
            INSERT INTO sales.sales_person
            WITH sp AS (SELECT id, CAST(row_number() OVER (ORDER BY id) - 1 AS INTEGER) AS seq FROM _emp WHERE is_sales)
            SELECT id,
                   CASE WHEN seq < 3 THEN NULL ELSE 1 + (seq - 3) % 10 END,
                   CASE WHEN seq < 3 THEN NULL ELSE 250000 + 50000 * ri(id, 'quota', 2) END,
                   round(6700 * rnd(id, 'bonus'), 0),
                   round(0.01 + 0.01 * rnd(id, 'comm'), 3)
            FROM sp ORDER BY id
        """)
        con.execute("""
            CREATE TEMP TABLE _sp_idx AS
            SELECT territory_id, business_entity_id,
                   CAST(row_number() OVER (PARTITION BY territory_id ORDER BY business_entity_id) - 1 AS INTEGER) AS seq,
                   CAST(count(*) OVER (PARTITION BY territory_id) AS INTEGER) AS n
            FROM sales.sales_person WHERE territory_id IS NOT NULL
        """)

    def _customers(self, con) -> None:
        s = self.scale
        E, C = s.employees, s.customers
        terr_case = _weighted_case("rnd(k, 'terr')", [(t[0], t[4]) for t in ref.TERRITORIES])
        con.execute(f"""
            CREATE TEMP TABLE _c AS
            SELECT CAST(k AS INTEGER) AS k, CAST(11000 + k AS INTEGER) AS customer_id,
                   CAST({E} + 1 + k AS INTEGER) AS be_id,
                   rnd(k, 'store') < 0.03 AS is_store, {terr_case} AS territory_id
            FROM range({C}) t(k)
        """)
        first, last = _sql_list(ref.FIRST_NAMES), _sql_list(ref.LAST_NAMES)
        con.execute(f"""
            INSERT INTO _people
            SELECT be_id, 'IN', {first}[1 + ri(be_id, 'fn', {len(ref.FIRST_NAMES)})],
                   {last}[1 + ri(be_id, 'ln', {len(ref.LAST_NAMES)})], territory_id
            FROM _c WHERE NOT is_store
        """)
        # addresses: one per person and per store (address_id = business_entity_id)
        streets, suffixes = _sql_list(ref.STREETS), _sql_list(ref.STREET_SUFFIXES)
        con.execute("""
            CREATE TEMP TABLE _st AS
            SELECT sp.territory_id, sp.state_province_id, sp.country_region_code, c.cities,
                   len(string_split(c.cities, '|')) AS n_cities,
                   CAST(row_number() OVER (PARTITION BY sp.territory_id ORDER BY sp.state_province_id) - 1 AS INTEGER) AS seq,
                   CAST(count(*) OVER (PARTITION BY sp.territory_id) AS INTEGER) AS n
            FROM person.state_province sp JOIN _cities c USING (state_province_id)
        """)
        con.execute(f"""
            INSERT INTO person.address
            WITH src AS (
                SELECT be_id, territory_id FROM _people
                UNION ALL SELECT be_id, territory_id FROM _c WHERE is_store
            ),
            tc AS (SELECT DISTINCT territory_id, n FROM _st)
            SELECT src.be_id,
                   CAST(1 + ri(src.be_id, 'no', 9999) AS VARCHAR) || ' ' || {streets}[1 + ri(src.be_id, 'st', {len(ref.STREETS)})]
                       || ' ' || {suffixes}[1 + ri(src.be_id, 'sfx', {len(ref.STREET_SUFFIXES)})],
                   CASE WHEN rnd(src.be_id, 'a2') < 0.08 THEN 'Suite ' || (100 + ri(src.be_id, 'suite', 800)) END,
                   string_split(st.cities, '|')[1 + ri(src.be_id, 'city', st.n_cities)],
                   st.state_province_id,
                   CASE WHEN st.country_region_code = 'US' THEN lpad(CAST(ri(src.be_id, 'zip', 99999) AS VARCHAR), 5, '0')
                        WHEN st.country_region_code = 'GB' THEN 'SW' || (1 + ri(src.be_id, 'zip', 20)) || ' ' || (1 + ri(src.be_id, 'zip2', 9)) || 'AB'
                        ELSE CAST(1000 + ri(src.be_id, 'zip', 89999) AS VARCHAR) END,
                   TIMESTAMP '2020-01-01' + to_days(ri(src.be_id, 'mod', 1800))
            FROM src JOIN tc USING (territory_id)
            JOIN _st st ON st.territory_id = src.territory_id AND st.seq = ri(src.be_id, 'state', tc.n)
            ORDER BY 1
        """)
        con.execute(f"""
            INSERT INTO person.person
            SELECT be_id, person_type,
                   CASE WHEN rnd(be_id, 'title') < 0.08 THEN ['Mr.', 'Ms.', 'Dr.'][1 + ri(be_id, 'tt', 3)] END,
                   first_name,
                   CASE WHEN rnd(be_id, 'mid') < 0.55 THEN chr(65 + ri(be_id, 'mi', 26)) || '.' END,
                   last_name, ri(be_id, 'promo', 3), be_id,
                   TIMESTAMP '2020-01-01' + to_days(ri(be_id, 'mod', 1800))
            FROM _people ORDER BY be_id
        """)
        con.execute("""
            INSERT INTO person.email_address
            SELECT business_entity_id, 1,
                   lower(first_name) || CASE WHEN person_type = 'IN' THEN CAST(business_entity_id AS VARCHAR) || '@example.com'
                                             ELSE CAST(business_entity_id AS VARCHAR) || '@adventure-works.com' END,
                   modified_date
            FROM person.person WHERE rnd(business_entity_id, 'em') >= 0.03
            UNION ALL
            SELECT business_entity_id, 2, lower(first_name) || '.' || lower(replace(last_name, '''', '')) || '@mail.example.org',
                   modified_date + INTERVAL 400 DAY
            FROM person.person WHERE rnd(business_entity_id, 'em') >= 0.03 AND rnd(business_entity_id, 'em2') < 0.02
            ORDER BY 1, 2
        """)
        adj, noun = _sql_list(ref.STORE_ADJECTIVES), _sql_list(ref.STORE_NOUNS)
        con.execute(f"""
            INSERT INTO sales.store
            SELECT c.be_id,
                   {adj}[1 + ri(c.be_id, 'sa', {len(ref.STORE_ADJECTIVES)})] || ' ' || {noun}[1 + ri(c.be_id, 'sn', {len(ref.STORE_NOUNS)})]
                       || CASE WHEN rnd(c.be_id, 'sx') < 0.3 THEN ' #' || (1 + ri(c.be_id, 'sno', 99)) ELSE '' END,
                   sp.business_entity_id, c.be_id
            FROM _c c
            JOIN (SELECT DISTINCT territory_id, n FROM _sp_idx) tn USING (territory_id)
            JOIN _sp_idx sp ON sp.territory_id = c.territory_id AND sp.seq = ri(c.be_id, 'rep', tn.n)
            WHERE c.is_store ORDER BY 1
        """)
        con.execute("""
            INSERT INTO sales.customer
            SELECT customer_id, CASE WHEN NOT is_store THEN be_id END, CASE WHEN is_store THEN be_id END,
                   territory_id,
                   'AW' || lpad(CAST(customer_id AS VARCHAR), 8, '0'),
                   TIMESTAMP '2020-01-01' + to_days(ri(k, 'cmod', 1800))
            FROM _c ORDER BY customer_id
        """)

    def _purchasing(self, con) -> None:
        s = self.scale
        V, P = s.vendors, s.purchase_orders
        vbase = s.employees + s.customers + 1
        pre, suf = _sql_list(ref.VENDOR_PREFIXES), _sql_list(ref.VENDOR_SUFFIXES)
        con.execute(f"""
            INSERT INTO purchasing.vendor
            WITH v AS (
                SELECT CAST(v AS INTEGER) AS v,
                       {pre}[1 + ri(v, 'vp', {len(ref.VENDOR_PREFIXES)})] || ' ' || {suf}[1 + ri(v, 'vs', {len(ref.VENDOR_SUFFIXES)})] AS name
                FROM range({V}) t(v)
            )
            SELECT {vbase} + v, upper(left(replace(name, ' ', ''), 8)) || lpad(CAST(v + 1 AS VARCHAR), 4, '0'),
                   name, 1 + CAST(floor(pow(rnd(v, 'cr'), 2) * 5) AS INTEGER),
                   rnd(v, 'pref') < 0.8, rnd(v, 'act') < 0.95
            FROM v ORDER BY v
        """)
        # each vendor supplies 1-4 consecutive purchasable products (deterministic so POs can match)
        con.execute(f"""
            CREATE TEMP TABLE _pv AS
            SELECT CAST(v AS INTEGER) AS v, CAST(m AS INTEGER) AS m,
                   CAST((v * 7 + m) % (SELECT count(*) FROM _purchasable) AS INTEGER) AS idx
            FROM (SELECT v, unnest(range(1 + ri(v, 'np', 4))) AS m FROM range({V}) t(v))
        """)
        con.execute(f"""
            INSERT INTO purchasing.product_vendor
            SELECT p.product_id, {vbase} + pv.v, 10 + ri(pv.v * 10 + pv.m, 'lead', 110),
                   round(p.standard_cost * (0.9 + 0.2 * rnd(pv.v * 10 + pv.m, 'sp')), 2),
                   1 + ri(pv.v, 'minq', 100), 1000 + ri(pv.v, 'maxq', 1000)
            FROM _pv pv JOIN _purchasable p USING (idx)
            ORDER BY 1, 2
        """)
        con.execute(f"""
            CREATE TEMP TABLE _po AS
            WITH base AS (
                SELECT CAST(k AS INTEGER) AS k, CAST(k + 1 AS INTEGER) AS purchase_order_id,
                       CAST(floor(pow(rnd(k, 'pov'), 1.5) * {V}) AS INTEGER) AS v,
                       DATE '{START_DATE}' + CAST(floor({DAYS} * (k + 0.5) / {P}) AS INTEGER) AS d,
                       1 + ri(k, 'nl', 5) AS n_lines
                FROM range({P}) t(k)
            ),
            buyers AS (
                SELECT business_entity_id AS emp, CAST(row_number() OVER (ORDER BY business_entity_id) - 1 AS INTEGER) AS seq,
                       CAST(count(*) OVER () AS INTEGER) AS n
                FROM humanresources.employee_department_history
                WHERE department_id = {ref.PURCHASING_DEPARTMENT} AND end_date IS NULL
            )
            SELECT b.*, (SELECT count(*) FROM _pv WHERE _pv.v = b.v) AS n_prod,
                   CASE WHEN rnd(k, 'rej') < 0.03 THEN 3 WHEN b.d > DATE '{END_DATE}' - 30 THEN 1 + ri(k, 'pst', 2) ELSE 4 END AS status,
                   (SELECT emp FROM buyers WHERE seq = ri(b.k, 'buyer', (SELECT max(n) FROM buyers))) AS employee_id,
                   CAST(sum(n_lines) OVER (ORDER BY k ROWS UNBOUNDED PRECEDING) - n_lines AS BIGINT) AS line_offset
            FROM base b
        """)
        con.execute(f"""
            INSERT INTO purchasing.purchase_order_detail
            WITH l AS (
                SELECT po.*, unnest(range(n_lines)) AS j FROM _po po
            ), x AS (
                SELECT l.*, CAST(line_offset + j + 1 AS INTEGER) AS detail_id,
                       CAST(3 * (1 + ri(line_offset + j, 'q', 200)) AS INTEGER) AS qty,
                       pv.idx
                FROM l JOIN _pv pv ON pv.v = l.v AND pv.m = ri(l.line_offset + l.j, 'pm', l.n_prod)
            )
            SELECT x.purchase_order_id, x.detail_id, CAST(x.d + 14 AS TIMESTAMP), x.qty, p.product_id,
                   pvd.standard_price, x.qty * pvd.standard_price,
                   CASE WHEN x.status = 4 THEN x.qty ELSE 0 END,
                   CASE WHEN x.status = 4 AND rnd(x.detail_id, 'rj') < 0.05 THEN floor(x.qty * 0.3 * rnd(x.detail_id, 'rjq')) ELSE 0 END,
                   CASE WHEN x.status = 4 THEN x.qty - CASE WHEN rnd(x.detail_id, 'rj') < 0.05 THEN floor(x.qty * 0.3 * rnd(x.detail_id, 'rjq')) ELSE 0 END ELSE 0 END
            FROM x JOIN _purchasable p USING (idx)
            JOIN purchasing.product_vendor pvd ON pvd.product_id = p.product_id AND pvd.business_entity_id = {vbase} + x.v
            ORDER BY 1, 2
        """)
        con.execute(f"""
            INSERT INTO purchasing.purchase_order_header
            SELECT po.purchase_order_id, 1 + ri(po.k, 'rev', 3), po.status, po.employee_id, {vbase} + po.v,
                   1 + ri(po.k, 'sm', 5), CAST(po.d AS TIMESTAMP),
                   CASE WHEN po.status IN (2, 4) THEN CAST(po.d + 9 AS TIMESTAMP) END,
                   t.sub, round(t.sub * 0.08, 4), round(t.sub * 0.02, 4), t.sub + round(t.sub * 0.08, 4) + round(t.sub * 0.02, 4)
            FROM _po po JOIN (SELECT purchase_order_id, sum(line_total) AS sub
                              FROM purchasing.purchase_order_detail GROUP BY 1) t USING (purchase_order_id)
            ORDER BY 1
        """)

    def _orders_stage(self, con) -> None:
        s = self.scale
        N, C = s.orders, s.customers
        con.execute(f"""
            CREATE TEMP TABLE _o AS
            WITH ob AS (
                SELECT CAST(i AS BIGINT) AS i, CAST(43659 + i AS INTEGER) AS sales_order_id,
                       DATE '{START_DATE}' + CAST(floor({DAYS} * sqrt((i + 0.5) / {N})) AS INTEGER) AS d,
                       CAST(11000 + floor({C} * pow(rnd(i, 'cust'), 2.5)) AS INTEGER) AS customer_id
                FROM range({N}) t(i)
            ), j AS (
                SELECT ob.*, c.territory_id, c.is_store, c.be_id AS address_id, st.sales_person_id,
                       CASE WHEN c.is_store THEN 5 + ri(ob.i, 'nl', 30)
                            ELSE 1 + CAST(floor(pow(rnd(ob.i, 'nl'), 2) * 4) AS INTEGER) END AS n_lines
                FROM ob JOIN _c c USING (customer_id)
                LEFT JOIN sales.store st ON st.business_entity_id = c.be_id
            )
            SELECT *, CAST(sum(n_lines) OVER (ORDER BY i ROWS UNBOUNDED PRECEDING) - n_lines AS BIGINT) AS line_offset
            FROM j
        """)

    def _order_lines(self, con) -> None:
        ns = con.execute("SELECT count(*) FROM _sellable").fetchone()[0]
        con.execute(f"""
            INSERT INTO sales.sales_order_detail
            WITH l AS (
                SELECT sales_order_id, is_store, d, line_offset + j + 1 AS detail_id
                FROM (SELECT sales_order_id, is_store, d, line_offset, unnest(range(n_lines)) AS j FROM _o)
            ), x AS (
                SELECT l.*,
                       1 + CAST(floor({ns} * pow(rnd(detail_id, 'prod'), 2)) AS INTEGER) AS prank,
                       CASE WHEN is_store THEN 1 + CAST(floor(pow(rnd(detail_id, 'qty'), 3) * 80) AS INTEGER)
                            WHEN rnd(detail_id, 'qty') < 0.85 THEN 1 ELSE 2 + ri(detail_id, 'qty2', 2) END AS qty
                FROM l
            ), y AS (
                SELECT x.*, s.product_id,
                       round(s.list_price * pow(1.03, year(x.d) - 2024) * CASE WHEN x.is_store THEN 0.6 ELSE 1 END, 2) AS unit_price,
                       CASE WHEN x.is_store AND x.qty >= 61 THEN 6 WHEN x.is_store AND x.qty >= 41 THEN 5
                            WHEN x.is_store AND x.qty >= 25 THEN 4 WHEN x.is_store AND x.qty >= 15 THEN 3
                            WHEN x.is_store AND x.qty >= 11 THEN 2
                            WHEN rnd(detail_id, 'off') < 0.92 THEN 1 ELSE 7 + ri(detail_id, 'off2', 10) END AS special_offer_id
                FROM x JOIN _sellable s USING (prank)
            )
            SELECT y.sales_order_id, y.detail_id,
                   CASE WHEN y.is_store THEN format('{{:04X}}-{{:04X}}-{{:02X}}', hash(y.sales_order_id, 't1') % 65536,
                                                    hash(y.sales_order_id, 't2') % 65536, hash(y.sales_order_id, 't3') % 256) END,
                   y.qty, y.product_id, y.special_offer_id, y.unit_price, so.discount_pct,
                   round(y.qty * y.unit_price * (1 - CAST(so.discount_pct AS DOUBLE)), 4)
            FROM y JOIN sales.special_offer so USING (special_offer_id)
            ORDER BY y.sales_order_id, y.detail_id
        """)

    def _order_headers(self, con) -> None:
        ccy = " ".join(f"WHEN {t} THEN {c}" for t, c in ref.TERRITORY_CURRENCY.items())
        n_ccy = len(ref.RATE_CURRENCIES)
        con.execute(f"""
            INSERT INTO sales.sales_order_header
            WITH o AS (
                SELECT _o.*,
                       CASE WHEN rnd(i, 'cancel') < 0.005 THEN 6
                            WHEN d > DATE '{END_DATE}' - 14 THEN 1 + ri(i, 'st', 4) ELSE 5 END AS status,
                       CASE territory_id {ccy} END AS ccy,
                       d - CASE isodow(d) WHEN 6 THEN 1 WHEN 7 THEN 2 ELSE 0 END AS rate_day
                FROM _o
            ), t AS (
                SELECT sales_order_id, sum(line_total) AS sub FROM sales.sales_order_detail GROUP BY 1
            )
            SELECT o.sales_order_id, 1 + ri(o.i, 'rev', 2), CAST(o.d AS TIMESTAMP), CAST(o.d + 12 AS TIMESTAMP),
                   CASE WHEN o.status = 5 THEN CAST(o.d + 7 AS TIMESTAMP) END, o.status, NOT o.is_store,
                   'SO' || o.sales_order_id,
                   CASE WHEN o.is_store THEN 'PO' || lpad(CAST(hash(o.i, 'po') % 10000000000 AS VARCHAR), 10, '0') END,
                   '10-4030-' || lpad(CAST(o.customer_id AS VARCHAR), 6, '0'),
                   o.customer_id, o.sales_person_id, o.territory_id, o.address_id, o.address_id,
                   CASE WHEN o.is_store THEN {ref.SHIP_METHOD_RESELLER} ELSE {ref.SHIP_METHOD_ONLINE} END,
                   CASE WHEN o.ccy IS NOT NULL THEN CAST((o.rate_day - DATE '{START_DATE}') * {n_ccy} + o.ccy + 1 AS INTEGER) END,
                   t.sub, round(t.sub * 0.08, 4), round(t.sub * 0.025, 4),
                   t.sub + round(t.sub * 0.08, 4) + round(t.sub * 0.025, 4)
            FROM o JOIN t USING (sales_order_id)
            ORDER BY o.sales_order_id
        """)

    def _finalize(self, con) -> None:
        for stmt in constraint_statements(tags={"dim"}):
            con.execute(stmt)
        meta = {"seed_version": SEED_VERSION, "scale": self.scale.key, "orders": str(self.scale.orders),
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "engine": f"duckdb {duckdb.__version__}"}
        _insert_rows(con, "sdps_metadata", list(meta.items()))
        for t in ("_o", "_c", "_po", "_pv", "_emp", "_lvl", "_people", "_st", "_cities", "_sp_idx",
                  "_sellable", "_purchasable"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute("ANALYZE")
        con.execute("CHECKPOINT")

    @staticmethod
    def _row_counts(con) -> dict:
        tables = [r[0] for r in con.execute("""
            SELECT schema_name || '.' || table_name FROM duckdb_tables()
            WHERE database_name = current_database() AND schema_name <> 'main' ORDER BY 1
        """).fetchall()]
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}

    # ---- PostgreSQL load -------------------------------------------------------------------
    def _load_postgres(self, src, pg, counts: dict, tmp: Path) -> None:
        with pg.cursor() as cur:
            self._report(0.57, "Dropping existing AdventureWorks schemas…")
            for schema in SCHEMAS:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cur.execute("DROP TABLE IF EXISTS public.sdps_metadata")
            for stmt in schema_statements():
                cur.execute(stmt)
            cur.execute("CREATE TABLE public.sdps_metadata (key VARCHAR PRIMARY KEY, value VARCHAR)")

        total = max(1, sum(counts.values()))
        loaded = 0
        for table, n in counts.items():
            self._check_cancel()
            self._report(0.58 + 0.32 * loaded / total, f"COPY {table} ({n:,} rows)…")
            csv_path = tmp / f"{table}.csv"
            src.execute(f"COPY (SELECT * FROM {table}) TO '{csv_path.as_posix()}' (FORMAT csv, HEADER true)")
            with pg.cursor() as cur, cur.copy(f"COPY {table} FROM STDIN (FORMAT csv, HEADER true)") as copy, \
                    open(csv_path, "rb") as f:
                while chunk := f.read(4 << 20):
                    if self._cancel.is_set():
                        raise SeedCancelled()
                    copy.write(chunk)
            csv_path.unlink()
            loaded += n

        self._report(0.91, "Adding primary & foreign keys…")
        with pg.cursor() as cur:
            for stmt in constraint_statements(tags={"dim", "fact", "fk"}):
                self._check_cancel()
                cur.execute(stmt)
            meta = src.execute("SELECT key, value FROM sdps_metadata").fetchall()
            meta = [(k, v) for k, v in meta if k != "engine"] + [("engine", "postgres (generated by duckdb)")]
            cur.executemany("INSERT INTO public.sdps_metadata VALUES (%s, %s)", meta)
            self._report(0.96, "ANALYZE…")
            cur.execute("ANALYZE")
        self._report(1.0, "PostgreSQL load complete ✓")
