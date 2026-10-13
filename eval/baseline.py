"""Non-LLM baselines, scored exactly like the agent (top-1 / top-3 service, fault type).

1. metric-change: rank services by their largest relative metric change in the incident
   window, using the same tool code the agent uses (triage.tools.metrics).
2. baro: BARO from RCAEval's own package (RCAEval.e2e.baro, metrics only), run the way
   RCAEval runs it: on the case's full raw metrics.parquet with its inject time.

Both guess a fault type from the kind of metric ranked first.
"""

import json

import pandas as pd
from rich.console import Console

from eval.common import load_labels, load_split, now, rate, result_path, save
from triage.config import get_settings
from triage.tools.db import ToolContext
from triage.tools.metrics import metric_changes

READABLE_TO_FAULT = {
    "cpu_usage": "cpu",
    "memory_bytes": "mem",
    "disk_io_bytes": "disk",
    "open_sockets": "socket",
    "error_rate": "loss",
    "latency_p50_s": "delay",
    "latency_p90_s": "delay",
}
RAW_TO_FAULT = {"cpu": "cpu", "mem": "mem", "diskio": "disk", "socket": "socket", "error": "loss", "latency-50": "delay", "latency-90": "delay"}
METHODS = {
    "metric-change": "largest relative metric change",
    "baro": "BARO (RCAEval 1.8.0, metrics only)",
}


def rank_metric_change(case_id: str) -> tuple[list[str], str | None]:
    """Services by their biggest |relative change|; fault type from that metric."""
    best: dict[str, tuple[float, str]] = {}
    for row in metric_changes(ToolContext.for_case(case_id, get_settings().db_path)):
        score = abs(row["change"])
        if score > best.get(row["service"], (-1.0, ""))[0]:
            best[row["service"]] = (score, row["metric"])
    ranked = sorted(best.items(), key=lambda item: item[1][0], reverse=True)
    fault = READABLE_TO_FAULT.get(ranked[0][1][1], "unknown") if ranked else None
    return [service for service, _ in ranked], fault


def rank_baro(case_id: str) -> tuple[list[str], str | None]:
    from RCAEval.e2e.baro import baro

    settings = get_settings()
    original = json.loads((settings.raw_dir / "selection.json").read_text())["cases"][int(case_id.split("_")[1]) - 1]
    folder = settings.raw_dir / original
    metrics = pd.read_parquet(folder / "metrics.parquet")
    inject = int((folder / "inject_time.txt").read_text().strip())
    ranks = baro(metrics, inject_time=inject, dataset="re2-ob")["ranks"]  # e.g. ["emailservice_mem", ...]
    services = []
    for name in ranks:
        service = name.rsplit("_", 1)[0]
        if service not in services:
            services.append(service)
    fault = RAW_TO_FAULT.get(ranks[0].rsplit("_", 1)[1], "unknown") if ranks else None
    return services, fault


RANKERS = {"metric-change": rank_metric_change, "baro": rank_baro}


def run_baseline(split: str) -> list[dict]:
    console = Console()
    labels = load_labels()
    payloads = []
    for key, method in METHODS.items():
        rows = []
        for case_id in load_split(split):
            services, fault = RANKERS[key](case_id)
            label = labels[case_id]
            top3 = services[:3]
            rows.append(
                {
                    "case_id": case_id,
                    "label": {"service": label["root_cause_service"], "fault_type": label["fault_type"]},
                    "predicted": {"top3": top3, "fault_type": fault},
                    "top1": bool(top3) and top3[0] == label["root_cause_service"],
                    "top3": label["root_cause_service"] in top3,
                    "fault_type_ok": fault == label["fault_type"],
                }
            )
        metrics = {
            "n": len(rows),
            "top1": rate([r["top1"] for r in rows]),
            "top3": rate([r["top3"] for r in rows]),
            "fault_type": rate([r["fault_type_ok"] for r in rows]),
        }
        payload = {"kind": "baseline", "method": method, "date": now(), "split": split, "metrics": metrics, "cases": rows}
        path = save(result_path("baseline", key, split), payload)
        console.print(f"{method}: {metrics} -> {path.name}")
        payloads.append(payload)
    return payloads
