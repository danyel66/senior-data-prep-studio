# Senior Data Prep Studio

Desktop studio (PyQt6) for senior-level SQL, Python/Pandas and analytics-engineering practice
against production-scale data.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | PyQt6 skeleton, split-screen layout, dark theme | ✅ done |
| 2 | DB connection manager + AdventureWorks seed (DuckDB/Postgres) | ⏳ |
| 3 | AI "Senior Principal Data Architect" problem generator | ⏳ |
| 4 | Async SQL/Python execution (QThread workers) | ⏳ |

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Tests (headless): `pip install pytest && QT_QPA_PLATFORM=offscreen pytest -q`

## Layout

```
senior-data-prep-studio/
├── main.py                      # entry point
├── pyproject.toml / requirements.txt
├── sdps/
│   ├── app.py                   # QApplication bootstrap, HiDPI, theme
│   ├── ui/
│   │   ├── theme.py             # colour tokens, Fusion palette, global QSS
│   │   ├── main_window.py       # splitters, menus, toolbar, status bar, QSettings persistence
│   │   └── widgets/
│   │       ├── code_editor.py        # editor w/ gutter, smart indent, Ctrl+Enter; EditorPanel
│   │       ├── syntax_highlighter.py # SQL + Python highlighters (multi-line aware)
│   │       ├── prompt_panel.py       # AI challenge display + category/difficulty pickers
│   │       ├── results_panel.py      # virtualised QAbstractTableModel grid (lazy fetchMore)
│   │       └── telemetry_panel.py    # metric cards, live RSS, bounded execution log
│   ├── db/                      # Phase 2
│   ├── ai/                      # Phase 3
│   └── execution/               # Phase 4
└── tests/test_smoke.py
```

## Shortcuts

| Key | Action |
|---|---|
| Ctrl+Enter / F5 | Run current script (or selection) |
| Ctrl+Shift+L | Toggle SQL ↔ Python |
| Ctrl+G | New AI challenge |
| Ctrl+D | Load 1M-row demo dataset (grid virtualisation check) |
| Ctrl+L | Clear results |
