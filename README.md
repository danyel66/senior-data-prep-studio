# Senior Data Prep Studio

Desktop studio (PyQt6) for senior-level SQL, Python/Pandas and analytics-engineering practice
against production-scale data.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | PyQt6 skeleton, split-screen layout, dark theme | ✅ done |
| 2 | DB connection manager + AdventureWorks generator (DuckDB default, Postgres optional) | ✅ done |
| 3 | AI "Senior Principal Data Architect" problem generator | ⏳ |
| 4 | Async SQL/Python execution (QThread workers) | ⏳ |

## Run

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

On first launch the app creates an empty DuckDB file and offers to generate the dataset
(Medium, about 5 seconds). You can regenerate at any size later via **Database → Generate AdventureWorks Data…**.

## Database

**DuckDB (default):** an embedded file at
`~/.local/share/SeniorDataPrepStudio/adventureworks.duckdb`
(macOS: `~/Library/Application Support/…`, Windows: `%APPDATA%\…`). There's nothing to install.

**PostgreSQL (optional):** go to **Database → Connection Settings…**, choose PostgreSQL, enter
host/port/db/user, and click *Test Connection*. Then use **Generate AdventureWorks Data…**, which builds the data
in DuckDB and streams it into Postgres with `COPY`, then adds all primary and foreign keys. The password is
held in memory for the session only; to avoid retyping it, use `PGPASSWORD` or a `.pgpass` file.

### Dataset sizes (measured on 4 cores)

| Scale | Orders | Order lines | DuckDB build | Postgres load | Disk (DuckDB) |
|---|---|---|---|---|---|
| Tiny | 5K | 12K | <1 s | ~1 s | 5 MB |
| Small | 100K | 240K | ~1 s | ~5 s | 20 MB |
| **Medium** | **1M** | **2.4M** | **~5 s** | **~26 s** | **100 MB** |
| Large | 10M | 24M | ~32 s | a few min | 860 MB |
| XL | 30M | 73M | ~2 min | longer | ~2.6 GB |

### Schema

AdventureWorks adapted to `snake_case`, 29 tables across `person`, `humanresources`, `production`,
`purchasing` and `sales`. Every table has a description, which you can see as a tooltip in the schema browser.
The DDL is in [`sdps/db/sql/schema.sql`](sdps/db/sql/schema.sql) and keys are in
[`constraints.sql`](sdps/db/sql/constraints.sql).

The data is built to be realistic, and some properties are worth discovering on your own:

- **Skew:** territory, customer and product volumes all follow heavy-tailed distributions, and there are
  reseller "whales".
- **Growth:** order volume rises steadily from 2021 to 2024.
- **Two sales channels:** online individual orders (no sales person) and reseller orders (many lines,
  volume discounts, sales person attached).
- **Type-2 history:** `product_list_price_history` and `employee_department_history`.
- **Org tree:** `humanresources.employee.manager_id` forms the reporting hierarchy, so use recursive CTEs.
- **FX rates:** rates exist on weekdays only, so you need an as-of join.
- **Quirks:** NULL-heavy columns, customers who never ordered, people with zero or two emails, discontinued
  products that still appear on later orders.
- **Unindexed facts:** on DuckDB the fact tables have no keys or indexes, so indexing is left for you to
  practise. On Postgres every PK and FK is enforced.

The generator is deterministic: the same scale always produces the same data.

## Tests

```bash
pip install pytest
pytest -q
# include PostgreSQL tests:
SDPS_TEST_PG="host=localhost port=5432 user=postgres dbname=aw_test" pytest -q
```

Tests run headless, use a temporary app-data directory and never touch your real database.

## Layout

```
senior-data-prep-studio/
├── main.py
├── sdps/
│   ├── app.py
│   ├── db/                          # Qt-free database layer
│   │   ├── config.py                # DatabaseConfig (JSON in app-data dir, password never saved)
│   │   ├── backends.py              # Backend/Session interface, DuckDB + PostgreSQL implementations
│   │   ├── manager.py               # ConnectionManager: connect, introspect, sessions, file swap
│   │   ├── seed.py                  # set-based AdventureWorks generator + Postgres COPY loader
│   │   ├── reference_data.py        # territories, departments, products, name pools…
│   │   └── sql/{schema,constraints}.sql
│   ├── ui/
│   │   ├── main_window.py           # layout, Database menu, connection status, first-run prompt
│   │   ├── workers.py               # run_task(): background QThread with progress/cancel
│   │   ├── dialogs/                 # Database settings, Generate data
│   │   └── widgets/                 # schema browser, editor, prompt, results grid, telemetry
│   ├── ai/                          # Phase 3
│   └── execution/                   # Phase 4
└── tests/                           # UI smoke tests, data-integrity tests, Postgres tests
```

## Shortcuts

| Key | Action |
|---|---|
| Ctrl+Enter / F5 | Run current script (or selection) |
| Ctrl+Shift+L | Toggle SQL ↔ Python |
| Ctrl+B | Show/hide schema browser |
| F6 | Refresh schema |
| Ctrl+, | Database connection settings |
| Ctrl+Shift+G | Generate AdventureWorks data |
| Ctrl+G | New AI challenge |
| Ctrl+D | Load 1M-row demo grid |
| Ctrl+L | Clear results |

In the schema browser, double-click a table or column to insert its name into the editor. Right-click a table for
`SELECT … LIMIT 100` and row-count snippets.
