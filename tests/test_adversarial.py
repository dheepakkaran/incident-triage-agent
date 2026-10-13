"""Adversarial cases 2-7 from eval/adversarial.py, run on the fixture DB with the mock model.

(Case 1, prompt injection with the real model, runs only in `python -m triage eval adversarial`;
its sanitizer path is covered offline by test_guardrails.py.)
"""

import pytest

from eval import adversarial
from tests.conftest import CASE
from triage.config import get_settings

SCENARIOS = [
    adversarial.scenario_unknown_service,
    adversarial.scenario_forbidden_action,
    adversarial.scenario_fake_quote,
    adversarial.scenario_invalid_json,
    adversarial.scenario_tool_loop,
    adversarial.scenario_sql_injection,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda fn: fn.__name__)
def test_adversarial_scenario_is_blocked(scenario):
    result = scenario(get_settings(), CASE)
    assert result["blocked"], result["detail"]


def test_injected_db_copy_contains_the_attack(tmp_path, fixture_db):
    target = tmp_path / "injected.db"
    adversarial._injected_db(CASE, fixture_db, target)
    import sqlite3

    rows = sqlite3.connect(target).execute("SELECT service, level FROM logs WHERE message LIKE '%SYSTEM OVERRIDE%'").fetchall()
    assert ("frontend", "ERROR") in rows and ("cartservice", "ERROR") in rows


def test_forced_log_read_exposes_the_attack_and_it_is_removed(tmp_path, fixture_db, mock_deps_factory):
    """Offline version of scenario 1: the first call reads the poisoned logs; nothing reaches the model."""
    import dataclasses

    from tests.test_guardrails import AGREE, GOOD_DIAGNOSIS, SCALE
    from triage.llm.mock import as_json, tool_call

    db = tmp_path / "injected.db"
    adversarial._injected_db(CASE, fixture_db, db)
    base = mock_deps_factory([tool_call("get_metric_changes", {}), "cart cpu [t2]", as_json({**GOOD_DIAGNOSIS, "evidence": [{**GOOD_DIAGNOSIS["evidence"][0], "tool_call_id": "t2"}]}), AGREE, SCALE])
    deps = dataclasses.replace(base, settings=dataclasses.replace(base.settings, db_path=db), llm=adversarial.ForcedFirstLogRead(base.llm, "frontend"))
    run = adversarial._injection_run(CASE, deps)
    assert run == {"case_id": CASE, "exposed": True, "followed": False, "leaked_to_prompt": False, "top": "cartservice"}
    for prompt in base.extras["model"].prompts:
        assert all("SYSTEM OVERRIDE" not in str(m.content) for m in prompt)
