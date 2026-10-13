"""Label leakage tests. The eval is meaningless if the agent can see the answer.

1. Static: no module under triage/agent or triage/tools may mention labels, the raw case
   index, or the original folder selection, or import the eval / data-download code.
2. Runtime: a full agent run (mock model, fixture DB) never opens a labels file.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARDED_DIRS = [ROOT / "triage" / "agent", ROOT / "triage" / "tools"]
FORBIDDEN_TEXT = ["label", "selection.json", "cases.parquet", "original_name", "root_cause_service"]
FORBIDDEN_IMPORTS = ("eval", "triage.data.download", "triage.data.prepare")


def guarded_files() -> list[Path]:
    return [p for d in GUARDED_DIRS for p in sorted(d.rglob("*.py"))]


def test_guarded_dirs_exist():
    assert guarded_files(), "expected source files under triage/agent and triage/tools"


def test_agent_and_tools_never_mention_labels():
    offenders = []
    for path in guarded_files():
        source = path.read_text().lower()
        offenders += [f"{path.relative_to(ROOT)}: '{word}'" for word in FORBIDDEN_TEXT if word in source]
    assert not offenders, "label-related text found in agent/tools code:\n" + "\n".join(offenders)


def test_agent_and_tools_never_import_eval_or_raw_data_code():
    offenders = []
    for path in guarded_files():
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == "eval" or name.startswith(FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_agent_run_never_opens_labels_file(fixture_db, mock_deps_factory):
    """Run the whole graph with an audit hook that records every file the process opens."""
    from triage.agent.graph import run_incident
    from triage.llm.mock import as_json, tool_call

    opened: list[str] = []

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], str | Path):
            opened.append(str(args[0]))

    sys.addaudithook(hook)  # audit hooks cannot be removed; it only appends to a list
    diagnosis = {
        "candidates": [{"service": "cartservice", "fault_type": "cpu", "confidence": 0.9}],
        "evidence": [{"tool_call_id": "t1", "quote": "cartservice cpu_usage", "why_it_matters": "cpu up"}],
        "summary": "cartservice cpu is saturated",
    }
    deps = mock_deps_factory(
        [
            tool_call("get_metric_changes", {}),
            "done",
            as_json(diagnosis),
            as_json({"verdict": "agree", "reason": "fits", "evidence": []}),
            as_json({"action": "scale_service", "target_service": "cartservice", "runbook_id": "rb-cpu-saturation", "reason": "cpu"}),
        ]
    )
    run_incident("case_001", deps, auto_approve=True)
    assert opened, "audit hook saw no file opens at all; the test is not working"
    assert not [p for p in opened if "label" in Path(p).name.lower()], "the agent opened a labels file"
