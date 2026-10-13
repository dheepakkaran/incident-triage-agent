"""Download a 20-case subset of RCAEval RE2 (Online Boutique) from the official Hugging Face copy.

RCAEval's README recommends the Hugging Face Parquet copy because it can be fetched one
case at a time. We read the case index first, pick 20 cases, then download only those.

This module knows the labels (it has to, to pick cases evenly across fault types). It
writes the original folder names to data/raw/selection.json. Nothing under
triage/agent or triage/tools may read that file or data/labels.json.
"""

import json
import random

import pandas as pd
from huggingface_hub import hf_hub_download, snapshot_download
from rich.console import Console

from triage.config import get_settings

REPO_ID = "phamquiluan/RCAEval"
DATASET = "RE2-OB"  # one system: Online Boutique, which has metrics, logs and traces
N_CASES = 20
SEED = 42
MIN_PERIOD_STEPS = 600  # need 10 minutes before and after injection (metrics are 1 Hz)

console = Console()


def select_cases(index: pd.DataFrame, n: int = N_CASES, seed: int = SEED) -> list[str]:
    """Pick n cases spread as evenly as possible over fault types, then over services."""
    rng = random.Random(seed)
    pool = index[
        (index.dataset == DATASET)
        & (index.normal_timesteps >= MIN_PERIOD_STEPS)
        & (index.faulty_timesteps >= MIN_PERIOD_STEPS)
    ]
    faults = sorted(pool.fault.unique())
    per_fault = {f: n // len(faults) for f in faults}
    for fault in rng.sample(faults, n % len(faults)):  # the remainder goes to random fault types
        per_fault[fault] += 1

    service_counts = {s: 0 for s in sorted(pool.root_cause_service.unique())}
    chosen = []
    for fault in faults:
        rows = pool[pool.fault == fault]
        services = sorted(rows.root_cause_service.unique(), key=lambda s: (service_counts[s], rng.random()))
        for service in services[: per_fault[fault]]:
            options = sorted(rows[rows.root_cause_service == service].case)
            chosen.append(rng.choice(options))
            service_counts[service] += 1
    return chosen


def order_cases(chosen: list[str], index: pd.DataFrame, seed: int = SEED) -> list[str]:
    """Shuffle, but make sure the 5 dev cases cover 5 different fault types (better for tuning)."""
    rng = random.Random(seed + 1)
    shuffled = rng.sample(chosen, len(chosen))
    fault_of = dict(zip(index.case, index.fault, strict=True))
    dev, seen = [], set()
    for case in shuffled:
        if fault_of[case] not in seen and len(dev) < 5:
            dev.append(case)
            seen.add(fault_of[case])
    return dev + [c for c in shuffled if c not in dev]


def download() -> list[str]:
    settings = get_settings()
    raw = settings.raw_dir
    raw.mkdir(parents=True, exist_ok=True)

    console.print(f"Fetching case index from hf://datasets/{REPO_ID} ...")
    index_path = hf_hub_download(REPO_ID, "cases.parquet", repo_type="dataset", local_dir=raw)
    index = pd.read_parquet(index_path)

    ordered = order_cases(select_cases(index), index)
    console.print(f"Downloading {len(ordered)} {DATASET} cases (about 10 MB each) ...")
    snapshot_download(
        REPO_ID,
        repo_type="dataset",
        allow_patterns=[f"{case}/*" for case in ordered],
        local_dir=raw,
    )
    (raw / "selection.json").write_text(json.dumps({"dataset": DATASET, "seed": SEED, "cases": ordered}, indent=2))
    console.print(f"[green]Done.[/green] {len(ordered)} cases in {raw}")
    return ordered
