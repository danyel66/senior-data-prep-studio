# Senior Data Prep Studio

Desktop studio (PyQt6) for senior-level SQL, Python/Pandas and analytics-engineering practice
against production-scale data.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | PyQt6 skeleton, split-screen layout, dark theme | ✅ done |
| 2 | DB connection manager + AdventureWorks generator (DuckDB default, Postgres optional) | ✅ done |
| 3 | AI "Senior Principal Data Architect" challenge generator (Claude or Ollama) | ✅ done |
| 4 | Async SQL/Python execution, live telemetry, answer checking | ✅ done |

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

## AI challenges

Go to **Challenge → AI Settings…** and choose a provider:

- **Claude (default).** Uses the Anthropic API. Paste an API key from
  [console.anthropic.com](https://console.anthropic.com/settings/keys). It's stored in your system keychain
  (the macOS Keychain on a Mac), never in a file. `ANTHROPIC_API_KEY` and `ant auth login` also work.
  The model is Claude Opus 5.5 by default, with Sonnet 5.5 and Haiku 5.5 as cheaper options. The dialog shows
  an estimated cost per challenge, and the telemetry log shows the actual cost of each one.
- **Ollama (free, local).** Install from [ollama.com](https://ollama.com), then run
  `ollama pull qwen2.5-coder:14b` (or `qwen2.5-coder:7b` on smaller machines), and select the model in AI Settings.
  Local models are shown a worked example per category, and the app cleans up their common formatting slips (such as
  code wrapped in Markdown fences). Expect 1–5 minutes per challenge, with lower quality than Claude.

Choose a category, a difficulty and optionally a topic, then click **New Challenge**. Each challenge works like
this:

1. **Grounded:** the prompt includes your database's real DDL, row counts, skew and SQL dialect (DuckDB or
   Postgres).
2. **Structured:** the model must return JSON in a strict schema covering scenario, tasks, deliverable,
   expected columns, three progressive hints, the reference solution and interview follow-ups.
3. **Verified:** the reference solution is checked against your database with `EXPLAIN`. SQL is checked
   directly, dbt models after their `source()` calls are rendered, and Python through each of its `sql()`
   queries. If a check fails, the exact database error goes back to the model, with up to 2 automatic repair
   rounds. Challenges that still fail are shown with a ⚠ badge instead of being hidden.

Hints are revealed one at a time. **Insert Solution** puts the answer in the editor, and you can undo that with
Ctrl+Z. All challenges are saved under **Challenge → Recent Challenges**.

Execution contracts that Phase 4 will run:

| Category | Deliverable |
|---|---|
| Advanced SQL | One read-only `SELECT` / `WITH` query |
| Python / Pandas | A script using `pd`, `np` and `sql(query) -> DataFrame` that assigns its answer to `result` |
| Analytics Engineering / dbt | One model `SELECT` using `{{ source('schema', 'table') }}`, plus YAML or macros as artifacts |

## Running and checking your code

- **Run (Ctrl+Enter / F5).** Runs the whole editor, or only the selected text. SQL scripts can have several
  statements (for example `CREATE INDEX …; SELECT …`). The last statement's rows stream into the grid while the
  query is still running, and the window never freezes. The grid shows the first 100,000 rows. Python scripts
  run with `pd`, `np` and `sql("…") -> DataFrame`; whatever you assign to `result` appears in the grid, and
  `print` output goes to the **Output** tab.
- **Cancel.** Stops a running query or Python script within a fraction of a second, including infinite loops.
- **Telemetry.** Shows execution time, rows returned, and peak process memory with how much the run added,
  updated live while the run is in progress.
- **`EXPLAIN` / `EXPLAIN ANALYZE`.** Plans appear in the **Plan** tab. On DuckDB, look for lines like
  `Row Groups Scanned: 5 / 9`, which show zone maps skipping data.
- **Check (Ctrl+Shift+Enter).** Compares your answer with the challenge's reference solution:
  - SQL and dbt answers are compared inside the database with `EXCEPT ALL` in both directions, so results of
    any size are checked without loading them into memory.
  - Python DataFrames are compared row by row using hashes.
  - Numbers are compared at 4 decimal places. Column order doesn't matter when the names match, and row order
    is checked only when the task requires it.
  - The **Check** tab shows the verdict, row counts, and sample rows that are missing or shouldn't be there.
- **Schema refresh.** DDL (`CREATE`, `DROP`, …) refreshes the schema browser automatically.
- **Memory limit.** DuckDB is capped at half your RAM by default (4 GB on an 8 GB Mac), so pandas and macOS
  have room. You can change this in Database → Connection Settings.

## Troubleshooting (macOS)

| Symptom | Cause | Fix |
|---|---|---|
| `Could not find the Qt platform plugin "cocoa"` (comes and goes) | The virtual environment is in an iCloud-synced folder (`~/Documents`, `~/Desktop`), and iCloud evicts library files | Create the environment outside synced folders: `python3.12 -m venv ~/.venvs/sdps` |
| `No matching distribution found for anthropic` | Python 3.9 (macOS built-in) is too old | Install Python 3.12 (`brew install python@3.12`) and recreate the environment |
| "Database file is in use by another program" | Another copy of the app, or a tool like DBeaver, has the DuckDB file open | Quit it, or `kill <PID>` using the process number shown in the message |
| The app runs but no window is visible | A window position saved with a different display arrangement | Fixed automatically since v0.3.1; otherwise use Mission Control |

The app checks the first three at startup and prints the fix instead of crashing.

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
│   │   ├── dialogs/                 # Database settings, Generate data, AI settings
│   │   └── widgets/                 # schema browser, editor, prompt, results grid, telemetry
│   ├── ai/                          # Qt-free challenge generator
│   │   ├── challenge.py             # Challenge model, JSON schema, Markdown rendering, local library
│   │   ├── prompts.py               # persona, rules, deliverable contracts, schema-grounded prompts
│   │   ├── providers.py             # Claude (SDK, streaming, structured output, caching) + Ollama
│   │   ├── generator.py             # grounding, EXPLAIN verification, repair loop
│   │   └── config.py                # provider settings; API key in the system keychain
│   └── execution/                   # Qt-free execution engine
│       ├── engine.py                # Executor: SQL streaming, Python runner, cancel, memory sampler
│       ├── check.py                 # answer checking (SQL in-database, Python DataFrames)
│       └── sqltools.py              # statement splitter, dbt source() rendering
└── tests/                           # UI smoke tests, data-integrity tests, Postgres tests
```

## Shortcuts

| Key | Action |
|---|---|
| Ctrl+Enter / F5 | Run current script (or selection) |
| Ctrl+Shift+Enter | Check your answer against the challenge |
| Ctrl+Shift+L | Toggle SQL ↔ Python |
| Ctrl+B | Show/hide schema browser |
| F6 | Refresh schema |
| Ctrl+, | Database connection settings |
| Ctrl+Shift+G | Generate AdventureWorks data |
| Ctrl+G | New AI challenge |
| Ctrl+Shift+H | Reveal next hint |
| Ctrl+Shift+S | Show solution |
| Ctrl+D | Load 1M-row demo grid |
| Ctrl+L | Clear results |

In the schema browser, double-click a table or column to insert its name into the editor. Right-click a table for
`SELECT … LIMIT 100` and row-count snippets.
