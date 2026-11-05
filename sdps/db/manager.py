"""ConnectionManager: the single place the app goes through to reach the database.

It is Qt-free and synchronous; the UI calls it from background threads (see ui/workers.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .backends import Backend, DatabaseError, QueryResult, Session, TableInfo, create_backend
from .config import DatabaseConfig


@dataclass
class ConnectionStatus:
    connected: bool
    description: str
    server_version: str = ""
    seeded: bool = False
    metadata: dict | None = None
    error: str = ""


class ConnectionManager:
    def __init__(self, config: DatabaseConfig | None = None):
        self.config = config or DatabaseConfig.load()
        self._backend: Backend | None = None

    # ---- lifecycle -------------------------------------------------------------------------
    @property
    def backend(self) -> Backend | None:
        return self._backend

    @property
    def is_connected(self) -> bool:
        return self._backend is not None

    def connect(self, config: DatabaseConfig | None = None) -> ConnectionStatus:
        """(Re)connect, optionally with a new config. Raises DatabaseError on failure."""
        if config is not None:
            self.config = config
        self.disconnect()
        backend = create_backend(self.config)
        backend.connect()
        self._backend = backend
        return self.status()

    def disconnect(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def status(self) -> ConnectionStatus:
        if self._backend is None:
            return ConnectionStatus(False, self.config.describe())
        meta = self._backend.metadata()
        return ConnectionStatus(True, self.config.describe(), self._backend.server_version(),
                                bool(meta), meta)

    @staticmethod
    def test(config: DatabaseConfig) -> str:
        """Open and close a throwaway connection. Returns the server version or raises."""
        backend = create_backend(config)
        backend.connect()
        try:
            return backend.server_version()
        finally:
            backend.close()

    # ---- operations ------------------------------------------------------------------------
    def _require(self) -> Backend:
        if self._backend is None:
            raise DatabaseError("Not connected to a database")
        return self._backend

    def execute(self, sql: str, params=None) -> QueryResult:
        return self._require().execute(sql, params)

    def open_session(self) -> Session:
        return self._require().open_session()

    def introspect(self) -> list[TableInfo]:
        return self._require().introspect()

    def install_duckdb_file(self, new_file: str | os.PathLike) -> ConnectionStatus:
        """Atomically replace the configured DuckDB file with a freshly built one, then reconnect."""
        target = Path(self.config.duckdb_path)
        self.disconnect()                          # Windows cannot replace an open file
        Path(str(target) + ".wal").unlink(missing_ok=True)
        os.replace(new_file, target)
        return self.connect()
