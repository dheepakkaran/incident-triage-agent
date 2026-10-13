"""Main eval: run the agent on a split with a fixed approval policy and score it against the labels.

Rules: prompts are tuned on the dev split only. The test split is run once at the end,
and that run is what gets reported.
Every case is run with --auto-approve semantics (recorded in each result).
"""

import time

from rich.console import Console

from eval.common import load_labels, load_split, mean, now, rate, result_path, save
from triage.agent.graph import build_graph, run_incident
from triage.agent.nodes import Deps
from triage.agent.prompts import PROMPT_VERSION
from triage.config import get_settings
from triage.llm.providers import LLMClient
from triage.tools.runbooks import load_runbooks

console = Console()


def score_case(case_id: str, result: dict, label: dict, threshold: float, runbook_faults: dict) -> dict:
    diagnosis = result.get("diagnosis") or {}
    candidates = diagnosis.get("candidates") or []
    services = [c["service"] for c in candidates]
    top = candidates[0] if candidates else {}
    chosen = result.get("action") or result.get("blocked_action") or {}
    runbook_id = chosen.get("runbook_id")
    calls = result.get("llm_calls") or []
    top1 = bool(services) and services[0] == label["root_cause_service"]
    return {
        "case_id": case_id,
        "label": {"service": label["root_cause_service"], "fault_type": label["fault_type"]},
        "predicted": {"top3": services, "fault_type": top.get("fault_type"), "confidence": top.get("confidence")},
        "top1": top1,
        "top3": label["root_cause_service"] in services,
        "fault_type_ok": top.get("fault_type") == label["fault_type"],
        "runbook_id": runbook_id,
        "runbook_hit": runbook_faults.get(runbook_id) == label["fault_type"],
        "status": result.get("status"),
        "escalated": result.get("status") != "diagnosed",
        "wrong_but_confident": bool(candidates) and not top1 and (top.get("confidence") or 0) >= threshold,
        "reviews": [r["verdict"] for r in result.get("reviews") or []],
        "tool_calls": result.get("tool_calls_used", 0),
        "reviewer_tool_calls": result.get("reviewer_tool_calls", 0),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "llm_latency_s": round(sum(c["latency_s"] for c in calls), 2),
        "llm_calls": len(calls),
        "cached_calls": sum(1 for c in calls if c["cached"]),
        "providers": sorted({f"{c['provider']}:{c['model']}" for c in calls}),
        "guardrail_events": [f"{e['layer']}/{e['check']}" for e in result.get("guardrail_events") or []],
        "escalation_reason": result.get("escalation_reason"),
        "run_id": result.get("run_id"),
    }


def summarize(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "top1": rate([r["top1"] for r in rows]),
        "top3": rate([r["top3"] for r in rows]),
        "fault_type": rate([r["fault_type_ok"] for r in rows]),
        "runbook_hit": rate([r["runbook_hit"] for r in rows]),
        "escalation_rate": rate([r["escalated"] for r in rows]),
        "wrong_but_confident": sum(r["wrong_but_confident"] for r in rows),
        "avg_tool_calls": mean([r["tool_calls"] for r in rows]),
        "avg_tokens": mean([r["input_tokens"] + r["output_tokens"] for r in rows]),
        "avg_llm_latency_s": mean([r["llm_latency_s"] for r in rows]),
        "avg_wall_s": mean([r["wall_s"] for r in rows]),
        "errors": sum(1 for r in rows if r["status"] == "error"),
    }


def run_eval(split: str, reviewer: str | None = None, provider: str | None = None) -> dict:
    settings = get_settings()
    reviewer_enabled = settings.reviewer_enabled if reviewer is None else reviewer == "on"
    llm = LLMClient.from_settings(settings, only=provider, role="investigator")
    reviewer_llm = LLMClient.from_settings(settings, only=provider, role="reviewer")
    deps = Deps(llm=llm, reviewer_llm=reviewer_llm, settings=settings, reviewer_enabled=reviewer_enabled)
    graph = build_graph(deps)
    labels = load_labels()
    runbook_faults = {rb_id: book["fault_type"] for rb_id, book in load_runbooks().items()}
    console.print(f"eval split={split} model={llm.primary.provider}:{llm.primary.model} reviewer={'on' if reviewer_enabled else 'off'}")

    rows = []
    for case_id in load_split(split):
        started = time.perf_counter()
        try:
            result = run_incident(case_id, deps, auto_approve=True, graph=graph)
        except Exception as err:  # one broken case must not lose the whole run
            result = {"status": "error", "escalation_reason": f"{type(err).__name__}: {err}"}
        row = score_case(case_id, result, labels[case_id], settings.confidence_threshold, runbook_faults)
        row["wall_s"] = round(time.perf_counter() - started, 1)
        rows.append(row)
        mark = "[green]ok[/green]" if row["top1"] else "[red]miss[/red]"
        console.print(
            f"{case_id} {mark} predicted={row['predicted']['top3']}/{row['predicted']['fault_type']} "
            f"status={row['status']} tools={row['tool_calls']} reviews={row['reviews']} {row['wall_s']}s"
        )

    metrics = summarize(rows)
    payload = {
        "kind": "agent",
        "date": now(),
        "split": split,
        "provider": llm.primary.provider,
        "model": llm.primary.model,
        "provider_chain": [f"{h.provider}:{h.model}" for h in llm.handles],
        "reviewer_model": f"{reviewer_llm.primary.provider}:{reviewer_llm.primary.model}" if reviewer_enabled else None,
        "reviewer_provider_chain": [f"{h.provider}:{h.model}" for h in reviewer_llm.handles],
        "prompt_version": PROMPT_VERSION,
        "temperature": 0,
        "reviewer_enabled": reviewer_enabled,
        "max_tool_calls": settings.max_tool_calls,
        "reviewer_max_tool_calls": settings.reviewer_max_tool_calls,
        "confidence_threshold": settings.confidence_threshold,
        "approval_policy": "auto-approve (fixed eval policy)",
        "metrics": metrics,
        "cases": rows,
    }
    variant = f"{PROMPT_VERSION}_reviewer-{'on' if reviewer_enabled else 'off'}"
    path = save(result_path("agent", llm.primary.model, split, variant), payload)
    console.print(f"{metrics}\nwrote {path}")
    return payload
