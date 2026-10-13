"""Action guardrails: allowlist, risk level, runbook match, and the simulated executor.

Nothing here touches a real system. An approved action is one JSON line in
runs/actions.log, and that is all.
"""

import json
import time
from pathlib import Path

from triage.schemas import ACTION_TYPES, RISKY_ACTIONS, ActionChoice, ActionProposal


def check_allowlist(choice: ActionChoice) -> str | None:
    if choice.action not in ACTION_TYPES:
        return f"action '{choice.action}' is not in the allowlist {list(ACTION_TYPES)}"
    return None


def check_target(choice: ActionChoice, services: list[str]) -> str | None:
    if choice.action == "no_action":
        return None
    if choice.target_service not in services:
        return f"target service '{choice.target_service}' is not a known service"
    return None


def check_runbook_match(choice: ActionChoice, runbooks: dict[str, dict]) -> str | None:
    """The action must be one the chosen runbook allows. escalate_to_human is always allowed."""
    if choice.action == "escalate_to_human":
        return None
    runbook = runbooks.get(choice.runbook_id)
    if runbook is None:
        return f"runbook '{choice.runbook_id}' was not among the retrieved runbooks {list(runbooks)}"
    if choice.action not in runbook["allowed_actions"]:
        return f"runbook {choice.runbook_id} does not allow '{choice.action}' (allowed: {runbook['allowed_actions']})"
    return None


def to_proposal(choice: ActionChoice) -> ActionProposal:
    risky = choice.action in RISKY_ACTIONS
    return ActionProposal(**choice.model_dump(), risk="high" if risky else "low", needs_approval=risky)


def simulate_action(proposal: dict, case_id: str, run_id: str, runs_dir: Path) -> str:
    """The only 'execution' this project does: append a line to runs/actions.log."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": int(time.time()),
        "case_id": case_id,
        "run_id": run_id,
        "action": proposal["action"],
        "target_service": proposal["target_service"],
        "simulated": True,
    }
    with open(runs_dir / "actions.log", "a") as log:
        log.write(json.dumps(line) + "\n")
    return f"SIMULATED {proposal['action']}({proposal['target_service']}) -> runs/actions.log"
