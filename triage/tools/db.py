"""Read-only access to data/telemetry.db.

Two rules every tool follows:
  1. The connection is opened with SQLite's `mode=ro` URI flag, so even a bug cannot write.
  2. SQL text is written here in code, never by the LLM. Values go in as `?` parameters.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@contextmanager
def readonly(db_path: Path):
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        yield conn
    finally:
        conn.close()


def query_df(db_path: Path, sql: str, params: tuple) -> pd.DataFrame:
    with readonly(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def case_services(db_path: Path, case_id: str) -> list[str]:
    """Every service that shows up in this case's metrics, logs or spans."""
    with readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT service FROM metrics WHERE case_id = ? "
            "UNION SELECT service FROM logs WHERE case_id = ? "
            "UNION SELECT service FROM spans WHERE case_id = ?",
            (case_id, case_id, case_id),
        ).fetchall()
    return sorted(r[0] for r in rows)


def known_case_ids(db_path: Path) -> list[str]:
    with readonly(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT case_id FROM cases ORDER BY case_id")]


def case_alert(db_path: Path, case_id: str) -> dict:
    with readonly(db_path) as conn:
        row = conn.execute(
            "SELECT case_id, service, signal, started_at, text FROM alerts WHERE case_id = ?", (case_id,)
        ).fetchone()
    if row is None:
        raise KeyError(f"no alert for {case_id}")
    return dict(zip(("case_id", "service", "signal", "started_at", "text"), row, strict=True))


@dataclass(frozen=True)
class ToolContext:
    """The run context. Tools get the case from here, so the LLM cannot pick another case."""

    case_id: str
    db_path: Path
    services: tuple[str, ...]
    inject_ts: int

    @classmethod
    def for_case(cls, case_id: str, db_path: Path) -> "ToolContext":
        with readonly(db_path) as conn:
            row = conn.execute("SELECT inject_ts FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown case {case_id}")
        return cls(case_id, Path(db_path), tuple(case_services(db_path, case_id)), int(row[0]))


def callee_of(operation: str) -> str:
    """'CurrencyService/Convert' -> 'currencyservice'. Root HTTP spans ('frontend') map to themselves."""
    return operation.split("/")[0].lower()


def fmt(value: float) -> str:
    """Short human number: 0.0123, 4.56, 789, 12.3k, 4.5M, 7.9G."""
    if value != value:  # NaN
        return "n/a"
    size = abs(value)
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e4, "k")):
        if size >= limit:
            return f"{value / limit:.1f}{suffix}"
    if size >= 100:
        return f"{value:.0f}"
    if size >= 1:
        return f"{value:.2f}"
    return f"{value:.3g}"


def check_service(value: str, info) -> str:
    """Pydantic validator: the service must be one of this case's services (passed as context)."""
    services = (info.context or {}).get("services")
    if services is not None and value not in services:
        raise ValueError(f"unknown service '{value}'. Known services: {', '.join(services)}")
    return value
