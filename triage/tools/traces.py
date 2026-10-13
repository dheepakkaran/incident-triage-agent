"""get_trace_stats: per call edge touching one service, baseline vs incident.

Client spans give the caller's view of each call (it includes network time); server
spans give the time spent inside the service. Comparing the two separates "the network
into X is slow" from "X itself is slow".
"""

import pandas as pd
from pydantic import BaseModel, field_validator

from triage.tools.db import ToolContext, callee_of, check_service, fmt, query_df

MIN_SPANS = 5  # do not quote a p95 computed from fewer requests than this


class TraceStatsArgs(BaseModel):
    """Request count, error rate and p50/p95 duration for every call edge into or out of one service,
    in the 10 minutes before vs after the incident started (1 in 5 traces sampled)."""

    service: str

    @field_validator("service")
    @classmethod
    def _known(cls, value, info):
        return check_service(value, info)


def _window_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "err": float("nan"), "p50": float("nan"), "p95": float("nan")}
    errors = df.status_code.fillna(0) != 0
    enough = len(df) >= MIN_SPANS
    return {
        "n": len(df),
        "err": float(errors.mean()),
        "p50": float(df.duration_ms.quantile(0.5)) if enough else float("nan"),
        "p95": float(df.duration_ms.quantile(0.95)) if enough else float("nan"),
    }


def edge_stats(ctx: ToolContext, service: str) -> list[dict]:
    """One row per edge touching `service`, plus a 'server' row for time inside the service."""
    operations = query_df(ctx.db_path, "SELECT DISTINCT operation FROM spans WHERE case_id = ?", (ctx.case_id,))
    inbound_ops = [op for op in operations.operation if callee_of(op) == service]
    marks = ",".join("?" * len(inbound_ops)) or "''"
    spans = query_df(
        ctx.db_path,
        f"SELECT service, operation, start_ts, duration_ms, status_code FROM spans "
        f"WHERE case_id = ? AND (service = ? OR operation IN ({marks}))",
        (ctx.case_id, service, *inbound_ops),
    )
    spans["callee"] = spans.operation.map(callee_of)
    spans["kind"] = "client"
    spans.loc[spans.callee == spans.service, "kind"] = "server"

    rows = []
    for (caller, callee, kind), group in spans.groupby(["service", "callee", "kind"]):
        if kind == "server" and caller != service:
            continue
        before = group[group.start_ts < ctx.inject_ts]
        after = group[group.start_ts >= ctx.inject_ts]
        rows.append(
            {
                "caller": caller,
                "callee": callee,
                "kind": kind,
                "before": _window_stats(before),
                "after": _window_stats(after),
            }
        )
    return rows


def _pct(value: float) -> str:
    return "n/a" if value != value else f"{value:.1%}"


def _line(row: dict) -> str:
    b, a = row["before"], row["after"]
    edge_name = f"inside {row['caller']} (server spans)" if row["kind"] == "server" else f"{row['caller']} -> {row['callee']}"
    return (
        f"{edge_name}: n {b['n']} -> {a['n']}, errors {_pct(b['err'])} -> {_pct(a['err'])}, "
        f"p50 {fmt(b['p50'])} -> {fmt(a['p50'])} ms, p95 {fmt(b['p95'])} -> {fmt(a['p95'])} ms"
    )


def get_trace_stats(ctx: ToolContext, args: TraceStatsArgs) -> str:
    rows = edge_stats(ctx, args.service)
    incoming = [r for r in rows if r["kind"] == "client" and r["callee"] == args.service]
    outgoing = [r for r in rows if r["kind"] == "client" and r["caller"] == args.service]
    server = [r for r in rows if r["kind"] == "server"]
    lines = [f"trace stats for {args.service}, before -> after (1 in 5 traces sampled; client view includes network time)"]
    lines.append("incoming calls:")
    lines += [_line(r) for r in incoming] or ["(none)"]
    lines.append("outgoing calls:")
    lines += [_line(r) for r in outgoing] or ["(none)"]
    lines += [_line(r) for r in server] or [f"inside {args.service}: no server spans (this service does not export traces)"]
    return "\n".join(lines)
