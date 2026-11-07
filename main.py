"""Entry point: `python main.py`.

Kept free of newer syntax so the version check below can run (and explain itself) even on an
old interpreter such as macOS's built-in Python 3.9.
"""
import sys

if sys.version_info < (3, 11):
    sys.stderr.write(
        "\nSenior Data Prep Studio needs Python 3.11 or newer; this is Python %d.%d (%s).\n\n"
        "On macOS:  brew install python@3.12   (or the installer from python.org)\n"
        "Then recreate the environment outside iCloud-synced folders:\n"
        "  python3.12 -m venv ~/.venvs/sdps && source ~/.venvs/sdps/bin/activate\n"
        "  pip install -r requirements.txt\n\n"
        % (sys.version_info[0], sys.version_info[1], sys.executable))
    sys.exit(1)

from sdps.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
