"""Look inside the escalated cases of one eval run: what did the rejected attempts say?

    python -m eval.analyze_escalations eval/results/<..._agent.json>

For each escalated case it reads the run's trace (runs/<run_id>/trace.json), pulls the
service and fault type from every diagnosis attempt, and compares them with the label.
This is analysis only: nothing here changes the agent or re-runs a case.
"""

import json
import sys
from pathlib import Path

from eval.common import RESULTS_DIR, save
from triage.config import ROOT
from triage.guardrails.output import extract_json


def attempts(trace: dict) -> list[dict]:
    out = []
    for call in trace["llm_calls"]:
        if call["purpose"].startswith("diagnose"):
            data = extract_json(call["reply"]["content"]) or {}
            top = (data.get("candidates") or [{}])[0]
            out.append({"service": top.get("service"), "fault_type": top.get("fault_type")})
    return out


def analyze(results_file: Path) -> dict:
    data = json.loads(results_file.read_text())
    rows = []
    for case in data["cases"]:
        if not case["escalated"]:
            continue
        trace = json.loads((ROOT / "runs" / case["run_id"] / "trace.json").read_text())
        tried = attempts(trace)
        rows.append(
            {
                "case_id": case["case_id"],
                "label": case["label"],
                "escalation_reason": case["escalation_reason"],
                "final_top": case["predicted"]["top3"][:1],
                "diagnosis_attempts": tried,
                "an_attempt_named_the_right_service": any(a["service"] == case["label"]["service"] for a in tried),
            }
        )
    payload = {
        "kind": "analysis",
        "source": results_file.name,
        "escalated": len(rows),
        "escalated_with_right_service_in_an_attempt": sum(r["an_attempt_named_the_right_service"] for r in rows),
        "cases": rows,
    }
    out = RESULTS_DIR / results_file.name.replace("_agent.json", "_escalations.json")
    save(out, payload)
    print(f"{payload['escalated']} escalated, right service in an attempt: {payload['escalated_with_right_service_in_an_attempt']} -> {out.name}")
    return payload


if __name__ == "__main__":
    analyze(Path(sys.argv[1]))
