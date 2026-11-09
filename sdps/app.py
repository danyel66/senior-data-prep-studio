"""QApplication bootstrap, plus pre-flight checks for problems seen on real machines."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLockFile, Qt
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, ORG_NAME, __version__, macos
from .db.config import app_data_dir
from .ui.main_window import MainWindow
from .ui.theme import apply_dark_theme

_PLATFORM_PLUGIN = {"darwin": "libqcocoa.dylib", "win32": "qwindows.dll"}
ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def _cloud_synced(path: Path) -> str | None:
    """Name of the cloud-sync service a path is (probably) under, or None."""
    p = str(path.resolve())
    home = str(Path.home())
    if "/Library/Mobile Documents/" in p or "/Library/CloudStorage/" in p:
        return "iCloud Drive / a cloud-storage provider"
    if sys.platform == "darwin" and (p.startswith(home + "/Documents") or p.startswith(home + "/Desktop")):
        return "iCloud Drive (if 'Desktop & Documents Folders' sync is on)"
    if "OneDrive" in p or "Dropbox" in p:
        return "OneDrive / Dropbox"
    return None


def preflight() -> str | None:
    """Return a human-readable problem description if Qt can't possibly start, else None.
    Qt aborts the whole process when the platform plugin is missing, so check before QApplication."""
    plugin = _PLATFORM_PLUGIN.get(sys.platform)
    if plugin is None:
        return None
    platforms = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms"
    target = platforms / plugin
    if target.is_file() and target.stat().st_size > 0:
        return None
    msg = [f"Qt's display plugin is missing: {target}", ""]
    synced = _cloud_synced(Path(sys.prefix))
    if synced:
        msg += [f"Your Python environment ({sys.prefix}) is inside a folder synced by {synced}.",
                "Cloud sync removes local copies of library files, which breaks the app at random.",
                "Create the environment outside synced folders:", "",
                "  python3.12 -m venv ~/.venvs/sdps && source ~/.venvs/sdps/bin/activate",
                "  pip install --no-cache-dir -r requirements.txt"]
    else:
        msg += ["Reinstall PyQt6 without pip's cache:", "",
                "  pip uninstall -y PyQt6 PyQt6-Qt6 PyQt6-sip && pip cache purge",
                "  pip install --no-cache-dir PyQt6"]
    return "\n".join(msg)


def create_app(argv: list[str] | None = None) -> QApplication:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if QApplication.instance() is None:
        macos.set_app_name(APP_NAME)          # menu bar title on macOS (must precede QApplication)
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))   # also the Dock icon on macOS
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    apply_dark_theme(app)
    return app


def main() -> int:
    if problem := preflight():
        sys.stderr.write(f"\n{APP_NAME} can't start.\n\n{problem}\n\n")
        if macos.launched_from_app():         # no terminal to read: show it
            macos.alert(f"{APP_NAME} can't start", problem)
        return 1
    app = create_app()

    # One copy at a time: a second copy can't open the DuckDB file anyway.
    lock = QLockFile(str(app_data_dir() / "app.lock"))
    lock.setStaleLockTime(0)            # a crashed copy's lock is detected via its (dead) PID
    if not lock.tryLock(200):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running.\n\n"
                                "Switch to the open window (check the Dock or Mission Control).")
        return 0

    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    code = app.exec()
    lock.unlock()
    return code
