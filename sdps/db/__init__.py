"""Database layer: config, DuckDB/Postgres backends, connection manager and AdventureWorks seeder.

Qt-free on purpose so it can be tested and reused (e.g. from the Phase 4 execution workers).
"""
from .backends import ColumnInfo, DatabaseError, QueryResult, RowCursor, Session, TableInfo
from .config import DatabaseConfig, app_data_dir
from .manager import ConnectionManager, ConnectionStatus
from .seed import DEFAULT_SCALE, SCALES, Scale, SeedCancelled, Seeder

__all__ = ["ColumnInfo", "RowCursor", "ConnectionManager", "ConnectionStatus", "DatabaseConfig", "DatabaseError",
           "DEFAULT_SCALE", "QueryResult", "SCALES", "Scale", "SeedCancelled", "Seeder", "Session",
           "TableInfo", "app_data_dir"]
