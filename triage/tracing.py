"""Write each run's files under runs/<run_id>/: report.json, report.md and trace.json.

The trace is the full audit trail: every node visit, every tool call with its arguments
and output, every LLM call with tokens and latency, and every guardrail event.
"""

import json
import time

from triage.agent.prompts import PROMPT_VERSION
from triage.schemas import IncidentReport


def llm_totals(calls: list[dict]) -> dict:
    providers = sorted({f"{c['provider']}:{c['model']}" for c in calls})
    return {
        "calls": len(calls),
        "providers": providers,
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "latency_s": round(sum(c["latency_s"] for c in calls), 2),
        "cached_calls": sum(1 for c in calls if c["cached"]),
    }


def build_report(state: dict) -> IncidentReport:
    return IncidentReport(
        case_id=state["case_id"],
        run_id=state["run_id"],
        status=state["status"],
        alert=state["alert"].get("text", "") if isinstance(state.get("alert"), dict) else str(state.get("alert")),
        diagnosis=state.get("diagnosis"),
        reviews=state.get("reviews") or [],
        runbook_id=(state.get("action") or {}).get("runbook_id"),
        action=state.get("action"),
        approval=state.get("approval"),
        escalation_note=state.get("escalation_note") or (state.get("escalation_reason") if state["status"] != "diagnosed" else None),
        tool_calls_used=state.get("tool_calls_used", 0),
        guardrail_events=state.get("guardrail_events") or [],
        llm=llm_totals(state.get("llm_calls") or []),
        timings=state.get("timings") or {},
        prompt_version=PROMPT_VERSION,
    )


def report_markdown(r: IncidentReport) -> str:
    lines = [f"# Incident report {r.case_id}", "", "```", f"Alert:      {r.alert}"]
    if r.diagnosis:
        top = r.diagnosis.candidates[0]
        lines.append(f"Root cause: {top.service} (fault type: {top.fault_type}) - confidence {top.confidence:.2f}")
        for other in r.diagnosis.candidates[1:]:
            lines.append(f"            also possible: {other.service} ({other.fault_type}, {other.confidence:.2f})")
        for i, e in enumerate(r.diagnosis.evidence):
            prefix = "Evidence:   " if i == 0 else "            "
            lines.append(f"{prefix}[{e.tool_call_id}] {e.quote}")
    for review in r.reviews:
        lines.append(f"Reviewer:   {review.verdict} - {review.reason}")
    if r.runbook_id:
        lines.append(f"Runbook:    {r.runbook_id}")
    if r.action:
        if r.approval is None:
            approval = "needs human approval"
        elif r.approval.auto:
            approval = r.approval.note
        else:
            approval = ("approved" if r.approval.approved else "rejected") + (f": {r.approval.note}" if r.approval.note else "")
        lines.append(f"Proposed:   {r.action.action}({r.action.target_service})  -> {approval}")
    lines.append(f"Status:     {r.status}")
    lines.append("```")
    if r.escalation_note:
        lines += ["", "## Escalation", "", "```", r.escalation_note, "```"]
    if r.diagnosis:
        lines += ["", f"Summary: {r.diagnosis.summary}"]
    lines += [
        "",
        f"Tool calls: {r.tool_calls_used} | LLM calls: {r.llm.get('calls', 0)} | "
        f"tokens in/out: {r.llm.get('input_tokens', 0)}/{r.llm.get('output_tokens', 0)} | "
        f"guardrail events: {len(r.guardrail_events)} | prompt {r.prompt_version}",
    ]
    return "\n".join(lines) + "\n"


def write_run_files(state: dict, deps) -> dict:
    report = build_report(state)
    run_dir = deps.settings.runs_dir / state["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    (run_dir / "report.md").write_text(report_markdown(report))

    settings = deps.settings
    trace = {
        "run_id": state["run_id"],
        "case_id": state["case_id"],
        "written_at": int(time.time()),
        "config": {
            "prompt_version": PROMPT_VERSION,
            "provider_chain": [f"{h.provider}:{h.model}" for h in deps.llm.handles],
            "reviewer_provider_chain": [f"{h.provider}:{h.model}" for h in deps.review_llm.handles],
            "temperature": 0,
            "max_tool_calls": settings.max_tool_calls,
            "reviewer_enabled": deps.reviewer_enabled,
            "reviewer_max_tool_calls": settings.reviewer_max_tool_calls,
            "confidence_threshold": settings.confidence_threshold,
            "auto_approve": bool(state.get("auto_approve")),
        },
        "alert": state.get("alert"),
        "status": state["status"],
        "steps": state.get("steps") or [],
        "tool_results": state.get("tool_results") or {},
        "llm_calls": state.get("llm_calls") or [],
        "guardrail_events": state.get("guardrail_events") or [],
        "diagnosis": state.get("diagnosis"),
        "reviews": state.get("reviews") or [],
        "action": state.get("action"),
        "blocked_action": state.get("blocked_action"),
        "approval": state.get("approval"),
        "escalation_note": state.get("escalation_note"),
        "timings": state.get("timings") or {},
        "llm_totals": report.llm,
    }
    (run_dir / "trace.json").write_text(json.dumps(trace, indent=2, default=str))
    return {**report.model_dump(), "paths": {"dir": str(run_dir), "trace": str(run_dir / "trace.json")}}
