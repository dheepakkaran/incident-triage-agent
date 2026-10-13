"""list_services: the services in this case and who calls whom (from client spans)."""

from pydantic import BaseModel

from triage.tools.db import ToolContext, callee_of, query_df


class ListServicesArgs(BaseModel):
    """List the services in this incident and the caller -> callee edges between them."""


def call_edges(ctx: ToolContext) -> list[tuple[str, str, int]]:
    """(caller, callee, requests) for every client span whose operation names another service."""
    spans = query_df(
        ctx.db_path,
        "SELECT service, operation, COUNT(*) AS n FROM spans WHERE case_id = ? GROUP BY service, operation",
        (ctx.case_id,),
    )
    spans["callee"] = spans.operation.map(callee_of)
    calls = spans[(spans.callee != spans.service) & spans.callee.isin(ctx.services)]
    edges = calls.groupby(["service", "callee"]).n.sum().sort_values(ascending=False)
    return [(caller, callee, int(n)) for (caller, callee), n in edges.items()]


def list_services(ctx: ToolContext, args: ListServicesArgs) -> str:
    lines = [f"services ({len(ctx.services)}): {', '.join(ctx.services)}", "calls (caller -> callee: sampled requests in window):"]
    lines += [f"{caller} -> {callee}: {n}" for caller, callee, n in call_edges(ctx)]
    return "\n".join(lines)
