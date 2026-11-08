"""Execution engine (Qt-free). Runs SQL scripts and Python/pandas scripts against the connected
database on the calling (worker) thread, with cancellation, row streaming and memory telemetry.

SQL:    every statement but the last runs to completion; the last one is streamed in batches
        into a list the UI can display while it grows (capped at `row_cap` rows for memory).
Python: user code runs with `pd`, `np` and `sql(query) -> DataFrame` available; `print` output
        is captured; the answer is the DataFrame assigned to `result`.
"""
from __future__ import annotations

import builtins
import ctypes
import os
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..db import ConnectionManager, DatabaseError
from .sqltools import changes_schema, has_jinja, is_plan, render_dbt, returns_rows, split_statements

USER_FILENAME = "<your code>"


class ExecutionCancelled(Exception):
    pass


class ExecutionError(Exception):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details or message


@dataclass
class RunResult:
    language: str
    columns: list[str] = field(default_factory=list)
    rows: Sequence = ()
    truncated: bool = False
    elapsed_ms: float = 0.0
    peak_rss_mb: float | None = None
    mem_delta_mb: float | None = None
    statements: int = 0
    output: str = ""
    plan: str | None = None
    schema_changed: bool = False
    dataframe: Any = None              # pandas DataFrame (Python runs)

    @property
    def row_count(self) -> int:
        return len(self.rows)


# ───────────────────────────────────────── telemetry ─────────────────────────────────────────
class MemorySampler:
    """Samples this process's RSS on a background thread; reports peak and growth in MB."""

    def __init__(self, interval: float = 0.05):
        self.interval = interval
        self.baseline_mb = self.peak_mb = self.current_mb = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        try:
            import psutil
            self._proc = psutil.Process(os.getpid())
        except Exception:
            self._proc = None

    def _rss(self) -> float | None:
        try:
            return self._proc.memory_info().rss / 1_048_576 if self._proc else None
        except Exception:
            return None

    def __enter__(self) -> "MemorySampler":
        self.baseline_mb = self.peak_mb = self.current_mb = self._rss()
        if self._proc is not None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            rss = self._rss()
            if rss is not None:
                self.current_mb = rss
                self.peak_mb = max(self.peak_mb or 0.0, rss)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(1)
        rss = self._rss()
        if rss is not None:
            self.peak_mb = max(self.peak_mb or 0.0, rss)

    @property
    def delta_mb(self) -> float | None:
        if self.peak_mb is None or self.baseline_mb is None:
            return None
        return max(0.0, self.peak_mb - self.baseline_mb)


# ───────────────────────────────────── DataFrame adapter ─────────────────────────────────────
def _py(value: Any) -> Any:
    """numpy / pandas scalars -> plain Python values for display (NaN/NaT/NA -> None)."""
    if value is None:
        return None
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return value
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        value = value.item()
        if isinstance(value, float) and value != value:
            return None
    return value


class DataFrameRows(Sequence):
    """Zero-copy row view of a DataFrame for the virtualised grid (rows built only when shown)."""

    def __init__(self, df):
        self._arrays = [df.iloc[:, i].to_numpy() for i in range(df.shape[1])]
        self._n = len(df)

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, i):  # type: ignore[override]
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(self._n))]
        return tuple(_py(a[i]) for a in self._arrays)


def _filtered_traceback(exc: BaseException) -> tuple[str, str]:
    """(one-line message, traceback limited to the user's own code)."""
    te = traceback.TracebackException.from_exception(exc)
    te.stack = traceback.StackSummary.from_list([f for f in te.stack if f.filename == USER_FILENAME])
    lines = list(te.format())
    message = lines[-1].strip() if lines else str(exc)
    user_lines = [f.lineno for f in te.stack]
    if isinstance(exc, SyntaxError) and exc.lineno:
        message = f"Line {exc.lineno}: {exc.msg}"
    elif user_lines:
        message = f"Line {user_lines[-1]}: {message}"
    return message, "".join(lines)


