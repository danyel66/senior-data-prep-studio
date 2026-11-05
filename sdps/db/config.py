"""Database configuration, persisted as JSON in the per-user app data directory.

The Postgres password is deliberately never written to disk: it lives in memory for the session,
or comes from libpq's standard sources (PGPASSWORD, ~/.pgpass / %APPDATA%\\postgresql\\pgpass.conf).
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR_NAME = "SeniorDataPrepStudio"


def app_data_dir() -> Path:
    """Per-user data dir. Override with SDPS_HOME (used by tests)."""
    if override := os.environ.get("SDPS_HOME"):
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_DIR_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_duckdb_path() -> str:
    return str(app_data_dir() / "adventureworks.duckdb")


def _default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "postgres"


@dataclass
class DatabaseConfig:
    backend: str = "duckdb"                     # "duckdb" | "postgres"
    # DuckDB
    duckdb_path: str = field(default_factory=default_duckdb_path)
    duckdb_memory_limit: str = ""               # e.g. "4GB"; empty = DuckDB default (80% of RAM)
    duckdb_threads: int = 0                     # 0 = DuckDB default (all cores)
    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "adventureworks"
    pg_user: str = field(default_factory=_default_user)
    pg_sslmode: str = "prefer"
    pg_password: str = field(default="", repr=False)   # session-only, never persisted

    _NOT_PERSISTED = ("pg_password",)

    def pg_conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo
        params = dict(host=self.pg_host, port=self.pg_port, dbname=self.pg_database, user=self.pg_user,
                      sslmode=self.pg_sslmode, application_name="Senior Data Prep Studio")
        if self.pg_password:
            params["password"] = self.pg_password
        return make_conninfo(**params)

    def describe(self) -> str:
        if self.backend == "postgres":
            return f"PostgreSQL · {self.pg_user}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        return f"DuckDB · {Path(self.duckdb_path).name}"

    # ---- persistence -----------------------------------------------------------------------
    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "DatabaseConfig":
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8")).get("database", {})
        except (OSError, ValueError):
            raw = {}
        known = {f.name for f in fields(cls)} - set(cls._NOT_PERSISTED)
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        if cfg.backend not in ("duckdb", "postgres"):
            cfg.backend = "duckdb"
        return cfg

    def save(self) -> None:
        path = self.path()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        doc["database"] = {k: v for k, v in asdict(self).items() if k not in self._NOT_PERSISTED}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)
