"""`python -m triage run --case case_001`: run one case, show each step, ask for approval.

The run is checkpointed to runs/checkpoints.db (SQLite), keyed by run id, so when the
approval gate pauses the graph, resuming with Command(resume=...) continues the same run.
"""

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt

from triage.agent.graph import build_graph, initial_state, thread_config
from triage.agent.nodes import Deps
from triage.config import get_settings
from triage.llm.providers import LLMClient

console = Console()


def describe_step(node: str, update: dict) -> None:
    step = (update.get("steps") or [{}])[-1]
    detail = {k: v for k, v in step.items() if k not in ("node", "seconds")}
    console.print(f"[bold cyan]{node}[/bold cyan] ({step.get('seconds', 0)}s) {detail if detail else ''}")
    for event in update.get("guardrail_events") or []:
        console.print(f"  [yellow]guardrail[/yellow] {event['layer']}/{event['check']}: {event['detail']}")


def ask_approval(request: dict) -> dict:
    console.rule("[bold]Approval needed")
    console.print(f"Action:  [bold]{request['action']}({request['target_service']})[/bold]  runbook {request['runbook_id']}")
    console.print(f"Reason:  {request['reason']}")
    for e in request.get("evidence", []):
        console.print(f"  [{e['tool_call_id']}] {e['quote']}")
    answer = Prompt.ask("approve or reject?", choices=["approve", "reject"], default="reject")
    note = Prompt.ask("note for the agent (optional)", default="")
    return {"approved": answer == "approve", "note": note}


def run_case_cli(case_id: str, auto_approve: bool, reviewer: str | None, provider: str | None) -> int:
    settings = get_settings()
    reviewer_enabled = settings.reviewer_enabled if reviewer is None else reviewer == "on"
    deps = Deps(llm=LLMClient.from_settings(settings, only=provider), settings=settings, reviewer_enabled=reviewer_enabled)

    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.runs_dir / "checkpoints.db", check_same_thread=False)
    graph = build_graph(deps, checkpointer=SqliteSaver(conn))

    state = initial_state(case_id, deps, auto_approve)
    config = thread_config(state["run_id"])
    console.print(f"[bold]{case_id}[/bold]  run {state['run_id']}")
    console.print(f"Alert: {state['alert']['text']}")
    console.print("LLM: " + ", ".join(f"{h.provider}:{h.model}" for h in deps.llm.handles) + f" | reviewer {'on' if reviewer_enabled else 'off'}")

    payload = state
    while True:
        request = None
        for update in graph.stream(payload, config, stream_mode="updates"):
            for node, data in update.items():
                if node == "__interrupt__":
                    request = data[0].value
                elif data:
                    describe_step(node, data)
        if request is None:
            break
        payload = Command(resume=ask_approval(request))

    final = graph.get_state(config).values
    conn.close()
    report = final.get("report") or {}
    paths = report.get("paths", {})
    console.rule("Report")
    md_path = paths.get("dir")
    if md_path:
        from pathlib import Path

        console.print(Markdown((Path(md_path) / "report.md").read_text()))
        console.print(f"Files: {md_path}/report.md, report.json, trace.json")
    return 0 if final.get("status") in ("diagnosed", "escalated") else 1
