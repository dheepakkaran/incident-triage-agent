"""query_logs: log templates for one service, with counts before vs after the incident started.

Raw log lines are grouped into templates (numbers and ids masked), so the model sees
"request timeout after <n>ms" x 1,240 instead of 1,240 separate lines. Sample lines are
attacker-controlled text, so each one goes through the injection sanitizer.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from triage.guardrails.input import sanitize_text
from triage.tools.db import ToolContext, check_service, query_df

SAMPLE_CHARS = 300

_MASKS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<id>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
]


class QueryLogsArgs(BaseModel):
    """Log templates for one service (numbers and ids masked), with line counts in the 10 minutes
    before vs after the incident started, and 2 sample lines each. level=WARN includes ERROR."""

    service: str
    level: Literal["ERROR", "WARN", "ANY"] = "ERROR"
    keyword: str | None = Field(default=None, max_length=50, description="only lines containing this text")
    limit: int = Field(default=10, ge=1, le=20, description="max templates to return")

    @field_validator("service")
    @classmethod
    def _known(cls, value, info):
        return check_service(value, info)


def template_of(message: str) -> str:
    for pattern, token in _MASKS:
        message = pattern.sub(token, message)
    return message.strip()[:160]


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def log_templates(ctx: ToolContext, service: str, level: str = "ERROR", keyword: str | None = None) -> list[dict]:
    sql = "SELECT ts, level, message, count FROM logs WHERE case_id = ? AND service = ?"
    params: tuple = (ctx.case_id, service)
    if level == "ERROR":
        sql += " AND level = 'ERROR'"
    elif level == "WARN":
        sql += " AND level IN ('WARN', 'ERROR')"
    if keyword:
        sql += " AND message LIKE ? ESCAPE '\\'"
        params += (f"%{_like_escape(keyword)}%",)
    rows = query_df(ctx.db_path, sql, params)

    groups: dict[str, dict] = {}
    for r in rows.itertuples():
        tmpl = template_of(r.message)
        g = groups.setdefault(tmpl, {"template": tmpl, "level": r.level, "before": 0, "after": 0, "samples": []})
        g["after" if r.ts >= ctx.inject_ts else "before"] += int(r.count)
        if len(g["samples"]) < 2 and r.message not in g["samples"]:
            g["samples"].append(r.message)
    return sorted(groups.values(), key=lambda g: (abs(g["after"] - g["before"]), g["after"]), reverse=True)


def line_totals(ctx: ToolContext, service: str) -> tuple[int, int]:
    df = query_df(
        ctx.db_path,
        "SELECT SUM(CASE WHEN ts < ? THEN count ELSE 0 END) AS before, "
        "SUM(CASE WHEN ts >= ? THEN count ELSE 0 END) AS after FROM logs WHERE case_id = ? AND service = ?",
        (ctx.inject_ts, ctx.inject_ts, ctx.case_id, service),
    )
    return int(df.before.iloc[0] or 0), int(df.after.iloc[0] or 0)


def query_logs(ctx: ToolContext, args: QueryLogsArgs) -> str:
    before, after = line_totals(ctx, args.service)
    groups = log_templates(ctx, args.service, args.level, args.keyword)
    title = f"logs for {args.service}, level={args.level}" + (f", keyword='{args.keyword}'" if args.keyword else "")
    lines = [f"{title}. all lines (any level) before -> after: {before} -> {after}"]
    if not groups:
        lines.append(f"no matching {args.level} lines before or after the incident started")
    for i, g in enumerate(groups[: args.limit], start=1):
        lines.append(f"[{i}] {g['level']} \"{g['template']}\" count {g['before']} -> {g['after']}")
        for sample in g["samples"]:
            lines.append(f"    e.g. {sample[:SAMPLE_CHARS]}")
    if len(groups) > args.limit:
        lines.append(f"({len(groups) - args.limit} more templates not shown)")
    # Every line is passed through the sanitizer: log text is written by whoever can make a
    # service log something, so it must never be able to talk to the model.
    clean, _removed = sanitize_text("\n".join(lines))
    return clean
