"""The LangGraph graph, built explicitly: nodes, plain edges and conditional edges.

alert -> validate_alert -> investigate <-> run_tools -> diagnose -> review <-> review_tools
      -> propose_action -> approval_gate -> report
with escalate -> report for low confidence, failed guardrails, blocked actions, or a
second "revise" from the reviewer, and approval_gate -> investigate after one rejection.
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from triage.agent import nodes
from triage.agent.nodes import Deps
from triage.agent.state import TriageState
from triage.tools.db import case_alert

# ---- routing: each function reads the state and names the next node -------


def after_validate(state: TriageState) -> str:
    return "report" if state.get("status") == "rejected" else "investigate"


def after_investigate(state: TriageState) -> str:
    if state.get("escalation_reason"):
        return "escalate"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "run_tools"  # every requested call gets an answer, even "budget exhausted"
    return "diagnose"


def make_after_run_tools(deps: Deps):
    def after_run_tools(state: TriageState) -> str:
        return "diagnose" if state.get("tool_calls_used", 0) >= deps.settings.max_tool_calls else "investigate"

    return after_run_tools


def make_after_diagnose(deps: Deps):
    def after_diagnose(state: TriageState) -> str:
        if state.get("escalation_reason"):
            return "escalate"
        return "review" if deps.reviewer_enabled else "propose_action"

    return after_diagnose


def after_review(state: TriageState) -> str:
    if state.get("escalation_reason"):
        return "escalate"
    pending = state.get("review_messages") or []
    if pending and isinstance(pending[-1], AIMessage) and pending[-1].tool_calls:
        return "review_tools"
    return "investigate" if state["reviews"][-1]["verdict"] == "revise" else "propose_action"


def after_propose(state: TriageState) -> str:
    if state.get("escalation_reason"):
        return "escalate"
    return "report" if state["action"]["action"] == "no_action" else "approval_gate"


def after_approval(state: TriageState) -> str:
    if state.get("escalation_reason"):
        return "escalate"
    return "report" if state["approval"]["approved"] else "investigate"


# ---- graph ---------------------------------------------------------------


def _bind(fn, deps: Deps):
    """Give a node its dependencies while keeping the (state) -> update signature LangGraph expects."""

    def node(state: TriageState) -> dict:
        return fn(state, deps)

    node.__name__ = fn.__name__
    return node


def build_graph(deps: Deps, checkpointer=None):
    g = StateGraph(TriageState)
    for fn in (
        nodes.validate_alert,
        nodes.investigate,
        nodes.run_tools,
        nodes.diagnose,
        nodes.review,
        nodes.review_tools,
        nodes.propose_action,
        nodes.approval_gate,
        nodes.escalate,
        nodes.report,
    ):
        g.add_node(fn.__name__, _bind(fn, deps))

    g.add_edge(START, "validate_alert")
    g.add_conditional_edges("validate_alert", after_validate, ["investigate", "report"])
    g.add_conditional_edges("investigate", after_investigate, ["run_tools", "diagnose", "escalate"])
    g.add_conditional_edges("run_tools", make_after_run_tools(deps), ["investigate", "diagnose"])
    g.add_conditional_edges("diagnose", make_after_diagnose(deps), ["review", "propose_action", "escalate"])
    g.add_conditional_edges("review", after_review, ["review_tools", "investigate", "propose_action", "escalate"])
    g.add_edge("review_tools", "review")
    g.add_conditional_edges("propose_action", after_propose, ["approval_gate", "report", "escalate"])
    g.add_conditional_edges("approval_gate", after_approval, ["report", "investigate", "escalate"])
    g.add_edge("escalate", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def initial_state(case_id: str, deps: Deps, auto_approve: bool = False, alert: dict | None = None) -> dict:
    """The alert comes from the DB unless a test or the adversarial suite passes its own."""
    return {
        "case_id": case_id,
        "run_id": nodes.new_run_id(case_id),
        "alert": alert if alert is not None else case_alert(deps.settings.db_path, case_id),
        "auto_approve": auto_approve,
        "status": "running",
    }


def thread_config(run_id: str) -> dict:
    # recursion_limit: the longest legal path (8 tool rounds, a revision, a rejection) is < 60 steps.
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 80}


def run_incident(case_id: str, deps: Deps, auto_approve: bool = False, alert: dict | None = None, graph=None) -> dict:
    """Run until the end or until the approval gate pauses. A paused result has '__interrupt__'."""
    graph = graph or build_graph(deps)
    state = initial_state(case_id, deps, auto_approve, alert)
    result = graph.invoke(state, thread_config(state["run_id"]))
    return {**result, "run_id": state["run_id"]}


def resume_incident(graph, run_id: str, approved: bool, note: str = "") -> dict:
    """Answer the approval gate. The checkpointer restores the paused state for this thread."""
    result = graph.invoke(Command(resume={"approved": approved, "note": note}), thread_config(run_id))
    return {**result, "run_id": run_id}