# ───────────────────────────────────────── executor ──────────────────────────────────────────
class Executor:
    def __init__(self, manager: ConnectionManager, row_cap: int = 100_000, batch_size: int = 2_000):
        self.manager = manager
        self.row_cap = row_cap
        self.batch_size = batch_size
        self.rows_so_far = 0                   # read by the UI for live telemetry
        self.sampler: MemorySampler | None = None
        self._cancel = threading.Event()
        self._session = None
        self._lock = threading.Lock()
        self._py_thread: int | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        """Thread-safe: interrupts the running statement and/or the running Python code."""
        self._cancel.set()
        with self._lock:
            session, tid = self._session, self._py_thread
        if session is not None:
            session.interrupt()
        if tid is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(ExecutionCancelled))

    def _open_session(self):
        session = self.manager.open_session()
        with self._lock:
            self._session = session
        return session

    def _close_session(self, session) -> None:
        with self._lock:
            self._session = None
        session.close()

    # ---- SQL ---------------------------------------------------------------------------------
    def run_sql(self, script: str, on_columns: Callable[[list[str], list], None] | None = None) -> RunResult:
        if has_jinja(script):
            script = render_dbt(script)
        statements = split_statements(script)
        if not statements:
            raise ExecutionError("Nothing to run: the editor only contains comments.")
        result = RunResult("SQL", statements=len(statements),
                           schema_changed=any(changes_schema(s) for s in statements))
        messages: list[str] = []
        t0 = time.perf_counter()
        session = self._open_session()
        try:
            with MemorySampler() as sampler:
                self.sampler = sampler
                for i, stmt in enumerate(statements, 1):
                    if self.cancelled:
                        raise ExecutionCancelled()
                    last = i == len(statements)
                    try:
                        if last:
                            self._stream_last(session, stmt, result, on_columns)
                        else:
                            r = session.execute(stmt)
                            messages.append(f"Statement {i}: " + (f"{len(r.rows):,} rows" if r.columns else "OK") +
                                            f" ({r.elapsed_ms:,.0f} ms)")
                    except DatabaseError as e:
                        if self.cancelled:
                            raise ExecutionCancelled() from e
                        where = f"Statement {i} of {len(statements)}: " if len(statements) > 1 else ""
                        raise ExecutionError(where + str(e).splitlines()[0], str(e)) from e
            result.peak_rss_mb, result.mem_delta_mb = sampler.peak_mb, sampler.delta_mb
        finally:
            self._close_session(session)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        if result.columns:
            messages.append(f"Statement {len(statements)}: {result.row_count:,}"
                            f"{'+' if result.truncated else ''} rows")
        else:
            messages.append(f"Statement {len(statements)}: OK")
        result.output = "\n".join(messages)
        return result

    def _stream_last(self, session, stmt: str, result: RunResult, on_columns) -> None:
        cursor = session.open(stmt)
        try:
            result.columns = list(cursor.columns)
            rows: list = []
            result.rows = rows
            if not result.columns:
                return
            notified = False
            while True:
                if self.cancelled:
                    raise ExecutionCancelled()
                want = min(self.batch_size, self.row_cap - len(rows))
                batch = cursor.fetch(want) if want > 0 else []
                rows.extend(batch)
                self.rows_so_far = len(rows)
                if not notified and on_columns is not None:
                    on_columns(result.columns, rows)        # UI binds to the growing list
                    notified = True
                if len(rows) >= self.row_cap:
                    result.truncated = bool(cursor.fetch(1))
                    break
                if len(batch) < want:
                    break
            if is_plan(result.columns):
                result.plan = "\n".join(str(r[-1]) for r in rows)
        finally:
            cursor.close()

    # ---- Python ------------------------------------------------------------------------------
    def run_python(self, code: str, *, label: str = "your code") -> RunResult:
        try:
            import numpy as np
            import pandas as pd
        except ImportError as e:
            raise ExecutionError("pandas and numpy are needed for Python challenges: pip install -r requirements.txt") from e
        try:
            compiled = compile(code, USER_FILENAME, "exec")
        except SyntaxError as e:
            msg, details = _filtered_traceback(e)
            raise ExecutionError(f"Syntax error in {label}: {msg}", details) from e

        result = RunResult("Python")
        printed: list[str] = []
        session = self._open_session()

        def sql(query: str) -> "pd.DataFrame":
            if self.cancelled:
                raise ExecutionCancelled()
            q = render_dbt(query) if has_jinja(query) else query
            return session.fetch_df(q)

        def captured_print(*args, sep=" ", end="\n", file=None, flush=False):
            if file is not None:
                return builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            printed.append(sep.join(str(a) for a in args) + end)

        namespace = {"__name__": "__main__", "__builtins__": builtins, "pd": pd, "np": np, "sql": sql,
                     "print": captured_print}
        t0 = time.perf_counter()
        try:
            with MemorySampler() as sampler:
                self.sampler = sampler
                tid = threading.get_ident()
                try:
                    with self._lock:
                        self._py_thread = tid
                    if self.cancelled:
                        raise ExecutionCancelled()
                    exec(compiled, namespace)
                finally:
                    with self._lock:
                        self._py_thread = None
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)  # drop a late cancel
        except ExecutionCancelled:
            raise
        except DatabaseError as e:
            if self.cancelled:
                raise ExecutionCancelled() from e
            raise ExecutionError(f"A sql() query in {label} failed: {str(e).splitlines()[0]}", str(e)) from e
        except Exception as e:  # noqa: BLE001 - user code can raise anything
            if self.cancelled:
                raise ExecutionCancelled() from e
            msg, details = _filtered_traceback(e)
            raise ExecutionError(msg if label == "your code" else f"{label.capitalize()}: {msg}", details) from e
        finally:
            self._close_session(session)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.peak_rss_mb, result.mem_delta_mb = sampler.peak_mb, sampler.delta_mb
        result.output = "".join(printed)

        answer = namespace.get("result")
        if isinstance(answer, pd.Series):
            answer = answer.to_frame()
        if isinstance(answer, pd.DataFrame):
            frame = answer.reset_index() if not isinstance(answer.index, pd.RangeIndex) else answer
            result.dataframe = frame
            result.columns = [str(c) for c in frame.columns]
            shown = frame.head(self.row_cap)
            result.rows = DataFrameRows(shown)
            result.truncated = len(frame) > self.row_cap
            self.rows_so_far = len(frame)
        elif answer is not None:
            result.output += f"\n`result` is a {type(answer).__name__}, not a DataFrame:\n{answer!r}\n"
        return result
