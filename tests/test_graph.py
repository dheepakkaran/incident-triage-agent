"""Graph routing: each conditional edge picks the right next node, and a full run visits them in order."""

from langchain_core.messages import AIMessage, HumanMessage

from tests.conftest import CASE
from tests.test_guardrails import AGREE, GOOD_DIAGNOSIS, SCALE
from triage.agent import graph
from triage.agent.graph import build_graph, run_incident
from triage.llm.mock import as_json, tool_call


def test_after_validate():
    assert graph.after_validate({"status": "rejected"}) == "report"
    assert graph.after_validate({"status": "running"}) == "investigate"


def test_after_investigate():
    with_calls = AIMessage(content="", tool_calls=[{"name": "list_services", "args": {}, "id": "c1"}])
    assert graph.after_investigate({"messages": [with_calls]}) == "run_tools"
    assert graph.after_investigate({"messages": [AIMessage(content="done")]}) == "diagnose"
    assert graph.after_investigate({"messages": [HumanMessage("x")]}) == "diagnose"
    assert graph.after_investigate({"messages": [with_calls], "escalation_reason": "llm down"}) == "escalate"


def test_after_review():
    pending = AIMessage(content="", tool_calls=[{"name": "get_trace_stats", "args": {}, "id": "c1"}])
    assert graph.after_review({"review_messages": [pending]}) == "review_tools"
    assert graph.after_review({"review_messages": [], "reviews": [{"verdict": "agree"}]}) == "propose_action"
    assert graph.after_review({"review_messages": [], "reviews": [{"verdict": "revise"}]}) == "investigate"
    assert graph.after_review({"escalation_reason": "x"}) == "escalate"


def test_after_propose_and_approval():
    assert graph.after_propose({"action": {"action": "no_action"}}) == "report"
    assert graph.after_propose({"action": {"action": "scale_service"}}) == "approval_gate"
    assert graph.after_propose({"escalation_reason": "blocked"}) == "escalate"
    assert graph.after_approval({"approval": {"approved": True}}) == "report"
    assert graph.after_approval({"approval": {"approved": False}}) == "investigate"


def test_graph_has_the_documented_nodes(mock_deps_factory):
    compiled = build_graph(mock_deps_factory([]))
    names = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    assert names == {
        "validate_alert", "investigate", "run_tools", "diagnose", "review", "review_tools",
        "propose_action", "approval_gate", "escalate", "report",
    }


def test_happy_path_visits_nodes_in_order_and_writes_files(mock_deps_factory):
    deps = mock_deps_factory([tool_call("get_metric_changes", {}), "cart cpu [t1]", as_json(GOOD_DIAGNOSIS), AGREE, SCALE])
    result = run_incident(CASE, deps, auto_approve=True)
    assert [s["node"] for s in result["steps"]] == [
        "validate_alert", "investigate", "run_tools", "investigate", "diagnose",
        "review", "propose_action", "approval_gate", "report",
    ]
    run_dir = deps.settings.runs_dir / result["run_id"]
    assert {p.name for p in run_dir.iterdir()} == {"report.json", "report.md", "trace.json"}
    assert "Root cause: cartservice (fault type: cpu)" in (run_dir / "report.md").read_text()
    assert result["report"]["runbook_id"] == "rb-cpu-saturation"
