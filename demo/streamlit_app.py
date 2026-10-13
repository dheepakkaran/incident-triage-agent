"""Streamlit demo for the incident triage agent (Streamlit Community Cloud or local).

    streamlit run demo/streamlit_app.py

Replay mode (default) runs the real graph on recorded model replies, with no API calls.
Live mode uses Groq through the server-side secret GROQ_API_KEY.
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `import triage` works when Streamlit runs this file directly
sys.path.insert(0, str(ROOT / "demo"))

import core  # noqa: E402

THREADS = core.configure_environment()

import streamlit as st  # noqa: E402

# Secrets stay on the server: copy the key into the process environment, never into the page.
try:
    if "GROQ_API_KEY" in st.secrets and not os.getenv("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
except FileNotFoundError:  # no secrets file when running locally without one
    pass

st.set_page_config(page_title="Incident triage agent", page_icon="🚨", layout="wide")


@st.cache_resource(show_spinner="Loading data, embedding model and runbook index (first visit only)...")
def startup() -> dict:
    """Runs once per server process: download the DB, warm up the embedding model and Chroma."""
    started = time.perf_counter()
    core.ensure_db()
    from triage.tools import runbooks

    runbooks.search("warm up", 1)
    return {"cold_start_s": round(time.perf_counter() - started, 1), "cap": core.DailyCap(core.LIVE_PER_DAY)}


BOOT = startup()
REPLAYS = core.load_replays()
RESULTS_MD = (ROOT / "eval" / "results" / "RESULTS.md").read_text() if (ROOT / "eval" / "results" / "RESULTS.md").exists() else ""

from triage.config import get_settings  # noqa: E402
from triage.llm.providers import LLMError  # noqa: E402
from triage.tools.db import case_alert  # noqa: E402

ss = st.session_state
ss.setdefault("live_runs", 0)
ss.setdefault("actions", [])
ss.setdefault("run_values", {})
ss.setdefault("pending", None)
ss.setdefault("message", "")


# ---- running -------------------------------------------------------------


def run_graph(payload, label: str) -> None:
    """Stream the graph, showing each node as it finishes, and keep the result in the session."""
    with st.status(label, expanded=True) as status:
        try:
            for node, values, pending in core.drive(ss.graph, ss.run_id, payload):
                ss.run_values, ss.pending = values, pending
                if node:
                    step = (values.get("steps") or [{}])[-1]
                    status.write(f"`{node}` done ({step.get('seconds', 0)}s)")
        except LLMError as err:
            ss.message = f"The live model failed ({str(err)[:200]}). Free-tier quota may be used up; try Replay."
            status.update(label="Run failed", state="error")
            return
        status.update(label="Paused for approval" if ss.pending else "Run finished", state="complete", expanded=False)
    approval, action = ss.run_values.get("approval") or {}, ss.run_values.get("action") or {}
    if not ss.pending and approval.get("approved") and action and ss.run_values.get("status") == "diagnosed":
        ss.actions.append(f"{time.strftime('%H:%M:%S')}  SIMULATED {action['action']}({action['target_service']})  [{ss.run_values['case_id']}]")


def start(case_id: str, mode: str) -> None:
    settings = get_settings()
    live = mode == "Run live"
    if live and not settings.groq_api_key:
        live, ss.message = False, "Live mode is off here (no GROQ_API_KEY secret). Showing the replay instead."
    elif live and ss.live_runs >= core.LIVE_PER_SESSION:
        live, ss.message = False, f"You have used your {core.LIVE_PER_SESSION} live runs this session. Showing the replay instead."
    elif live and not BOOT["cap"].take():
        live, ss.message = False, f"Today's cap of {core.LIVE_PER_DAY} live runs is used up. Showing the replay instead."
    else:
        ss.message = ""
    if live:
        ss.live_runs += 1
    ss.graph, state, description = core.start_run(case_id, "live" if live else "replay", REPLAYS[case_id])
    ss.run_id = state["run_id"]
    ss.message = ss.message or description + (f" ({ss.live_runs}/{core.LIVE_PER_SESSION} live runs used)" if live else "")
    run_graph(state, "Investigating...")


def decide(approved: bool, note: str) -> None:
    ss.message = "You approved the proposal." if approved else "You rejected the proposal; the agent re-investigates once."
    run_graph(core.resume_payload(approved, note), "Resuming...")


# ---- rendering -----------------------------------------------------------


def show_timeline(values: dict) -> None:
    results = values.get("tool_results") or {}
    for step in values.get("steps") or []:
        extra = {k: v for k, v in step.items() if k not in ("node", "seconds", "calls", "tool_calls")}
        detail = ", ".join(f"{k}={v}" for k, v in extra.items())
        st.markdown(f"**`{step['node']}`** · {step.get('seconds', 0)}s {('· ' + detail) if detail else ''}")
        for call in step.get("calls") or []:
            if "id" in call:
                output = results.get(call["id"], {}).get("data", "")
                with st.expander(f"[{call['id']}] {call['tool']}({call['args']})"):
                    st.code(output[:1500] + (" …" if len(output) > 1500 else ""), language=None)
            else:
                why = call.get("rejected") or f"duplicate of {call.get('duplicate_of')}"
                st.warning(f"tool call `{call['tool']}` not run: {why}")
    for e in values.get("guardrail_events") or []:
        st.warning(f"guardrail **{e['layer']}/{e['check']}**: {e['detail']}")


def show_diagnosis(values: dict) -> None:
    d = values.get("diagnosis")
    if not d:
        st.caption("No diagnosis yet.")
        return
    st.table([{"rank": i + 1, "service": c["service"], "fault type": c["fault_type"], "confidence": round(c["confidence"], 2)} for i, c in enumerate(d["candidates"])])
    for e in d["evidence"]:
        st.markdown(f"- **[{e['tool_call_id']}]** `{e['quote']}` — {e['why_it_matters']}")
    st.markdown(d["summary"])
    for r in values.get("reviews") or []:
        st.markdown(f"Reviewer: **{r['verdict']}** — {r['reason']}")


def show_report(values: dict) -> None:
    path = ((values.get("report") or {}).get("paths") or {}).get("dir")
    if path and Path(path, "report.md").exists():
        st.markdown(Path(path, "report.md").read_text())
    else:
        st.caption("The report appears when the run finishes.")


# ---- page ----------------------------------------------------------------

st.title("🚨 Incident triage agent")
st.caption("First-pass root-cause analysis with LangGraph, guardrails, a reviewer agent and a human approval gate. Nothing is ever executed.")
triage_tab, results_tab, guard_tab, about_tab = st.tabs(["Triage", "Results", "Guardrails", "About"])

with triage_tab:
    cases = sorted(REPLAYS)
    left, mid, right = st.columns([2, 2, 1])
    case_id = left.selectbox("Test case", cases)
    mode = mid.radio("Mode", ["Replay", "Run live"], horizontal=True)
    if right.button("Run", type="primary", use_container_width=True):
        start(case_id, mode)
    st.info(f"**Alert:** {case_alert(get_settings().db_path, case_id)['text']}")
    if ss.message:
        st.caption(ss.message)

    values = ss.run_values if ss.run_values.get("case_id") == case_id else {}
    timeline_col, side_col = st.columns([3, 2])
    with timeline_col:
        st.subheader("Timeline")
        if values:
            show_timeline(values)
        else:
            st.caption("Pick a case and press Run.")
    with side_col:
        st.subheader("Diagnosis")
        show_diagnosis(values)
        st.subheader("Approval")
        pending = ss.pending if values else None
        if pending:
            st.markdown(f"**{pending['action']}({pending['target_service']})** · runbook `{pending['runbook_id']}`")
            st.markdown(pending["reason"])
            for e in pending.get("evidence", []):
                st.markdown(f"- [{e['tool_call_id']}] `{e['quote']}`")
            note = st.text_input("Note for the agent (optional)", key=f"note-{ss.run_id}")
            yes, no = st.columns(2)
            if yes.button("Approve", use_container_width=True):
                decide(True, note)
                st.rerun()
            if no.button("Reject", use_container_width=True):
                decide(False, note)
                st.rerun()
        else:
            st.caption("No approval needed right now.")
        st.subheader("Simulated action log")
        st.code("\n".join(ss.actions) or "(nothing executed: approved actions are only logged here)", language=None)
    st.subheader("Final incident report")
    show_report(values)

with results_tab:
    st.markdown(RESULTS_MD.split("## Adversarial")[0] if RESULTS_MD else "No results file.")

with guard_tab:
    st.markdown("## Adversarial" + RESULTS_MD.split("## Adversarial", 1)[1] if "## Adversarial" in RESULTS_MD else "No results file.")

with about_tab:
    st.markdown(
        f"""
