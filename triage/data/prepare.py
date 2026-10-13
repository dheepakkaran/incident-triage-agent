"""Turn the 20 downloaded RE2-OB cases into data/telemetry.db, with no label leakage.

Leakage rules (the eval is meaningless without them):
  - cases are renamed case_001..case_020; the original folder names (which contain the
    root cause service and fault type) go only to data/labels.json;
  - telemetry is scanned for anything naming the injected fault, and matches are masked;
  - only eval/ code reads labels.json (tests/test_leakage.py enforces this).

Size: each case keeps 10 minutes before and 10 minutes after injection. To keep the
database small (the raw subset is ~2.7M log lines and ~6M spans):
  - metrics are averaged into 10-second buckets;
  - identical log lines from one service within a 10-second bucket become one row with a
    `count` column, so log counts stay exact;
  - 1 in 5 traces is kept, whole, chosen by a hash of the trace id (same traces every run),
    and health-check / telemetry-export spans are dropped;
  - trace and span ids are replaced by small integers (they are random ids anyway).
"""

import hashlib
import json
import re
import sqlite3
from collections import Counter

import pandas as pd
from rich.console import Console
from rich.table import Table

from triage.config import ROOT, get_settings
from triage.data.alerts import build_alert

WINDOW_S = 600
METRIC_STEP_S = 10
LOG_BUCKET_S = 10
TRACE_SAMPLE_EVERY = 5
N_DEV = 5

# Raw metric suffix -> readable name used everywhere in this project.
METRIC_NAMES = {
    "cpu": "cpu_usage",  # percent of one core
    "mem": "memory_bytes",
    "diskio": "disk_io_bytes",
    "socket": "open_sockets",
    "workload": "request_rate",  # requests per second
    "error": "error_rate",  # errors per second
    "latency-50": "latency_p50_s",
    "latency-90": "latency_p90_s",
}
SERVICE_ALIASES = {"frontendservice": "frontend"}
NOISE_OPERATIONS = re.compile(r"grpc\.health\.v1|opentelemetry\.proto")  # health checks, trace export


def short_operation(name: str) -> str:
    """'grpc.hipstershop.CurrencyService/Convert' -> 'CurrencyService/Convert' (client and server alike)."""
    return re.sub(r"^/?(grpc\.)?hipstershop\.", "", name)  # traces call the frontend "frontendservice"
# istio-init is a sidecar init container; frontend-external duplicates frontend's own metrics.
DROP_SERVICES = {"istio-init", "frontend-external"}

# Anything that would name the injected fault. None of these occur in RE2-OB today, but the
# scan runs on every prepare so a new dataset version cannot leak silently.
FAULT_MARKER_RE = re.compile(
    r"stress-?ng|chaos|blade|\binject|netem|\btc qdisc|toxiproxy|pumba|litmus|tcpkill|\bfault\b|re2ob_", re.IGNORECASE
)
MASK = "[masked: fault-injection marker]"

ERROR_RE = re.compile(r"error|exception|traceback|failed|failure|panic|fatal|unavailable|deadline|refused|\braise\b", re.I)
WARN_RE = re.compile(r"warn|retrying|transient|temporarily|timeout|timed out|slow", re.I)

SCHEMA = """
CREATE TABLE cases  (case_id TEXT PRIMARY KEY, inject_ts INTEGER, window_start INTEGER, window_end INTEGER);
CREATE TABLE metrics(case_id TEXT, ts INTEGER, service TEXT, metric TEXT, value REAL);
CREATE TABLE logs   (case_id TEXT, ts INTEGER, service TEXT, level TEXT, message TEXT, count INTEGER);
CREATE TABLE spans  (case_id TEXT, trace_id INTEGER, span_id INTEGER, parent_id INTEGER, service TEXT,
                     operation TEXT, start_ts REAL, duration_ms REAL, status_code INTEGER);
CREATE TABLE alerts (case_id TEXT PRIMARY KEY, service TEXT, signal TEXT, started_at INTEGER, text TEXT);
CREATE INDEX idx_metrics ON metrics(case_id, service, ts);
CREATE INDEX idx_logs    ON logs(case_id, service, ts);
CREATE INDEX idx_spans   ON spans(case_id, service, start_ts);
"""

console = Console()


def infer_level(message: str) -> str:
    """RE2 logs carry no level field, so we infer one from the text."""
    if ERROR_RE.search(message):
        return "ERROR"
    if WARN_RE.search(message):
        return "WARN"
    return "INFO"


