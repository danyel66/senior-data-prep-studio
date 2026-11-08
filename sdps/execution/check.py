"""Answer checking: compare the user's output with the reference solution's output.

SQL / dbt: compared *inside the database* with EXCEPT ALL in both directions, so results of any
size are checked without loading them into Python (important on 8 GB machines). Numeric columns
are compared at 4 decimal places. Column order doesn't matter when names match.

Python: both scripts run; the DataFrames are normalised (numbers rounded to 4 dp, NaN == NaN)
and compared as multisets of rows via row hashes, then for order when the challenge requires it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..db import DatabaseError
from .engine import ExecutionError, Executor, _py
from .sqltools import changes_schema, has_jinja, render_dbt, split_statements

SAMPLE_ROWS = 10
ORDER_CHECK_ROWS = 1_000
_NUMERIC_TYPES = ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC", "HUGEINT")


@dataclass
class CheckResult:
    passed: bool
    headline: str
    details: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    user_rows: int = 0
    expected_rows: int = 0
    missing: list[tuple] = field(default_factory=list)      # expected rows your answer lacks (sample)
    unexpected: list[tuple] = field(default_factory=list)   # rows your answer has that it shouldn't
    elapsed_ms: float = 0.0


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _final_query(script: str) -> tuple[str, int]:
    if has_jinja(script):
        script = render_dbt(script)
    statements = split_statements(script)
    if not statements:
        raise ExecutionError("Nothing to check: the editor only contains comments.")
    return statements[-1], len(statements)


def _align(user_cols: list[str], ref_cols: list[str], details: list[str]) -> list[str] | None:
    """Return the user's columns in the reference order, or None if they can't be lined up."""
    if len(user_cols) != len(ref_cols):
        details.append(f"Expected {len(ref_cols)} columns ({', '.join(ref_cols)}), "
                       f"but your answer has {len(user_cols)} ({', '.join(user_cols)}).")
        return None
    lower = {c.lower(): c for c in user_cols}
    if len(lower) == len(user_cols) and sorted(lower) == sorted(c.lower() for c in ref_cols):
        if [c.lower() for c in user_cols] != [c.lower() for c in ref_cols]:
            details.append("Columns are in a different order than expected; compared by name.")
        return [lower[c.lower()] for c in ref_cols]
    details.append(f"Column names differ (expected {', '.join(ref_cols)}; got {', '.join(user_cols)}); "
                   "compared by position.")
    return list(user_cols)


def check_sql(manager, user_script: str, reference_sql: str, order_matters: bool) -> CheckResult:
    t0 = time.perf_counter()
    details: list[str] = []
    user_sql, n_statements = _final_query(user_script)
    ref_sql, _ = _final_query(reference_sql)
    if n_statements > 1:
        details.append(f"Your script has {n_statements} statements; the last one was checked.")
    if changes_schema(user_sql):
        return CheckResult(False, "Your last statement changes data or schema; end the script with the query "
                                  "that answers the task.", details)

    session = manager.open_session()
    try:
        def describe(sql: str):
            cur = session.open(f"SELECT * FROM ({sql}) AS _q LIMIT 0")
            cols, types = list(cur.columns), list(cur.types)
            cur.close()
            return cols, types

        try:
            user_cols, user_types = describe(user_sql)
        except DatabaseError as e:
            return CheckResult(False, "Your query has an error.", details + [str(e).splitlines()[0]])
        try:
            ref_cols, ref_types = describe(ref_sql)
        except DatabaseError as e:
            return CheckResult(False, "The reference solution failed on this database.",
                               details + [str(e).splitlines()[0]])

        aligned = _align(user_cols, ref_cols, details)
        if aligned is None:
            return CheckResult(False, "Different columns than expected.", details, columns=ref_cols)

        def projection(cols: list[str], types: list[str], all_types: list[str]) -> str:
            parts = []
            for c, t, other in zip(cols, types, all_types):
                if any(k in t for k in _NUMERIC_TYPES) or any(k in other for k in _NUMERIC_TYPES):
                    parts.append(f"CAST({_quote(c)} AS DECIMAL(38,4)) AS {_quote(c)}")
                else:
                    parts.append(_quote(c))
            return ", ".join(parts)

        utypes = [user_types[user_cols.index(c)] for c in aligned]
        u_proj = projection(aligned, utypes, ref_types)
        r_proj = projection(ref_cols, ref_types, utypes)
        u = f"SELECT {u_proj} FROM ({user_sql}) AS _u"
        r = f"SELECT {r_proj} FROM ({ref_sql}) AS _r"
        try:
            counts = session.execute(
                f"SELECT (SELECT count(*) FROM ({u}) a), (SELECT count(*) FROM ({r}) b), "
                f"(SELECT count(*) FROM ({r} EXCEPT ALL {u}) m), (SELECT count(*) FROM ({u} EXCEPT ALL {r}) x)").rows[0]
        except DatabaseError as e:
            return CheckResult(False, "Your output's column types can't be compared with the expected output.",
                               details + [str(e).splitlines()[0]], columns=ref_cols)
        user_n, ref_n, n_missing, n_extra = (int(v) for v in counts)
        res = CheckResult(False, "", details, ref_cols, user_n, ref_n)
        if n_missing or n_extra:
            res.missing = session.execute(f"SELECT * FROM ({r} EXCEPT ALL {u}) m LIMIT {SAMPLE_ROWS}").rows
            res.unexpected = session.execute(f"SELECT * FROM ({u} EXCEPT ALL {r}) x LIMIT {SAMPLE_ROWS}").rows
            res.headline = (f"Not quite: {n_missing:,} expected row(s) are missing and "
                            f"{n_extra:,} row(s) shouldn't be there.")
            if user_n != ref_n:
                details.append(f"Row count: yours {user_n:,}, expected {ref_n:,}.")
        elif order_matters:
            ur = session.execute(f"{u} LIMIT {ORDER_CHECK_ROWS}").rows
            rr = session.execute(f"{r} LIMIT {ORDER_CHECK_ROWS}").rows
            first_diff = next((i for i, (a, b) in enumerate(zip(ur, rr)) if a != b), None)
            if first_diff is not None:
                res.headline = "Right rows, wrong order."
                details.append(f"The first difference is at row {first_diff + 1}. Check the ORDER BY the task asks for.")
                res.missing, res.unexpected = [rr[first_diff]], [ur[first_diff]]
            else:
                res.passed = True
        else:
            res.passed = True
        if res.passed:
            res.headline = f"Correct! Your output matches the reference solution ({user_n:,} rows)."
        res.elapsed_ms = (time.perf_counter() - t0) * 1000
        return res
    finally:
        session.close()


# ─────────────────────────────────────────── Python ───────────────────────────────────────────
def _normalise(df):
    import numpy as np
    import pandas as pd
    out = pd.DataFrame(index=range(len(df)))
    for i, col in enumerate(df.columns):
        s = df.iloc[:, i].reset_index(drop=True)
        if pd.api.types.is_bool_dtype(s):
            out[i] = s.astype("float64")
        elif pd.api.types.is_numeric_dtype(s):
            out[i] = s.astype("float64").round(4)
        elif pd.api.types.is_datetime64_any_dtype(s):
            out[i] = s.astype("int64", errors="ignore") if s.notna().all() else s.astype(str)
        else:
            numeric = pd.to_numeric(s, errors="coerce")
            if numeric.notna().sum() == s.notna().sum() and s.notna().any():
                out[i] = numeric.astype("float64").round(4)       # e.g. Decimal objects
            else:
                out[i] = s.astype(str).where(s.notna(), "<NULL>")
    out = out.replace({np.nan: -1.234567e300})                  # NaN must compare equal
    return out


def _rows(df) -> list[tuple]:
    return [tuple(_py(v) for v in row) for row in df.itertuples(index=False)]


def compare_frames(user_df, ref_df, order_matters: bool) -> CheckResult:
    import pandas as pd
    details: list[str] = []
    user_cols, ref_cols = [str(c) for c in user_df.columns], [str(c) for c in ref_df.columns]
    aligned = _align(user_cols, ref_cols, details)
    if aligned is None:
        return CheckResult(False, "Different columns than expected.", details, columns=ref_cols,
                           user_rows=len(user_df), expected_rows=len(ref_df))
    user_df = user_df.copy()
    user_df.columns = user_cols
    u = user_df[aligned].reset_index(drop=True)
    r = ref_df.reset_index(drop=True)
    hu = pd.util.hash_pandas_object(_normalise(u), index=False)
    hr = pd.util.hash_pandas_object(_normalise(r), index=False)
    res = CheckResult(False, "", details, ref_cols, len(u), len(r))
    cu, cr = hu.value_counts(), hr.value_counts()
    diff = cr.subtract(cu, fill_value=0)
    missing_hashes, extra_hashes = diff[diff > 0], diff[diff < 0]
    if len(missing_hashes) or len(extra_hashes):
        n_missing, n_extra = int(missing_hashes.sum()), int(-extra_hashes.sum())
        res.headline = f"Not quite: {n_missing:,} expected row(s) are missing and {n_extra:,} row(s) shouldn't be there."
        res.missing = _rows(r[hr.isin(missing_hashes.index)].head(SAMPLE_ROWS))
        res.unexpected = _rows(u[hu.isin(extra_hashes.index)].head(SAMPLE_ROWS))
        if len(u) != len(r):
            details.append(f"Row count: yours {len(u):,}, expected {len(r):,}.")
        return res
    if order_matters:
        mismatch = (hu.to_numpy() != hr.to_numpy()).nonzero()[0]
        if len(mismatch):
            i = int(mismatch[0])
            res.headline = "Right rows, wrong order."
            details.append(f"The first difference is at row {i + 1}. Sort your DataFrame as the task asks.")
            res.missing, res.unexpected = _rows(r.iloc[[i]]), _rows(u.iloc[[i]])
            return res
    res.passed = True
    res.headline = f"Correct! Your DataFrame matches the reference solution ({len(u):,} rows)."
    return res


def check_python(executor: Executor, user_df, reference_code: str, order_matters: bool) -> CheckResult:
    t0 = time.perf_counter()
    if user_df is None:
        return CheckResult(False, "Your script didn't assign a DataFrame to `result`.")
    ref = executor.run_python(reference_code, label="reference solution")
    if ref.dataframe is None:
        return CheckResult(False, "The reference solution didn't produce a DataFrame, so it can't be compared.")
    res = compare_frames(user_df, ref.dataframe, order_matters)
    res.elapsed_ms = (time.perf_counter() - t0) * 1000
    return res
