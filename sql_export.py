"""SQL export - the handoff point to Power BI.

Writes three tables to SQLite:
  orders_clean       the cleaned data, with types inferred properly
  decision_log       one row per decision, so provenance lives next to the data
  run_metadata       one row per run: source file, hash, reviewer, timestamps

Power BI connects to the .db file directly (ODBC / the SQLite connector).
We deliberately do not embed Power BI: that needs Premium capacity or PPU
licences, which is not worth it for a solo project. Ship clean data plus the
log and let the customer point their own Power BI at it.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd

import cleaner

TABLE_DATA = "orders_clean"
TABLE_LOG = "decision_log"
TABLE_META = "run_metadata"


def infer_types(df: pd.DataFrame) -> pd.DataFrame:
    """Turn text columns into real dates/numbers so Power BI sees them right.

    Only converts when nearly every non-null value parses - otherwise a
    single stray string would silently null out a whole column.
    """
    out = df.copy()
    for col in out.columns:
        # Version-agnostic text check: pandas 3 gives text columns a string
        # dtype rather than `object`, so `dtype != object` would skip them.
        if not cleaner._is_text_series(out[col]):
            continue
        s = out[col].dropna().astype(str).str.strip()
        s = s[s != ""]
        if s.empty:
            continue

        as_num = pd.to_numeric(s.str.replace(r"[£$,]", "", regex=True),
                               errors="coerce")
        if as_num.notna().mean() > 0.98:
            out[col] = pd.to_numeric(
                out[col].astype(str).str.replace(r"[£$,]", "", regex=True),
                errors="coerce")
            continue

        iso = s.str.match(r"^\d{4}-\d{2}-\d{2}$")
        if iso.mean() > 0.98:
            out[col] = pd.to_datetime(out[col], format="%Y-%m-%d",
                                      errors="coerce")
    return out


def export(df: pd.DataFrame, log, db_path: str, *,
           table: str = TABLE_DATA, typed: bool = True,
           if_exists: str = "replace") -> dict[str, Any]:
    """Write cleaned data + provenance to SQLite. Returns a small summary."""
    data = infer_types(df) if typed else df.copy()
    payload = log.to_dict()

    log_rows = pd.DataFrame(payload["decisions"])
    meta = pd.DataFrame([{
        "source_file": payload["source"]["file"],
        "source_sha256": payload["source"]["sha256"],
        "source_rows": payload["source"]["rows"],
        "output_rows": len(data),
        "approved_by": payload["session"]["approved_by"],
        "started_at": payload["session"]["started_at"],
        "completed_at": payload["session"]["completed_at"],
        "decisions_approved": payload["session"]["approved"],
        "decisions_modified": payload["session"]["modified"],
        "decisions_skipped": payload["session"]["skipped"],
    }])

    with sqlite3.connect(db_path) as con:
        data.to_sql(table, con, index=False, if_exists=if_exists)
        log_rows.to_sql(TABLE_LOG, con, index=False, if_exists=if_exists)
        meta.to_sql(TABLE_META, con, index=False, if_exists=if_exists)
        con.commit()

    return {
        "db_path": db_path,
        "tables": {table: len(data), TABLE_LOG: len(log_rows),
                   TABLE_META: len(meta)},
        "dtypes": {c: str(t) for c, t in data.dtypes.items()},
    }


def preview_schema(df: pd.DataFrame) -> pd.DataFrame:
    """What Power BI will see, before you commit to writing it."""
    typed = infer_types(df)
    return pd.DataFrame({
        "column": typed.columns,
        "as_text": [str(df[c].dtype) for c in typed.columns],
        "exported_as": [str(typed[c].dtype) for c in typed.columns],
        "nulls": [int(typed[c].isna().sum()) for c in typed.columns],
    })