### The problem
A production alert fires ("checkout latency is up"). The on-call engineer digs through metrics, logs and traces,
finds the right runbook and works out the root cause. It takes 1–4 hours, it is the same work every time, and
letting an AI restart or roll back services on its own is dangerous.

### What this does
An agent does the first-pass investigation with 5 read-only tools (service graph, metrics, logs, traces, runbooks).
A second reviewer agent tries to prove the diagnosis wrong. The output is a root cause with quoted evidence and a
proposed fix. **Risky actions wait for a human, and nothing is ever executed**: an approved action is only logged.

### Limits
15 test cases, one system (Online Boutique), one benchmark. Labels come from fault injection, not real incidents.
Replays come from a small local open model (qwen3:8b). Live mode uses a free-tier hosted model and is rate limited:
{core.LIVE_PER_SESSION} runs per session, {core.LIVE_PER_DAY} per day ({BOOT['cap'].used()} used today).

### Data credit
RCAEval (Pham et al., WWW 2025): [paper](https://arxiv.org/abs/2412.17015) ·
[code](https://github.com/phamquiluan/RCAEval) · [data](https://doi.org/10.5281/zenodo.14590730) (CC-BY-4.0; the
Hugging Face copy is MIT). This demo uses a trimmed SQLite copy of the 15 test cases.

Cold start of this server (data download, embedding model, Chroma index): {BOOT['cold_start_s']}s · CPU threads: {THREADS}.
"""
    )
