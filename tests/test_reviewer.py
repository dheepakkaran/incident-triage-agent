"""Investigator-reviewer pattern: agree, revise -> fixed, revise -> still revise -> escalate."""

from tests.conftest import CASE
from tests.test_guardrails import AGREE, GOOD_DIAGNOSIS, SCALE
from triage.agent.graph import run_incident
from triage.llm.mock import as_json, tool_call

# The classic mistake: blame the service that shows the symptom.
VICTIM_DIAGNOSIS = {
    "candidates": [{"service": "frontend", "fault_type": "delay", "confidence": 0.8}],
    "evidence": [{"tool_call_id": "t1", "quote": "frontend latency_p90_s: 0.1 -> 0.5", "why_it_matters": "frontend slow"}],
    "summary": "frontend is slow",
}
REVISE_TO_CART = as_json(
    {
        "verdict": "revise",
        "suspected_service": "cartservice",
        "reason": "frontend only waits on cartservice, whose server time jumped",
        "evidence": [{"tool_call_id": "t2", "quote": "inside cartservice (server spans): n 20 -> 20"}],
    }
)


def nodes_of(result):
    return [s["node"] for s in result["steps"]]


def test_agree_goes_straight_to_action(mock_deps_factory):
    deps = mock_deps_factory([tool_call("get_metric_changes", {}), "cart cpu", as_json(GOOD_DIAGNOSIS), AGREE, SCALE])
    result = run_incident(CASE, deps, auto_approve=True)
    assert [r["verdict"] for r in result["reviews"]] == ["agree"]
    assert nodes_of(result).index("review") + 1 == nodes_of(result).index("propose_action")
    assert result["status"] == "diagnosed"


def test_revise_sends_case_back_once_and_fixes_the_diagnosis(mock_deps_factory):
    script = [
        tool_call("get_metric_changes", {}),
        "frontend latency is up",
        as_json(VICTIM_DIAGNOSIS),
        tool_call("get_trace_stats", {"service": "cartservice"}),  # the reviewer checks the dependency
        REVISE_TO_CART,
        "agreed, cartservice cpu [t1] and server time [t2]",  # investigator, second pass
        as_json(GOOD_DIAGNOSIS),
        AGREE,
        SCALE,
    ]
    deps = mock_deps_factory(script)
    result = run_incident(CASE, deps, auto_approve=True)

    assert [r["verdict"] for r in result["reviews"]] == ["revise", "agree"]
    assert result["revision_round"] == 1
    assert result["diagnosis"]["candidates"][0]["service"] == "cartservice"
    assert result["status"] == "diagnosed"
    # The reviewer's tool call went through the same guarded executor and has its own budget.
    assert result["tool_results"]["t2"]["tool"] == "get_trace_stats"
    assert result["tool_calls_used"] == 1 and result["reviewer_tool_calls"] == 1
    # The investigator saw the reviewer's reason.
    investigator_prompt = deps.extras["model"].prompts[5]
    assert "frontend only waits on cartservice" in investigator_prompt[-1].content


def test_second_revise_escalates_with_both_views(mock_deps_factory):
    script = [
        tool_call("get_metric_changes", {}),
        "frontend latency is up",
        as_json(VICTIM_DIAGNOSIS),
        tool_call("get_trace_stats", {"service": "cartservice"}),
        REVISE_TO_CART,
        "I still think it is the frontend",  # the investigator does not change its mind
        as_json(VICTIM_DIAGNOSIS),
        REVISE_TO_CART,
    ]
    deps = mock_deps_factory(script)
    result = run_incident(CASE, deps, auto_approve=True)

    assert result["status"] == "escalated"
    assert "still disagree after one revision" in result["escalation_note"]
    assert "investigator: frontend, reviewer: cartservice" in result["escalation_note"]
    assert len(result["report"]["reviews"]) == 2
    assert "propose_action" not in nodes_of(result)


def test_revise_toward_a_caller_is_rejected(mock_deps_factory):
    """frontend calls cartservice, so blaming the frontend for a cartservice fault is moving blame upstream."""
    to_caller = as_json(
        {
            "verdict": "revise",
            "suspected_service": "frontend",
            "reason": "frontend latency is up",
            "evidence": [{"tool_call_id": "t1", "quote": "frontend latency_p90_s: 0.1 -> 0.5"}],
        }
    )
    script = [tool_call("get_metric_changes", {}), "cart cpu", as_json(GOOD_DIAGNOSIS), to_caller, AGREE, SCALE]
    deps = mock_deps_factory(script)
    result = run_incident(CASE, deps, auto_approve=True)
    assert any("is upstream" in e["detail"] for e in result["guardrail_events"])
    assert [r["verdict"] for r in result["reviews"]] == ["agree"]
    assert result["status"] == "diagnosed"


def test_revise_without_real_evidence_is_retried(mock_deps_factory):
    unsupported = as_json({"verdict": "revise", "suspected_service": "cartservice", "reason": "hunch", "evidence": []})
    script = [tool_call("get_metric_changes", {}), "x", as_json(VICTIM_DIAGNOSIS), unsupported, AGREE, SCALE]
    deps = mock_deps_factory(script)
    result = run_incident(CASE, deps, auto_approve=True)
    assert any("must cite at least one quote" in e["detail"] for e in result["guardrail_events"])
    assert [r["verdict"] for r in result["reviews"]] == ["agree"]


def test_reviewer_tool_budget_and_allowlist(mock_deps_factory):
    script = [
        tool_call("get_metric_changes", {}),
        "x",
        as_json(GOOD_DIAGNOSIS),
        tool_call("query_logs", {"service": "cartservice"}),  # not a reviewer tool
        tool_call("get_trace_stats", {"service": "cartservice"}),
        tool_call("get_trace_stats", {"service": "frontend"}),
        AGREE,  # budget (3) is used up, so this turn is asked without tools
        SCALE,
    ]
    deps = mock_deps_factory(script)
    result = run_incident(CASE, deps, auto_approve=True)
    assert any(e["check"] == "unknown_tool" for e in result["guardrail_events"])
    assert result["reviewer_tool_calls"] == 3
    last_review_prompt = deps.extras["model"].prompts[6]
    assert deps.extras["model"].prompts and last_review_prompt  # the verdict call happened
    assert result["status"] == "diagnosed"


def test_reviewer_disabled_skips_review(mock_deps_factory):
    deps = mock_deps_factory([tool_call("get_metric_changes", {}), "x", as_json(GOOD_DIAGNOSIS), SCALE], reviewer=False)
    result = run_incident(CASE, deps, auto_approve=True)
    assert "review" not in nodes_of(result)
    assert result["status"] == "diagnosed"
