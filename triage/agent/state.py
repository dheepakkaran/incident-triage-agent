"""The graph state: one dict that every node reads and returns partial updates to.

Fields with a reducer (Annotated[..., fn]) are merged; all others are overwritten by
the node that returns them. Everything is plain JSON-friendly data (dicts, lists,
LangChain messages) so the SQLite checkpointer can save it and a paused run can resume.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_dicts(old: dict | None, new: dict | None) -> dict:
    return {**(old or {}), **(new or {})}


def add_timings(old: dict | None, new: dict | None) -> dict:
    """Seconds spent per node, summed over every visit."""
    merged = dict(old or {})
    for node, seconds in (new or {}).items():
        merged[node] = round(merged.get(node, 0.0) + seconds, 3)
    return merged


class TriageState(TypedDict, total=False):
    # Run context: set once at the start. Tools read case_id from here, never from the LLM.
    case_id: str
    run_id: str
    alert: dict
    auto_approve: bool  # eval policy: approve risky actions without asking (recorded in the report)
    inject_ts: int  # when the incident started; tools split before/after on this
    services: list[str]
    call_graph: list[str]  # "caller -> callee" edges, for the reviewer

    # Investigator conversation and the tools it ran
    messages: Annotated[list[AnyMessage], add_messages]
    tool_results: Annotated[dict, merge_dicts]  # tool_call_id -> ToolResult as dict
    tool_calls_used: int

    # Diagnosis -> review -> action -> approval
    diagnosis: dict | None
    review_messages: list[AnyMessage]  # the reviewer's own short conversation, reset each review
    reviewer_tool_calls: int
    reviews: Annotated[list[dict], operator.add]  # every verdict, in order
    revision_round: int  # 0 or 1: the reviewer may send the case back once
    runbooks: dict  # runbook_id -> metadata, from search_runbooks in propose_action
    action: dict | None
    blocked_action: dict | None  # an action the guardrails refused, kept for the report
    approval: dict | None
    rejections: int  # human rejections so far; the second one escalates

    # Outcome
    status: str  # running | diagnosed | escalated | rejected
    escalation_reason: str | None
    escalation_note: str | None
    report: dict | None

    # Audit trail
    guardrail_events: Annotated[list[dict], operator.add]
    llm_calls: Annotated[list[dict], operator.add]
    steps: Annotated[list[dict], operator.add]  # timeline of nodes, for the trace and the Space
    timings: Annotated[dict, add_timings]
