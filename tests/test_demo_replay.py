"""The demo's replay mode: recorded replies drive the real graph to the same result, and the
approval gate still pauses and resumes (including after a rejection)."""

import sys
from pathlib import Path

from tests.conftest import CASE
from tests.test_guardrails import AGREE, GOOD_DIAGNOSIS, SCALE
from triage.agent.graph import build_graph, resume_incident, run_incident
from triage.agent.nodes import Deps
from triage.llm.mock import as_json, tool_call
from triage.llm.providers import LLMClient, ModelHandle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))
import core  # noqa: E402
import orbs  # noqa: E402, F401


def recorded_run(mock_deps_factory) -> dict:
    deps = mock_deps_factory([tool_call("get_metric_changes", {}), "cart cpu [t1]", as_json(GOOD_DIAGNOSIS), AGREE, SCALE])
    return run_incident(CASE, deps, auto_approve=True)


def replay_deps(recorded: dict, settings) -> Deps:
    model = core.ReplayChatModel(recorded["llm_calls"])
    return Deps(llm=LLMClient([ModelHandle("replay", "recorded", model)], cache=None), settings=settings, reviewer_enabled=True)


def test_replay_reproduces_the_recorded_diagnosis_and_pauses_for_approval(mock_deps_factory):
    recorded = recorded_run(mock_deps_factory)
    deps = replay_deps(recorded, mock_deps_factory([]).settings)
    graph = build_graph(deps)
    paused = run_incident(CASE, deps, graph=graph)  # no auto-approve in the demo
    assert "__interrupt__" in paused
    assert paused["diagnosis"] == recorded["diagnosis"]
    done = resume_incident(graph, paused["run_id"], approved=True)
    assert done["status"] == "diagnosed"


def test_replay_survives_a_rejection(mock_deps_factory):
    recorded = recorded_run(mock_deps_factory)
    deps = replay_deps(recorded, mock_deps_factory([]).settings)
    graph = build_graph(deps)
    paused = run_incident(CASE, deps, graph=graph)
    again = resume_incident(graph, paused["run_id"], approved=False, note="not convinced")
    assert "__interrupt__" in again  # re-investigated and proposed again from the recording
    done = resume_incident(graph, paused["run_id"], approved=False, note="still no")
    assert done["status"] == "escalated"


def test_daily_cap(tmp_path):
    cap = core.DailyCap(2, tmp_path / "usage.json")
    assert [cap.take(), cap.take(), cap.take()] == [True, True, False]
    assert cap.used() == 2


def test_chat_view_tells_the_story_in_order(mock_deps_factory):
    """Each node's output becomes plain-language messages from Scout, Skeptic and the system."""
    import chat

    deps = mock_deps_factory([tool_call("get_metric_changes", {}), "cart cpu [t1]", as_json(GOOD_DIAGNOSIS), AGREE, SCALE])
    graph = build_graph(deps)
    from triage.agent.graph import initial_state, thread_config

    state = initial_state(CASE, deps)
    transcript = []
    for update in graph.stream(state, thread_config(state["run_id"]), stream_mode="updates"):
        for node, delta in update.items():
            if node != "__interrupt__":
                transcript += chat.messages_for(node, delta, graph.get_state(thread_config(state["run_id"])).values)
    speakers = [entry[0] for entry in transcript]
    assert speakers.index("scout") < speakers.index("skeptic")
    text = chat.render(transcript)
    assert "My diagnosis: <b>cartservice</b> has a <b>cpu</b> fault" in text
    assert "I agree." in text and "Waiting for your approval." in text
    assert 'class="orb scout' in text and 'class="orb skeptic' in text
    assert 'orb skeptic happy' in text  # Skeptic agreed, so it smiles


def test_confidence_meter_levels_and_handoff_and_summary():
    import chat

    assert 'class="meter high"' in chat._confidence_meter(0.9)
    assert 'class="meter mid"' in chat._confidence_meter(0.7)
    assert 'class="meter low"' in chat._confidence_meter(0.3)
    html = chat.render([("handoff", "sent back", "skeptic>scout")])
    assert 'class="handoff' in html and "orb skeptic" in html and "orb scout" in html
    values = {
        "status": "diagnosed",
        "diagnosis": {"candidates": [{"service": "cartservice", "fault_type": "cpu", "confidence": 0.9}]},
        "action": {"action": "scale_service", "target_service": "cartservice", "runbook_id": "rb-cpu-saturation"},
        "approval": {"approved": True, "note": "", "auto": False},
        "guardrail_events": [{}, {}],
        "tool_calls_used": 3,
    }
    who, card = chat.summary_card(values, 12.34, "agent time, live")
    assert who == "summary"
    assert "cartservice</b> · cpu fault" in card and "approved by you" in card and "12.3 s" in card
    assert ">2<" in card  # guardrails fired


def test_team_reactions():

    assert orbs.team_moods("happy", "doubt") == ("puzzled", "doubt")  # Scout looks at Skeptic
    assert orbs.team_moods("talk", "happy") == ("happy", "happy")  # team win
    assert orbs.team_moods("happy", "") == ("happy", "suspicious")  # Skeptic eyes the new diagnosis
    assert orbs.team_moods("think", "") == ("think", "")
