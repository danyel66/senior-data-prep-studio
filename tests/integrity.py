"""Data-integrity checks shared by tests (DuckDB or Postgres via a `scalar(sql)` callable)."""
import re

from sdps.db.sql_files import constraint_statements

_PK = {}


def _primary_keys() -> dict[str, str]:
    if not _PK:
        for s in constraint_statements({"dim", "fact"}):
            t, cols = re.match(r"ALTER TABLE (\S+) ADD PRIMARY KEY \((.*)\)", s).groups()
            _PK[t] = cols
    return _PK


def orphan_report(scalar) -> dict[str, int]:
    out = {}
    for s in constraint_statements({"fk"}):
        child, col, parent = re.match(r"ALTER TABLE (\S+) ADD FOREIGN KEY \((\w+)\) REFERENCES (\S+)", s).groups()
        pk = _primary_keys()[parent]
        n = scalar(f"SELECT count(*) FROM {child} c WHERE c.{col} IS NOT NULL AND NOT EXISTS "
                   f"(SELECT 1 FROM {parent} p WHERE p.{pk} = c.{col})")
        if n:
            out[f"{child}.{col} -> {parent}"] = n
    return out


def duplicate_pk_report(scalar) -> dict[str, int]:
    out = {}
    for t, cols in _primary_keys().items():
        n = scalar(f"SELECT count(*) FROM (SELECT {cols} FROM {t} GROUP BY {cols} HAVING count(*) > 1) d")
        if n:
            out[t] = n
    return out