def keep_trace(trace_id: str) -> bool:
    return int(hashlib.sha1(trace_id.encode()).hexdigest()[:8], 16) % TRACE_SAMPLE_EVERY == 0


def load_metrics(path, case_id: str, start: int, end: int) -> tuple[pd.DataFrame, int]:
    raw = pd.read_parquet(path)
    raw = raw[(raw.time >= start) & (raw.time < end)]
    frames, masked = [], 0
    for column in raw.columns:
        if column == "time":
            continue
        service, _, suffix = column.rpartition("_")
        if FAULT_MARKER_RE.search(column):
            masked += 1
            continue
        if service in DROP_SERVICES or suffix not in METRIC_NAMES:
            continue
        series = raw[column].astype(float)
        # disk_io is only reported when there is I/O, so missing means 0. Other metrics are
        # scraped every few seconds, so carry the last value forward.
        series = series.fillna(0.0) if suffix == "diskio" else series.ffill()
        frame = pd.DataFrame({"ts": (raw.time // METRIC_STEP_S) * METRIC_STEP_S, "value": series})
        frame = frame.dropna().groupby("ts", as_index=False).value.mean()
        frame["service"], frame["metric"] = service, METRIC_NAMES[suffix]
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True)
    out["case_id"] = case_id
    return out[["case_id", "ts", "service", "metric", "value"]], masked


def load_logs(path, case_id: str, start: int, end: int) -> tuple[pd.DataFrame, int]:
    raw = pd.read_parquet(path)
    raw = raw[(raw.timestamp >= start) & (raw.timestamp < end)]
    messages = raw.message.fillna("").astype(str)
    marked = messages.str.contains(FAULT_MARKER_RE)
    lines = pd.DataFrame(
        {
            "ts": (raw.timestamp.astype(int) // LOG_BUCKET_S) * LOG_BUCKET_S,
            "service": raw.container_name.astype(str).replace(SERVICE_ALIASES),
            "message": messages.where(~marked, MASK),
        }
    )
    lines = lines[~lines.service.isin(DROP_SERVICES)]
    # Same line, same service, same 10-second bucket -> one row with a count.
    out = lines.groupby(["ts", "service", "message"], as_index=False).size().rename(columns={"size": "count"})
    out["level"] = out.message.map(infer_level)
    out["case_id"] = case_id
    return out[["case_id", "ts", "service", "level", "message", "count"]], int(marked.sum())


def load_spans(path, case_id: str, start: int, end: int) -> tuple[pd.DataFrame, int]:
    raw = pd.read_parquet(path)
    start_s = raw.startTime // 1_000_000
    raw = raw[(start_s >= start) & (start_s < end)]
    raw = raw[raw.traceID.map(keep_trace)]
    operations = raw.operationName.fillna("").astype(str)
    raw, operations = raw[~operations.str.contains(NOISE_OPERATIONS)], operations[~operations.str.contains(NOISE_OPERATIONS)]
    marked = operations.str.contains(FAULT_MARKER_RE)

    # Random hex ids -> small integers. Parent ids map through the same table.
    trace_ids = {t: i for i, t in enumerate(raw.traceID.unique(), start=1)}
    span_ids = {s: i for i, s in enumerate(raw.spanID.unique(), start=1)}
    out = pd.DataFrame(
        {
            "case_id": case_id,
            "trace_id": raw.traceID.map(trace_ids),
            "span_id": raw.spanID.map(span_ids),
            # Roots have no parent (NULL). Traces are kept whole, so every other parent is present.
            "parent_id": raw.parentSpanID.map(span_ids).astype("Int64"),
            "service": raw.serviceName.astype(str).replace(SERVICE_ALIASES),
            "operation": operations.map(short_operation).where(~marked, MASK),
            "start_ts": raw.startTime.astype(float) / 1_000_000,
            "duration_ms": raw.duration.astype(float) / 1000,
            "status_code": raw.statusCode.astype("Int64"),
        }
    )
    return out, int(marked.sum())


def prepare() -> None:
    settings = get_settings()
    selection = json.loads((settings.raw_dir / "selection.json").read_text())
    index = pd.read_parquet(settings.raw_dir / "cases.parquet").set_index("case")

    if settings.db_path.exists():
        settings.db_path.unlink()
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(SCHEMA)

    labels, masked_total, counts = {}, {"metrics": 0, "logs": 0, "spans": 0}, {}
    for number, original in enumerate(selection["cases"], start=1):
        case_id = f"case_{number:03d}"
        folder = settings.raw_dir / original
        inject = int((folder / "inject_time.txt").read_text().strip())
        start, end = inject - WINDOW_S, inject + WINDOW_S

        metrics, m_masked = load_metrics(folder / "metrics.parquet", case_id, start, end)
        logs, l_masked = load_logs(folder / "logs.parquet", case_id, start, end)
        spans, s_masked = load_spans(folder / "traces.parquet", case_id, start, end)
        for name, n in (("metrics", m_masked), ("logs", l_masked), ("spans", s_masked)):
            masked_total[name] += n

        conn.execute("INSERT INTO cases VALUES (?, ?, ?, ?)", (case_id, inject, start, end))
        metrics.to_sql("metrics", conn, if_exists="append", index=False)
        logs.to_sql("logs", conn, if_exists="append", index=False)
        spans.to_sql("spans", conn, if_exists="append", index=False)
        conn.commit()
        counts[case_id] = {"metrics": len(metrics), "logs": len(logs), "spans": len(spans)}

        labels[case_id] = {
            "root_cause_service": str(index.loc[original, "root_cause_service"]),
            "fault_type": str(index.loc[original, "fault"]),
            "original_name": original,
        }

    # Alerts are built from the trimmed DB, from the entry service only.
    alerts = {}
    for case_id in labels:
        alert = build_alert(conn, case_id)
        conn.execute(
            "INSERT INTO alerts VALUES (?, ?, ?, ?, ?)",
            (case_id, alert["service"], alert["signal"], alert["started_at"], alert["text"]),
        )
        alerts[case_id] = alert
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    check_alerts_leak_free(alerts, labels)

    case_ids = list(labels)
    splits = {"dev": case_ids[:N_DEV], "test": case_ids[N_DEV:]}
    settings.labels_path.write_text(json.dumps(labels, indent=2))
    settings.splits_path.write_text(json.dumps(splits, indent=2))

    write_data_summary(labels, splits, counts, masked_total, settings.db_path)
    print_summary(case_ids, splits, alerts, counts, masked_total, settings.db_path)


def check_alerts_leak_free(alerts: dict, labels: dict) -> None:
    """Fail loudly if an alert names its own root cause service, fault type or original folder."""
    fault_words = {"cpu", "mem", "memory", "disk", "delay", "loss", "socket"}
    for case_id, alert in alerts.items():
        text = alert["text"].lower()
        label = labels[case_id]
        leaked = [w for w in fault_words if w in text.split()]
        if label["root_cause_service"] in text or label["original_name"] in text or leaked:
            raise ValueError(f"alert for {case_id} leaks its label: {alert['text']}")


def write_data_summary(labels: dict, splits: dict, counts: dict, masked_total: dict, db_path) -> None:
    """Committed summary for the README and docs. Per-split counts only, never per-case labels."""
    summary = {
        "dataset": "RCAEval RE2-OB (Online Boutique)",
        "cases": len(labels),
        "splits": {name: len(ids) for name, ids in splits.items()},
        "fault_types_per_split": {
            name: dict(sorted(Counter(labels[c]["fault_type"] for c in ids).items())) for name, ids in splits.items()
        },
        "root_cause_services_overall": dict(sorted(Counter(v["root_cause_service"] for v in labels.values()).items())),
        "rows": {table: sum(c[table] for c in counts.values()) for table in ("metrics", "logs", "spans")},
        "masked_fault_markers": masked_total,
        "window_seconds_each_side": WINDOW_S,
        "metric_step_seconds": METRIC_STEP_S,
        "log_bucket_seconds": LOG_BUCKET_S,
        "trace_sample_every": TRACE_SAMPLE_EVERY,
        "db_size_mb": round(db_path.stat().st_size / 1e6, 1),
    }
    out = ROOT / "eval" / "results" / "data_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")


def print_summary(case_ids, splits, alerts, counts, masked_total, db_path) -> None:
    table = Table(title="Prepared cases (labels are in data/labels.json, not shown)")
    for column in ("case", "split", "alert", "metrics", "logs", "spans"):
        table.add_column(column)
    for case_id in case_ids:
        split = "dev" if case_id in splits["dev"] else "test"
        c = counts[case_id]
        table.add_row(case_id, split, alerts[case_id]["text"], f"{c['metrics']:,}", f"{c['logs']:,}", f"{c['spans']:,}")
    console.print(table)
    console.print(f"Masked fault-injection markers: {masked_total}")
    console.print(f"Database: {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")
