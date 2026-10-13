"""Assemble the Hugging Face Space in space/dist/ (gitignored), ready for `hf upload`.

    python space/build.py --results eval/results/<test-split agent result>.json

It copies the app and the `triage` package, exports one replay per test case from the
traces of the given eval run, builds a test-split-only telemetry DB, and writes the
Space README. Licence: RCAEval data is CC-BY-4.0 (Zenodo) / MIT (Hugging Face copy),
so redistributing a trimmed copy with credit is allowed.
"""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACE = ROOT / "space"
DIST = SPACE / "dist"

CARD = """---
title: Incident Triage Agent
emoji: 🚨
colorFrom: red
colorTo: indigo
sdk: gradio
sdk_version: {gradio}
app_file: app.py
python_version: "3.12"
short_description: LangGraph incident triage with guardrails and HITL
license: mit
startup_duration_timeout: 30m
---

First-pass root-cause analysis for microservice incidents (RCAEval RE2, Online Boutique).
Code and full write-up: see the project README. Data: RCAEval (Pham et al., WWW 2025), CC-BY-4.0.
"""


def export_replays(results_file: Path, runs_dir: Path, out: Path) -> list[str]:
    """One small JSON per case: what the Space needs to replay the recorded run."""
    data = json.loads(results_file.read_text())
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for row in data["cases"]:
        trace = json.loads((runs_dir / row["run_id"] / "trace.json").read_text())
        replay = {
            "case_id": trace["case_id"],
            "alert": trace["alert"],
            "config": {**trace["config"], "provider_chain": [f"{data['provider']}:{data['model']}"]},
            "llm_calls": [{"purpose": c["purpose"], "reply": c["reply"]} for c in trace["llm_calls"]],
            "recorded_status": trace["status"],
        }
        (out / f"{trace['case_id']}.json").write_text(json.dumps(replay))
        cases.append(trace["case_id"])
    return cases


def build_db(source: Path, target: Path, case_ids: list[str]) -> None:
    """A copy of telemetry.db with only the given cases (the Space shows the test split)."""
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


def main() -> None:
    import gradio

    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path, help="eval/results/<...>_test_..._agent.json")
    args = parser.parse_args()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    shutil.copy(SPACE / "app.py", DIST / "app.py")
    shutil.copy(SPACE / "requirements.txt", DIST / "requirements.txt")
    shutil.copytree(ROOT / "triage", DIST / "triage", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "runbooks", DIST / "runbooks")
    (DIST / "results").mkdir()
    shutil.copy(ROOT / "eval" / "results" / "RESULTS.md", DIST / "results" / "RESULTS.md")
    (DIST / "README.md").write_text(CARD.format(gradio=gradio.__version__))

    cases = export_replays(args.results, ROOT / "runs", DIST / "replays")
    shutil.copytree(DIST / "replays", SPACE / "replays", dirs_exist_ok=True)  # small, committed for reference
    build_db(ROOT / "data" / "telemetry.db", DIST / "data" / "telemetry.db", cases)
    size = (DIST / "data" / "telemetry.db").stat().st_size / 1e6
    print(f"built {DIST}: {len(cases)} replays, telemetry.db {size:.1f} MB")


if __name__ == "__main__":
    main()
