# Data notes

All numbers below come from `eval/results/data_summary.json`, which `python -m triage data prepare` writes.

## Source

- RCAEval benchmark (Pham et al., WWW 2025, arXiv 2412.17015), RE2 suite, **Online Boutique** system (RE2-OB).
- Downloaded from the official Hugging Face copy (`phamquiluan/RCAEval`, Parquet, MIT licence). The RCAEval README recommends it because single cases can be fetched. The originals are on Zenodo (record 14590730).
- RE2-OB has 90 cases: 6 fault types (cpu, mem, disk, delay, loss, socket) × 5 root-cause services × 3 repetitions.

## Case selection (`triage/data/download.py`)

- Only cases with at least 10 minutes of data before and after the injection are eligible.
- 20 cases, seed 42, spread evenly over fault types (3–4 each). Within a fault type the services with the fewest picks go first, so each of the 5 services ends up with 4 cases.
- Cases are renamed `case_001 … case_020`. The first 5 (dev) cover 5 different fault types. The other 15 are test.
- There is no `socket` case in dev, because 5 dev slots cannot hold 6 fault types. Prompts are therefore never tuned on a socket fault.

## Leakage controls

| Risk | What we did |
|---|---|
| Folder names contain the answer (`re2ob_emailservice_cpu_3`) | Renamed to `case_NNN`. The originals live only in `data/labels.json` and `data/raw/selection.json`. |
| Agent code reads labels | `tests/test_leakage.py` fails if any file under `triage/agent` or `triage/tools` mentions labels or imports eval/data-download code. A runtime audit hook also fails if an agent run opens a labels file. |
| Telemetry names the injected fault | Every metric name, log line and span operation is scanned for `stress-ng, chaos, blade, inject, netem, tc qdisc, toxiproxy, pumba, litmus, tcpkill, fault, re2ob_`. Matches would be masked as `[masked: fault-injection marker]`. **RE2-OB has 0 matches** (`masked_fault_markers`). The scan stays in place so a new data version cannot leak silently. |
| Alert gives the answer away | Alerts use the frontend only (route latency or error rate). `prepare` raises an error if an alert contains its root-cause service, fault-type word or original folder name. |

## What was removed or changed

- **Window:** 10 minutes before and 10 minutes after the injection time (`window_seconds_each_side`).
- **Metrics:** averaged into 10-second buckets (`metric_step_seconds`) and renamed to readable names: `cpu→cpu_usage` (percent of one core), `mem→memory_bytes`, `diskio→disk_io_bytes`, `socket→open_sockets`, `workload→request_rate` (req/s), `error→error_rate` (errors/s), `latency-50/90→latency_p50_s/latency_p90_s` (seconds).
  - `disk_io_bytes` is only reported while there is I/O, so a missing value means 0. Other metrics are forward-filled.
  - Dropped: `istio-init_*` (an init container, present in one case) and `frontend-external_*` (a copy of frontend's own metrics).
- **Logs:** RE2 logs have no level field. The level is inferred from the text: ERROR for error, exception, failed, unavailable, deadline, refused, raise and similar words; WARN for warn, retrying, transient, timeout; INFO otherwise. Identical lines from the same service in the same 10-second bucket become one row with a `count` column (`log_bucket_seconds`), so counts stay exact. This cut ~2.7M lines to the row count in `rows.logs`.
- **Traces:** 1 in 5 traces is kept, whole, chosen by a hash of the trace id (`trace_sample_every`). gRPC health checks and OpenTelemetry export spans are dropped. Random hex trace and span ids become small integers. `frontendservice` is renamed `frontend` to match the metric and log names. Operation names are shortened (`grpc.hipstershop.CurrencyService/Convert` → `CurrencyService/Convert`).

## Tables (`data/telemetry.db`)

```
cases  (case_id, inject_ts, window_start, window_end)
metrics(case_id, ts, service, metric, value)
logs   (case_id, ts, service, level, message, count)
spans  (case_id, trace_id, span_id, parent_id, service, operation, start_ts, duration_ms, status_code)
alerts (case_id, service, signal, started_at, text)
```

Indexes cover `(case_id, service, ts)` on metrics and logs, and `(case_id, service, start_ts)` on spans.

## Known data quirks

- Some cases show almost no frontend symptom (for example, an emailservice fault only touches the checkout route, which is ~3% of traffic). Their alerts show small ratios such as "up 1.1x". We keep them: the benchmark says a fault happened, and a weak symptom is realistic.
- Trace errors appear only as gRPC status codes 2, 4, 13 and 14, and mostly in packet-loss cases.
- Some spans in `checkoutservice` have no parent (their caller's span was not exported). They count as roots.
