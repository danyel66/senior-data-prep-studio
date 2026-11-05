"""ChallengeGenerator: grounds the LLM in the live database, then verifies what comes back.

Verification (against the connected database, read-only):
  * every schema.table mentioned exists
  * Advanced SQL: `EXPLAIN <reference query>` succeeds (catches bad columns, syntax, dialect)
  * dbt: sources are rendered to schema.table, then EXPLAIN
  * Python: the script parses, assigns `result`, and every sql("...") literal passes EXPLAIN
Failures are sent back to the model for up to MAX_REPAIRS correction rounds.
"""
from __future__ import annotations

import ast
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..db import ConnectionManager, DatabaseError, TableInfo
from .challenge import CHALLENGE_SCHEMA, Challenge, ChallengeFormatError, ChallengeStore
from .config import AIConfig
from .prompts import build_repair_prompt, build_system_prompt, build_user_prompt
from .providers import AICancelled, AIError, Completion, Provider, create_provider

MAX_REPAIRS = 2
_SCHEMAS = ("person", "humanresources", "production", "purchasing", "sales")
# only names right after FROM/JOIN, so aliases like `person.first_name` aren't mistaken for tables
_TABLE_REF = re.compile(r"\b(?:from|join)\s+(" + "|".join(_SCHEMAS) + r")\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
_JINJA_BLOCK = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


@dataclass
class GenerationResult:
    challenge: Challenge
    attempts: int
    completions: list[Completion] = field(default_factory=list)

    @property
    def cost_usd(self) -> float | None:
        costs = [c.cost_usd for c in self.completions]
        return None if any(c is None for c in costs) else sum(costs)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.completions)


def build_data_profile(manager: ConnectionManager, tables: list[TableInfo]) -> str:
    meta = manager.backend.metadata() if manager.backend else {}
    lines = []
    if meta:
        lines.append(f"Dataset scale: {meta.get('scale', '?')} ({int(meta.get('orders', 0)):,} sales orders), "
                     "orders span 2021-01-01 to 2024-12-31.")
    big = sorted((t for t in tables if t.row_count), key=lambda t: -t.row_count)[:12]
    if big:
        lines.append("Largest tables (approximate rows): " +
                     ", ".join(f"{t.qualified_name} {t.row_count:,}" for t in big) + ".")
    try:
        rows = manager.execute("""
            SELECT t.name, round(100.0 * count(*) / sum(count(*)) OVER (), 1)
            FROM sales.sales_order_header h JOIN sales.sales_territory t USING (territory_id)
            GROUP BY t.name ORDER BY 2 DESC LIMIT 3""").rows
        if rows:
            lines.append("Order share by territory (top 3): " + ", ".join(f"{n} {p}%" for n, p in rows) + ".")
    except DatabaseError:
        pass
    return "\n".join(f"- {line}" for line in lines) or "- (no profile available)"


