"""Human-in-the-loop: the approval gate pauses with interrupt(), and resumes from the checkpoint."""

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from tests.conftest import CASE
from tests.test_guardrails import AGREE, GOOD_DIAGNOSIS, SCALE
from triage.agent.graph import build_graph, resume_incident, run_incident
from triage.llm.mock import as_json, tool_call

SECOND_DIAGNOSIS = {**GOOD_DIAGNOSIS, "summary": "re-checked: still cartservice cpu"}


def first_pass():
    return [tool_call("get_metric_changes", {}), "cartservice cpu [t1]", as_json(GOOD_DIAGNOSIS), AGREE, SCALE]


def second_pass():
    # After a rejection the investigator gets one more turn, then diagnose -> review -> action again.
    return ["still cartservice [t1]", as_json(SECOND_DIAGNOSIS), AGREE, SCALE]


def test_risky_action_pauses_then_approve_resumes_and_simulates(mock_deps_factory):
    deps = mock_deps_factory(first_pass())
    graph = build_graph(deps)
    paused = run_incident(CASE, deps, graph=graph)

    assert "__interrupt__" in paused
    request = paused["__interrupt__"][0].value
    assert request["action"] == "scale_service" and request["target_service"] == "cartservice"
    assert request["evidence"][0]["tool_call_id"] == "t1"
    assert not (deps.settings.runs_dir / "actions.log").exists()  # nothing happens while paused

    done = resume_incident(graph, paused["run_id"], approved=True, note="go ahead")
    assert done["status"] == "diagnosed"
    assert done["approval"] == {"approved": True, "note": "go ahead", "auto": False}
    assert "scale_service" in (deps.settings.runs_dir / "actions.log").read_text()


def test_reject_triggers_one_reinvestigation_with_the_note(mock_deps_factory):
    deps = mock_deps_factory(first_pass() + second_pass())
    graph = build_graph(deps)
    paused = run_incident(CASE, deps, graph=graph)

    paused_again = resume_incident(graph, paused["run_id"], approved=False, note="cpu graphs look normal to me")
    assert "__interrupt__" in paused_again  # a second proposal is waiting
    nodes = [s["node"] for s in paused_again["steps"]]
    assert nodes.count("investigate") == 3 and nodes.count("diagnose") == 2
    # The human's note reached both the investigator and the diagnosis prompt.
    prompts = deps.extras["model"].prompts
    assert "cpu graphs look normal to me" in prompts[5][-1].content
    assert "cpu graphs look normal to me" in prompts[6][-1].content

    done = resume_incident(graph, paused["run_id"], approved=True)
    assert done["status"] == "diagnosed" and done["rejections"] == 1


def test_second_rejection_escalates(mock_deps_factory):
    deps = mock_deps_factory(first_pass() + second_pass())
    graph = build_graph(deps)
    paused = run_incident(CASE, deps, graph=graph)
    resume_incident(graph, paused["run_id"], approved=False, note="no")
    done = resume_incident(graph, paused["run_id"], approved=False, note="still no")

    assert done["status"] == "escalated"
    assert "rejected the proposal twice" in done["escalation_note"]
    assert not (deps.settings.runs_dir / "actions.log").exists()


def test_paused_run_resumes_from_sqlite_checkpoint_in_a_new_graph(mock_deps_factory, tmp_path):
    """Simulates a process restart: a new graph object, same checkpoint DB, same thread id."""
    deps = mock_deps_factory(first_pass())
    db = tmp_path / "checkpoints.db"

    with sqlite3.connect(db, check_same_thread=False) as conn:
        paused = run_incident(CASE, deps, graph=build_graph(deps, SqliteSaver(conn)))
    assert "__interrupt__" in paused

    with sqlite3.connect(db, check_same_thread=False) as conn:
        done = resume_incident(build_graph(deps, SqliteSaver(conn)), paused["run_id"], approved=True)
    assert done["status"] == "diagnosed"


def test_auto_approve_is_recorded(mock_deps_factory):
    deps = mock_deps_factory(first_pass())
    done = run_incident(CASE, deps, auto_approve=True)
    assert "__interrupt__" not in done
    assert done["approval"]["auto"] is True
    assert done["report"]["approval"]["note"].startswith("auto-approved")
