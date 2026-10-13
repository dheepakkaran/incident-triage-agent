"""Prepare the demo: replays from an eval run, plus a test-split-only telemetry DB.

    python demo/build.py --results eval/results/<..._test_..._agent.json>            # local only
    python demo/build.py --results ... --upload                                      # + publish the DB

The DB (~200 MB) is too big for GitHub, so `--upload` puts it in a public Hugging Face
*dataset* that the app downloads on first start. Licence: RCAEval data is CC-BY-4.0
(Zenodo) / MIT (Hugging Face copy), so a trimmed copy may be shared with credit.
"""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"

DATASET_CARD = """---
license: cc-by-4.0
tags: [root-cause-analysis, microservices, observability, rcaeval]
---

# Incident triage demo telemetry

A trimmed SQLite copy of 15 RCAEval RE2-OB (Online Boutique) failure cases, used by the
incident triage agent demo. Cases are renamed `case_006` to `case_020`; labels are not included.
See the project's `docs/data_notes.md` for how the data was cut and sampled.

**Source and credit:** RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems
with Telemetry Data (Pham et al., WWW 2025). Paper: https://arxiv.org/abs/2412.17015 ·
Code: https://github.com/phamquiluan/RCAEval · Data: https://doi.org/10.5281/zenodo.14590730 (CC-BY-4.0).
"""


def export_replays(results_file: Path, runs_dir: Path, out: Path) -> list[str]:
    """One small JSON per case: what the demo needs to replay the recorded run."""
    data = json.loads(results_file.read_text())
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("case_*.json"):  # never mix replays from different runs
        old.unlink()
    cases = []
    for row in data["cases"]:
        trace = json.loads((runs_dir / row["run_id"] / "trace.json").read_text())
        replay = {
            "case_id": trace["case_id"],
            "config": {**trace["config"], "provider_chain": [f"{data['provider']}:{data['model']}"]},
            "llm_calls": [{"purpose": c["purpose"], "reply": c["reply"]} for c in trace["llm_calls"]],
            "recorded_status": trace["status"],
            "source": results_file.name,
        }
        (out / f"{trace['case_id']}.json").write_text(json.dumps(replay))
        cases.append(trace["case_id"])
    return cases


def build_db(source: Path, target: Path, case_ids: list[str]) -> None:
    from triage.data.prepare import SCHEMA

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    conn = sqlite3.connect(target.resolve().as_uri(), uri=True)
    conn.executescript(SCHEMA)
    conn.execute(f"ATTACH DATABASE '{source.resolve().as_uri()}?mode=ro' AS src")
    marks = ",".join("?" * len(case_ids))
    for table in ("cases", "metrics", "logs", "spans", "alerts"):
        conn.execute(f"INSERT INTO {table} SELECT * FROM src.{table} WHERE case_id IN ({marks})", case_ids)
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.execute("VACUUM")
    conn.close()


def upload(db: Path, repo_id: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    api.upload_file(path_or_fileobj=DATASET_CARD.encode(), path_in_repo="README.md", repo_id=repo_id, repo_type="dataset")
    api.upload_file(path_or_fileobj=str(db), path_in_repo="telemetry.db", repo_id=repo_id, repo_type="dataset")
    print(f"uploaded to https://huggingface.co/datasets/{repo_id}")


def main() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DEMO))
    from core import DB_PATH, DB_REPO

    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--upload", action="store_true", help="publish the DB to the public HF dataset")
    args = parser.parse_args()

    cases = export_replays(args.results, ROOT / "runs", DEMO / "replays")
    build_db(ROOT / "data" / "telemetry.db", DB_PATH, cases)
    print(f"{len(cases)} replays in demo/replays, {DB_PATH} {DB_PATH.stat().st_size / 1e6:.1f} MB")
    if args.upload:
        upload(DB_PATH, DB_REPO)


if __name__ == "__main__":
    main()
