import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HOME = tempfile.mkdtemp(prefix="sdps_test_home_")
os.environ["SDPS_HOME"] = _HOME           # never touch the real user config / database
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from sdps.db import DatabaseConfig, Seeder  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtCore import QCoreApplication, QSettings
    from sdps.app import create_app
    QCoreApplication.setOrganizationName("SDPS-tests")       # isolate QSettings from the real app
    app = create_app([])
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, _HOME)
    yield app


@pytest.fixture(scope="session")
def tiny_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "tiny.duckdb"
    Seeder("tiny").build_duckdb(path)
    return path


@pytest.fixture
def tiny_config(tiny_db, tmp_path) -> DatabaseConfig:
    """A private copy of the tiny DB so tests can't interfere with each other."""
    copy = tmp_path / "aw.duckdb"
    copy.write_bytes(tiny_db.read_bytes())
    return DatabaseConfig(duckdb_path=str(copy))


def wait_until(predicate, timeout: float = 15.0) -> None:
    from PyQt6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise TimeoutError("condition not met")


def pg_conninfo() -> str | None:
    """Set SDPS_TEST_PG to a libpq conninfo (e.g. 'host=localhost port=5432 user=postgres dbname=aw_test')."""
    return os.environ.get("SDPS_TEST_PG")
