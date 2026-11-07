"""Database backends behind one small interface.

A *Backend* owns the primary connection (used on the UI thread for quick metadata calls).
A *Session* is an independent connection handle for one worker thread (Phase 4 executes queries
on sessions so a long query never blocks the UI and can be interrupted).
"""
from __future__ import annotations

import threading
import time
import weakref
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .config import DatabaseConfig


class DatabaseError(Exception):
    """Any connection or query failure, with a user-presentable message."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    elapsed_ms: float
    rowcount: int = -1


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool


@dataclass
class TableInfo:
    schema: str
    name: str
    kind: str                  # "table" | "view"
    row_count: int | None
    comment: str | None
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


_COLUMNS_SQL = """
    SELECT table_schema, table_name, column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
      AND table_name <> 'sdps_metadata'
      AND table_catalog = current_database()
    ORDER BY table_schema, table_name, ordinal_position
"""


class Session(ABC):
    @abstractmethod
    def execute(self, sql: str, params: Any = None) -> QueryResult: ...

    @abstractmethod
    def interrupt(self) -> None:
        """Cancel the statement running on this session (callable from any thread)."""

    @abstractmethod
    def close(self) -> None: ...


class Backend(ABC):
    kind: str

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._sessions: "weakref.WeakSet[Session]" = weakref.WeakSet()
        self._lock = threading.RLock()

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def _close_primary(self) -> None: ...

    @abstractmethod
    def _new_session(self) -> Session: ...

    @abstractmethod
    def server_version(self) -> str: ...

    @abstractmethod
    def _table_stats(self) -> dict[tuple[str, str], tuple[str, int | None, str | None]]: ...

    @property
    @abstractmethod
    def primary(self) -> Session: ...

    # ---- shared behaviour ------------------------------------------------------------------
    def execute(self, sql: str, params: Any = None) -> QueryResult:
        with self._lock:
            return self.primary.execute(sql, params)

    def open_session(self) -> Session:
        session = self._new_session()
        self._sessions.add(session)
        return session

    def close(self) -> None:
        for s in list(self._sessions):
            try:
                s.interrupt()
                s.close()
            except Exception:
                pass
        self._close_primary()

    def introspect(self) -> list[TableInfo]:
        stats = self._table_stats()
        tables: dict[tuple[str, str], TableInfo] = {}
        for schema, table, col, dtype, nullable in self.execute(_COLUMNS_SQL).rows:
            key = (schema, table)
            if key not in tables:
                kind, rows, comment = stats.get(key, ("view", None, None))
                tables[key] = TableInfo(schema, table, kind, rows, comment)
            tables[key].columns.append(ColumnInfo(col, dtype.lower(), nullable == "YES"))
        return sorted(tables.values(), key=lambda t: (t.schema, t.name))

    def metadata(self) -> dict[str, str]:
        """Seed metadata, or {} if the database has not been seeded by this app."""
        try:
            return dict(self.execute("SELECT key, value FROM sdps_metadata").rows)
        except DatabaseError:
            return {}

    def is_seeded(self) -> bool:
        return bool(self.metadata())


# ───────────────────────────────────────── DuckDB ──────────────────────────────────────────
class DuckDBSession(Session):
    def __init__(self, con):
        self._con = con

    def execute(self, sql: str, params: Any = None) -> QueryResult:
        import duckdb
        t0 = time.perf_counter()
        try:
            cur = self._con.execute(sql, params) if params is not None else self._con.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall() if columns else []   # always drain: DuckDB 2 blocks on unfinished results
        except duckdb.Error as e:
            raise DatabaseError(str(e).strip()) from e
        return QueryResult(columns, rows, (time.perf_counter() - t0) * 1000, len(rows))

    def interrupt(self) -> None:
        try:
            self._con.interrupt()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass


class DuckDBBackend(Backend):
    kind = "duckdb"

    def __init__(self, config: DatabaseConfig):
        super().__init__(config)
        self._con = None
        self._primary: DuckDBSession | None = None

    def connect(self) -> None:
        import duckdb
        settings: dict[str, Any] = {}
        if self.config.duckdb_memory_limit:
            settings["memory_limit"] = self.config.duckdb_memory_limit
        if self.config.duckdb_threads:
            settings["threads"] = int(self.config.duckdb_threads)
        try:
            self._con = duckdb.connect(self.config.duckdb_path, config=settings)
        except duckdb.IOException as e:
            msg = str(e)
            if "lock" in msg.lower():
                import re
                pid = re.search(r"PID (\d+)", msg)
                who = f" (process {pid.group(1)})" if pid else ""
                msg = (f"The database file is in use by another program{who}. Quit other copies of Senior Data "
                       f"Prep Studio or tools like DBeaver, or run: kill {pid.group(1) if pid else '<PID>'}"
                       f"\n\n{msg}")
            raise DatabaseError(msg) from e
        except duckdb.Error as e:
            raise DatabaseError(str(e)) from e
        self._primary = DuckDBSession(self._con)

    @property
    def primary(self) -> Session:
        if self._primary is None:
            raise DatabaseError("Not connected")
        return self._primary

    def _new_session(self) -> Session:
        if self._con is None:
            raise DatabaseError("Not connected")
        return DuckDBSession(self._con.cursor())     # same database instance, independent connection

    def _close_primary(self) -> None:
        if self._con is not None:
            self._con.close()
        self._con = self._primary = None

    def server_version(self) -> str:
        import duckdb
        return f"DuckDB {duckdb.__version__}"

    def _table_stats(self):
        out = {}
        for schema, name, rows, comment in self.execute(
                "SELECT schema_name, table_name, estimated_size, comment FROM duckdb_tables() "
                "WHERE database_name = current_database()").rows:
            out[(schema, name)] = ("table", rows, comment or None)
        for schema, name, comment in self.execute(
                "SELECT schema_name, view_name, comment FROM duckdb_views() "
                "WHERE database_name = current_database() AND NOT internal").rows:
            out[(schema, name)] = ("view", None, comment or None)
        return out


# ──────────────────────────────────────── PostgreSQL ───────────────────────────────────────
class PostgresSession(Session):
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: Any = None) -> QueryResult:
        import psycopg
        t0 = time.perf_counter()
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [d.name for d in cur.description] if cur.description else []
                rows = cur.fetchall() if columns else []
                rowcount = cur.rowcount
        except psycopg.Error as e:
            raise DatabaseError(str(e).strip()) from e
        return QueryResult(columns, rows, (time.perf_counter() - t0) * 1000, rowcount)

    def interrupt(self) -> None:
        try:
            self._conn.cancel_safe(timeout=3)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class PostgresBackend(Backend):
    kind = "postgres"

    def __init__(self, config: DatabaseConfig):
        super().__init__(config)
        self._primary: PostgresSession | None = None

    def _connect_raw(self):
        try:
            import psycopg
        except ImportError as e:
            raise DatabaseError("PostgreSQL support needs the 'psycopg' package: pip install \"psycopg[binary]\"") from e
        try:
            return psycopg.connect(self.config.pg_conninfo(), autocommit=True, connect_timeout=5)
        except psycopg.Error as e:
            raise DatabaseError(str(e).strip()) from e

    def connect(self) -> None:
        self._primary = PostgresSession(self._connect_raw())

    @property
    def primary(self) -> Session:
        if self._primary is None:
            raise DatabaseError("Not connected")
        return self._primary

    def _new_session(self) -> Session:
        return PostgresSession(self._connect_raw())

    def _close_primary(self) -> None:
        if self._primary is not None:
            self._primary.close()
        self._primary = None

    def server_version(self) -> str:
        return "PostgreSQL " + self.execute("SHOW server_version").rows[0][0]

    def _table_stats(self):
        out = {}
        for schema, name, relkind, rows, comment in self.execute("""
                SELECT n.nspname, c.relname, c.relkind, c.reltuples::bigint, obj_description(c.oid, 'pg_class')
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'v', 'm')
                  AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')""").rows:
            kind = "view" if relkind in ("v", "m") else "table"
            out[(schema, name)] = (kind, None if kind == "view" or rows < 0 else rows, comment)
        return out

    def metadata(self) -> dict[str, str]:
        try:
            return dict(self.execute("SELECT key, value FROM public.sdps_metadata").rows)
        except DatabaseError:
            return {}


def create_backend(config: DatabaseConfig) -> Backend:
    return PostgresBackend(config) if config.backend == "postgres" else DuckDBBackend(config)
