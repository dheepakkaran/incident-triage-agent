"""The 5 read-only tools and one shared way to run them.

Each tool is a plain function `fn(ctx, args) -> str`, where `ctx` is the run context
(which case, which DB, which services) and `args` is a validated Pydantic model. The
LLM only ever sees the JSON schema of the args model; it never passes a case id or SQL.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from triage.guardrails.input import REMOVED, sanitize_text
from triage.guardrails.process import truncate
from triage.tools.db import ToolContext
from triage.tools.logs import QueryLogsArgs, query_logs
from triage.tools.metrics import MetricChangesArgs, get_metric_changes
from triage.tools.runbooks import SearchRunbooksArgs, search_runbooks
from triage.tools.services import ListServicesArgs, list_services
from triage.tools.traces import TraceStatsArgs, get_trace_stats


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    func: Callable[[ToolContext, BaseModel], str]

    def schema(self) -> dict:
        """OpenAI-style function schema for bind_tools, without Pydantic's 'title' noise."""
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        for prop in params.get("properties", {}).values():
            prop.pop("title", None)
        description = " ".join((self.args_model.__doc__ or "").split())
        return {"type": "function", "function": {"name": self.name, "description": description, "parameters": params}}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("list_services", ListServicesArgs, list_services),
        ToolSpec("get_metric_changes", MetricChangesArgs, get_metric_changes),
        ToolSpec("query_logs", QueryLogsArgs, query_logs),
        ToolSpec("get_trace_stats", TraceStatsArgs, get_trace_stats),
        ToolSpec("search_runbooks", SearchRunbooksArgs, search_runbooks),
    )
}
INVESTIGATOR_TOOLS = list(TOOLS)
REVIEWER_TOOLS = ["get_trace_stats", "get_metric_changes"]


def tool_schemas(names: list[str]) -> list[dict]:
    return [TOOLS[name].schema() for name in names]


def run_tool(name: str, ctx: ToolContext, args: BaseModel) -> tuple[str, bool, list[str]]:
    """Run a validated call. Returns (text the model will see, truncated?, removed injection lines).

    query_logs already sanitizes its own sample lines; those show up here as REMOVED markers,
    so they are counted too and the guardrail event is never lost.
    """
    raw = TOOLS[name].func(ctx, args)
    removed_inside = [f"(removed inside {name})"] * raw.count(REMOVED)
    clean, removed = sanitize_text(raw)
    text, truncated = truncate(clean)
    return text, truncated, removed_inside + removed