class ChallengeGenerator:
    def __init__(self, manager: ConnectionManager, config: AIConfig, store: ChallengeStore | None = None,
                 provider: Provider | None = None):
        self.manager = manager
        self.config = config
        self.store = store
        self.provider = provider or create_provider(config)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()
        self.provider.interrupt()

    def generate(self, category: str, difficulty: str, topic: str | None = None,
                 progress: Callable[[str], None] | None = None) -> GenerationResult:
        progress = progress or (lambda m: None)
        if not self.manager.is_connected:
            raise AIError("Connect to a database first: challenges are built from its schema.")
        t0 = time.monotonic()
        progress("Reading your database schema…")
        tables = self.manager.introspect()
        known = {t.qualified_name.lower(): t for t in tables}
        if not any(name.startswith("sales.") for name in known):
            raise AIError("The AdventureWorks tables aren't in this database yet. "
                          "Use Database → Generate AdventureWorks Data… first.")
        dialect = self.manager.config.backend
        system = build_system_prompt(dialect, build_data_profile(self.manager, tables))
        avoid = self.store.recent_titles(category) if self.store else []
        user = build_user_prompt(category, difficulty, topic, avoid)

        completions: list[Completion] = []
        challenge: Challenge | None = None
        problems: list[str] = []
        previous_json = ""
        for attempt in range(1 + MAX_REPAIRS):
            if self._cancel.is_set():
                raise AICancelled()
            if attempt:
                progress(f"Fixing {len(problems)} problem(s) found during verification (round {attempt})…")
                prompt = build_repair_prompt(category, difficulty, previous_json, problems)
            else:
                prompt = user
            completion = self.provider.complete(system, prompt, CHALLENGE_SCHEMA, progress, self._cancel)
            completions.append(completion)
            try:
                candidate = Challenge.from_model_output(completion.text)
            except ChallengeFormatError as e:
                problems = [f"The output did not match the required format: {e}"]
                previous_json = completion.text[:20000]
                continue
            # keep the requested category/difficulty even if the model drifted
            candidate.category, candidate.difficulty = category, difficulty
            progress("Verifying the reference solution against your database…")
            problems = self.verify(candidate, known)
            challenge = candidate
            previous_json = candidate.model_json()
            if not problems:
                break

        if challenge is None:
            raise AIError("The model didn't return a usable challenge. " + "; ".join(problems), retryable=True)
        challenge.provider = self.provider.name
        challenge.model = completions[-1].model
        challenge.dialect = dialect
        challenge.verified = not problems
        challenge.verification_notes = problems
        challenge.generation_seconds = round(time.monotonic() - t0, 1)
        result = GenerationResult(challenge, len(completions), completions)
        challenge.cost_usd = result.cost_usd
        return result               # the caller decides whether to save it (see ChallengeStore.add)

    # ---- verification -----------------------------------------------------------------------
    def verify(self, ch: Challenge, known: dict[str, TableInfo]) -> list[str]:
        problems: list[str] = []
        mentioned = {t.lower() for t in ch.tables_used}
        mentioned |= {f"{s}.{t}".lower() for s, t in _TABLE_REF.findall(ch.reference_solution)}
        mentioned |= {f"{s}.{t}".lower() for s, t in _SOURCE.findall(ch.reference_solution)}
        for name in sorted(mentioned):
            if name not in known:
                problems.append(f"Table '{name}' does not exist.")
        if problems:
            return problems

        if ch.category == "Python / Pandas":
            return problems + self._verify_python(ch.reference_solution)
        sql = ch.reference_solution
        if ch.category == "Analytics Engineering / dbt":
            if "ref(" in sql:
                return ["The dbt model must use source() only, not ref()."]
            sql = _SOURCE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", sql)
            sql = _JINJA_BLOCK.sub("", sql)
        error = self._explain(sql)
        return problems + ([f"The reference solution fails on the database: {error}"] if error else [])

    def _verify_python(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"The Python reference solution has a syntax error on line {e.lineno}: {e.msg}"]
        problems = []
        assigns_result = any(
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
                isinstance(t, ast.Name) and t.id == "result"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
            for node in ast.walk(tree))
        if not assigns_result:
            problems.append("The Python reference solution never assigns the final DataFrame to `result`.")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sql"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                if error := self._explain(node.args[0].value):
                    problems.append(f"A sql() query in the Python solution fails: {error}")
        return problems

    def _explain(self, sql: str) -> str | None:
        statement = sql.strip().rstrip(";").strip()
        if ";" in statement:
            return "the solution must be a single statement (found ';' separating statements)"
        if not re.match(r"(?is)^\s*(with|select|from|\()", statement):
            return "the solution must be a read-only SELECT / WITH query"
        session = self.manager.open_session()
        try:
            session.execute(f"EXPLAIN {statement}")
            return None
        except DatabaseError as e:
            return str(e).splitlines()[0][:400]
        finally:
            session.close()
