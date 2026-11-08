"""Small, dependency-free SQL helpers shared by the execution engine, the checker and the AI verifier."""
from __future__ import annotations

import re

SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_ROW_RETURNING = re.compile(r"^\s*(\(?\s*select|with|values|table|explain|describe|show|summarize|pivot|unpivot|from)\b",
                            re.IGNORECASE)
_DDL = re.compile(r"^\s*(create|drop|alter|rename|truncate|comment|attach|detach|insert|update|delete|merge|copy|"
                  r"import|load|checkpoint|vacuum|analyze)\b", re.IGNORECASE)
_LEADING_COMMENTS = re.compile(r"^(\s*(--[^\n]*\n?|/\*.*?\*/))*", re.DOTALL)


def strip_leading_comments(sql: str) -> str:
    return _LEADING_COMMENTS.sub("", sql, count=1).lstrip()


def render_dbt(sql: str) -> str:
    """Compile the dbt subset challenges use: source('s','t') -> s.t; drop config/other Jinja."""
    sql = SOURCE_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", sql)
    return _JINJA_RE.sub("", sql).strip()


def has_jinja(sql: str) -> bool:
    return "{{" in sql or "{%" in sql


def split_statements(script: str) -> list[str]:
    """Split on top-level semicolons, respecting quotes, comments and $$-quoted bodies."""
    out, buf, i, n = [], [], 0, len(script)
    while i < n:
        ch = script[i]
        two = script[i:i + 2]
        if two == "--":
            j = script.find("\n", i)
            j = n if j < 0 else j
            buf.append(script[i:j])
            i = j
        elif two == "/*":
            j = script.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf.append(script[i:j])
            i = j
        elif ch in ("'", '"'):
            j = i + 1
            while j < n:
                if script[j] == ch:
                    if j + 1 < n and script[j + 1] == ch:   # escaped quote ('' or "")
                        j += 2
                        continue
                    break
                j += 1
            buf.append(script[i:j + 1])
            i = j + 1
        elif ch == "$" and (m := re.match(r"\$[A-Za-z_]*\$", script[i:])):
            tag = m.group(0)
            j = script.find(tag, i + len(tag))
            j = n if j < 0 else j + len(tag)
            buf.append(script[i:j])
            i = j
        elif ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if strip_leading_comments(s).strip()]


def returns_rows(statement: str) -> bool:
    return bool(_ROW_RETURNING.match(strip_leading_comments(statement)))


def changes_schema(statement: str) -> bool:
    return bool(_DDL.match(strip_leading_comments(statement)))


def is_plan(columns: list[str]) -> bool:
    """EXPLAIN output (DuckDB: explain_key/explain_value; Postgres: QUERY PLAN)."""
    lowered = [c.lower() for c in columns]
    return lowered in (["explain_key", "explain_value"], ["query plan"], ["explain_value"])
