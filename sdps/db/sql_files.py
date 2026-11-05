"""Loads the bundled DDL files and splits them into executable statements."""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

SQL_DIR = Path(__file__).with_name("sql")


@cache
def _read(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def schema_ddl() -> str:
    """Full schema DDL text (also handy as LLM context in Phase 3)."""
    return _read("schema.sql")


def schema_statements() -> list[str]:
    body = "\n".join(line for line in schema_ddl().splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*$", body, flags=re.MULTILINE) if s.strip()]


def constraint_statements(tags: set[str]) -> list[str]:
    out = []
    for line in _read("constraints.sql").splitlines():
        m = re.match(r"^(ALTER .*?);\s*--\s*\[(\w+)\]\s*$", line.strip())
        if m and m.group(2) in tags:
            out.append(m.group(1))
    return out
