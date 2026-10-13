"""`python -m triage smoke tools --case case_001`: run all 5 tools once and print their output."""

from rich.console import Console
from rich.panel import Panel

from triage.config import get_settings
from triage.guardrails.process import validate_args
from triage.tools import TOOLS, run_tool
from triage.tools.db import ToolContext, case_alert


def smoke_tools(case_id: str) -> None:
    console = Console()
    settings = get_settings()
    ctx = ToolContext.for_case(case_id, settings.db_path)
    alert = case_alert(settings.db_path, case_id)
    entry = alert["service"]
    calls = [
        ("list_services", {}),
        ("get_metric_changes", {"top_n": 10}),
        ("query_logs", {"service": entry, "level": "ERROR"}),
        ("get_trace_stats", {"service": entry}),
        ("search_runbooks", {"query": alert["text"]}),
    ]
    console.print(f"[bold]{case_id}[/bold] alert: {alert['text']}")
    for name, raw_args in calls:
        args, error = validate_args(TOOLS[name].args_model, raw_args, list(ctx.services))
        if error:
            console.print(f"[red]{name}: {error}[/red]")
            continue
        text, truncated, removed = run_tool(name, ctx, args)
        title = f"{name}({raw_args}) {len(text)} chars" + (" [truncated]" if truncated else "")
        console.print(Panel(text, title=title, expand=False))
