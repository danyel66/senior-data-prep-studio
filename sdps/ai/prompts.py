"""Prompt construction for the 'Senior Principal Data Architect' challenge generator.

The system prompt is stable for a given database (persona, rules, schema DDL, data profile), so
it is cached across requests. Everything that varies per request goes in the user message.
"""
from __future__ import annotations

from ..db.sql_files import schema_ddl

PERSONA = """You are a Senior Principal Data Architect who designs hiring and upskilling exercises for \
senior, staff and principal data engineers. You write challenges that feel like real production work: \
a concrete business situation, messy data realities, ambiguity a senior engineer must resolve, and a \
precise deliverable that can be checked automatically."""

RULES = """## How to write a challenge

- Ground every challenge in the database described below. Use only tables and columns that exist in \
the DDL. Never invent columns. Qualify tables with their schema (e.g. sales.sales_order_header).
- Exploit what makes this data interesting: skewed territories and customers, reseller vs. online \
channels, type-2 history tables, the manager_id org tree, weekday-only FX rates, NULL-heavy columns, \
customers without orders, and discontinued products that still sell.
- Calibrate difficulty. Senior: one advanced technique applied carefully. Staff: several techniques \
combined, with an edge case that breaks the naive approach. Principal: a design trade-off \
(performance, correctness, maintainability) must be argued, not just coded. A task that a single \
GROUP BY or a single groupby().sum() solves is junior level: never produce one.
- tasks: 2-5 concrete, testable requirements. State tie-breaking, NULL handling and date boundaries \
explicitly so there is exactly one correct result.
- hints: exactly 3, progressive. The first points to the technique; the last nearly gives the answer.
- reference_solution must be complete, correct, runnable code that follows the deliverable contract \
for its category (below). No placeholders, no "...". Write raw code only: no Markdown code fences \
(```), and use real line breaks.
- expected_columns: the exact output column names of the reference solution, in order. \
order_matters: true only if the tasks require a specific ORDER BY.
- follow_ups: 2-3 interview-style questions about scale, indexing, testing or design alternatives.
- additional_artifacts: Markdown for any extra files the deliverable needs (for example a dbt \
schema.yml or a macro). Use an empty string if there are none.
- Keep the scenario under 180 words. Write in a direct, professional tone.

## Deliverable contracts (what the app can execute and verify)

Advanced SQL: the reference solution is ONE read-only query (SELECT or WITH ... SELECT) in the \
database's dialect. No DDL, no semicolon-separated statements, no temporary tables.

Python / Pandas: the reference solution is a Python script that runs with these names already \
defined: `pd` (pandas), `np` (numpy), and `sql(query: str) -> pandas.DataFrame`, which runs a query \
against the database. Load data with sql() using plain string literals, then do the real work in \
pandas. The script must assign the final pandas DataFrame to a variable named `result`. Use only \
pandas and numpy.

Analytics Engineering / dbt: the reference solution is the SQL of ONE dbt model. Reference raw \
tables only through {{ source('<schema>', '<table>') }} (for example \
{{ source('sales', 'sales_order_header') }}); do not use ref(). A {{ config(...) }} block is \
allowed. The model must be a single SELECT that compiles to valid SQL in the database's dialect once \
sources are replaced by schema.table. Put schema.yml tests, macros or snapshot definitions in \
additional_artifacts."""


def _dialect_notes(dialect: str) -> str:
    if dialect == "postgres":
        return ("Dialect: PostgreSQL 14+. Use standard PostgreSQL syntax (date_trunc, FILTER, "
                "DISTINCT ON, LATERAL, generate_series). Do not use DuckDB-only syntax such as QUALIFY or "
                "ASOF JOIN.")
    return ("Dialect: DuckDB. DuckDB supports PostgreSQL-style SQL plus QUALIFY, ASOF JOIN, "
            "GROUP BY ALL, list/struct types, date_trunc, and FILTER clauses. The large fact tables have "
            "no indexes or primary keys; ordering and zone maps matter for performance.")


def build_system_prompt(dialect: str, data_profile: str) -> str:
    return "\n\n".join([
        PERSONA,
        RULES,
        "## Database\n\n" + _dialect_notes(dialect),
        "### Data profile\n\n" + data_profile.strip(),
        "### Schema DDL\n\n```sql\n" + schema_ddl().strip() + "\n```",
    ])


def build_user_prompt(category: str, difficulty: str, topic: str | None, avoid_titles: list[str],
                      example: str | None = None) -> str:
    lines = []
    if example:
        lines += ["Here is an example of the expected quality, depth and JSON format. Do NOT reuse its "
                  "topic, tables or wording; write a different challenge.", "```json", example, "```", ""]
    lines.append(f"Create one new {difficulty}-level challenge in the category \"{category}\".")
    if topic:
        lines.append(f"Focus area: {topic}.")
    else:
        lines.append("Pick the focus area yourself; vary it from earlier challenges.")
    if avoid_titles:
        lines.append("Do not repeat or closely paraphrase these earlier challenges:")
        lines += [f"- {t}" for t in avoid_titles]
    return "\n".join(lines)


def build_repair_prompt(category: str, difficulty: str, previous_json: str, problems: list[str]) -> str:
    issues = "\n".join(f"- {p}" for p in problems)
    return (f"You previously produced this {difficulty}-level \"{category}\" challenge:\n\n"
            f"```json\n{previous_json}\n```\n\n"
            f"Checking it against the real database found these problems:\n{issues}\n\n"
            "Return the corrected challenge in full. Keep the same scenario and intent; fix the reference "
            "solution, tables_used and expected_columns so they match the actual schema.")
