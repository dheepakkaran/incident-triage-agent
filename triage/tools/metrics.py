"""get_metric_changes: which metrics moved most between the baseline and the incident window."""

import math

from pydantic import BaseModel, Field, field_validator

from triage.tools.db import ToolContext, check_service, fmt, query_df

# Below these values a metric is treated as "basically zero", so 0.001 -> 0.004 is not
# reported as a +300% change. Units: see docs/data_notes.md.
NOISE_FLOOR = {
    "cpu_usage": 0.5,
    "memory_bytes": 1e6,
    "disk_io_bytes": 1e4,
    "open_sockets": 1.0,
    "request_rate": 0.1,
    "error_rate": 0.01,
    "latency_p50_s": 0.002,
    "latency_p90_s": 0.002,
}


class MetricChangesArgs(BaseModel):
    """Compare every metric's mean in the 10 minutes before the incident with the 10 minutes after,
    sorted by the largest relative change. Optionally limit to one service."""

    service: str | None = Field(default=None, description="only this service (optional)")
    top_n: int = Field(default=15, ge=1, le=30, description="how many rows to return")

    @field_validator("service")
    @classmethod
    def _known(cls, value, info):
        return value if value is None else check_service(value, info)


def metric_changes(ctx: ToolContext, service: str | None = None) -> list[dict]:
    """Rows of {service, metric, baseline, incident, change}, largest |change| first.

    change is relative to the baseline, floored at the metric's noise floor so a metric
    that goes from ~0 to something real gets a large but finite score.
    """
    sql = (
        "SELECT service, metric, "
        "AVG(CASE WHEN ts < ? THEN value END) AS baseline, "
        "AVG(CASE WHEN ts >= ? THEN value END) AS incident "
        "FROM metrics WHERE case_id = ?"
    )
    params: tuple = (ctx.inject_ts, ctx.inject_ts, ctx.case_id)
    if service:
        sql += " AND service = ?"
        params += (service,)
    df = query_df(ctx.db_path, sql + " GROUP BY service, metric", params).fillna(0.0)
    rows = []
    for r in df.itertuples():
        floor = NOISE_FLOOR.get(r.metric, 1e-6)
        if max(abs(r.baseline), abs(r.incident)) < floor:
            continue  # both windows are noise-level
        change = (r.incident - r.baseline) / max(abs(r.baseline), floor)
        rows.append({"service": r.service, "metric": r.metric, "baseline": r.baseline, "incident": r.incident, "change": change})
    return sorted(rows, key=lambda row: abs(row["change"]), reverse=True)


def _pct(row: dict) -> str:
    if abs(row["baseline"]) < NOISE_FLOOR.get(row["metric"], 1e-6):
        return "up from ~0"
    pct = row["change"] * 100
    return f"{pct:+.0f}%" if math.isfinite(pct) else "n/a"


def get_metric_changes(ctx: ToolContext, args: MetricChangesArgs) -> str:
    rows = metric_changes(ctx, args.service)
    scope = args.service or "all services"
    lines = [f"metric changes for {scope}: mean in 10 min before -> 10 min after the incident started, largest change first"]
    for row in rows[: args.top_n]:
        lines.append(f"{row['service']} {row['metric']}: {fmt(row['baseline'])} -> {fmt(row['incident'])} ({_pct(row)})")
    if not rows:
        lines.append("no metric changed above its noise floor")
    return "\n".join(lines)
