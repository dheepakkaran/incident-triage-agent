"""Non-LLM baseline: rank services by their largest relative metric change in the incident window.

Same cases, same top-1/top-3 scoring as the agent. It also guesses a fault type from the
kind of metric that moved most, using the same mapping the agent's prompt describes.
It reads metrics through the same tool code the agent uses (triage.tools.metrics).
"""

from rich.console import Console

from eval.common import load_labels, load_split, now, rate, result_path, save
from triage.config import get_settings
from triage.tools.db import ToolContext
from triage.tools.metrics import metric_changes

METRIC_TO_FAULT = {
    "cpu_usage": "cpu",
    "memory_bytes": "mem",
    "disk_io_bytes": "disk",
    "open_sockets": "socket",
    "error_rate": "loss",
    "latency_p50_s": "delay",
    "latency_p90_s": "delay",
    "request_rate": "unknown",
}
METHOD = "largest relative metric change"


def rank_services(ctx: ToolContext) -> list[tuple[str, float, str]]:
    """(service, score, metric) sorted by score, where score = the service's biggest |relative change|."""
    best: dict[str, tuple[float, str]] = {}
    for row in metric_changes(ctx):
        score = abs(row["change"])
        if score > best.get(row["service"], (-1.0, ""))[0]:
            best[row["service"]] = (score, row["metric"])
    return sorted(((s, sc, m) for s, (sc, m) in best.items()), key=lambda item: item[1], reverse=True)


def run_baseline(split: str) -> dict:
    console = Console()
    settings = get_settings()
    labels = load_labels()
    rows = []
    for case_id in load_split(split):
        ranking = rank_services(ToolContext.for_case(case_id, settings.db_path))
        top3 = [service for service, _, _ in ranking[:3]]
        fault = METRIC_TO_FAULT.get(ranking[0][2], "unknown") if ranking else None
        label = labels[case_id]
        rows.append(
            {
                "case_id": case_id,
                "label": {"service": label["root_cause_service"], "fault_type": label["fault_type"]},
                "predicted": {"top3": top3, "fault_type": fault, "top_metric": ranking[0][2] if ranking else None},
                "top1": bool(top3) and top3[0] == label["root_cause_service"],
                "top3": label["root_cause_service"] in top3,
                "fault_type_ok": fault == label["fault_type"],
            }
        )
        console.print(f"{case_id} {'ok' if rows[-1]['top1'] else 'miss'} {top3} {fault}")
    metrics = {
        "n": len(rows),
        "top1": rate([r["top1"] for r in rows]),
        "top3": rate([r["top3"] for r in rows]),
        "fault_type": rate([r["fault_type_ok"] for r in rows]),
    }
    payload = {"kind": "baseline", "method": METHOD, "date": now(), "split": split, "metrics": metrics, "cases": rows}
    path = save(result_path("baseline", "metric-change", split), payload)
    console.print(f"{metrics}\nwrote {path}")
    return payload
