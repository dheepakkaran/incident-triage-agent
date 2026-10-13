"""Gradio demo for the incident triage agent (Hugging Face Space).

Two modes:
  - Replay (default): runs the REAL LangGraph graph, but the LLM is a scripted model that
    returns the replies recorded during the eval run. No API calls. The approval gate
    still pauses with interrupt() and resumes with Command(resume=...).
  - Run live: the same graph with Groq (key from the Space secret GROQ_API_KEY, never
    sent to the browser). 3 live runs per session and a global daily cap.
"""

import os


def cpu_quota() -> int:
    """CPUs this container may really use (cgroup quota), not the host's core count."""
    try:
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
        if quota != "max":
            return max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return 2


THREADS = str(cpu_quota())
for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(var, THREADS)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TELEMETRY_DB", os.path.join(HERE, "data", "telemetry.db"))
os.environ.setdefault("RUNS_DIR", "/tmp/triage-runs")
os.environ.setdefault("CACHE_DIR", "/tmp/triage-cache")
os.environ.setdefault("LLM_CACHE_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER_CHAIN", "groq")

import html  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402

import gradio as gr  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from triage.agent.graph import build_graph, initial_state, thread_config  # noqa: E402
from triage.agent.nodes import Deps  # noqa: E402
from triage.config import get_settings  # noqa: E402
from triage.llm.providers import LLMClient, LLMError, ModelHandle  # noqa: E402
from triage.tools import runbooks as runbook_search  # noqa: E402
from triage.tools.db import case_alert  # noqa: E402

STARTED = time.perf_counter()
REPLAYS = {p.stem: json.loads(p.read_text()) for p in sorted(Path(HERE, "replays").glob("case_*.json"))}
RESULTS_MD = Path(HERE, "results", "RESULTS.md").read_text() if Path(HERE, "results", "RESULTS.md").exists() else ""
LIVE_PER_SESSION = 3
LIVE_PER_DAY = int(os.getenv("LIVE_RUNS_PER_DAY", "50"))
USAGE_FILE = Path("/tmp/triage-live-usage.json")
_usage_lock = threading.Lock()
SESSIONS: dict[str, dict] = {}  # session hash -> {graph, thread_id, mode, live_runs, actions}

# Load the embedding model and the Chroma index once, at startup.
runbook_search.search("warm up", 1)
COLD_START_S = round(time.perf_counter() - STARTED, 1)


# ---- replay model ----------------------------------------------------------


def _kind(messages) -> str:
    system = str(messages[0].content) if messages else ""
    for prefix, kind in (
        ("You are an on-call SRE", "investigate"),
        ("You turn an incident", "diagnose"),
        ("You review another", "review"),
        ("Pick one remediation", "action"),
    ):
        if system.startswith(prefix):
            return kind
    return "other"


class ReplayChatModel:
    """Returns the replies recorded in the eval run, in order.

    After the recording runs out (only happens if you reject a proposal, which the eval
    never did), it repeats the last recorded reply of the same kind, and the investigator
    simply says it re-checked. The point is to exercise the real interrupt/resume path.
    """

    def __init__(self, llm_calls: list[dict]):
        self.replies = [self._message(c["reply"]) for c in llm_calls]
        self.last_by_kind: dict[str, AIMessage] = {}

    @staticmethod
    def _message(reply: dict) -> AIMessage:
        calls = [{"name": c["name"], "args": c["args"], "id": c["id"], "type": "tool_call"} for c in reply["tool_calls"]]
        return AIMessage(content=reply["content"], tool_calls=calls)

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs) -> AIMessage:
        kind = _kind(messages)
        if self.replies:
            reply = self.replies.pop(0)
        elif kind == "investigate":
            reply = AIMessage(content="Re-checked the evidence after the engineer's feedback; it still points the same way.")
        else:
            reply = self.last_by_kind.get(kind, AIMessage(content="{}"))
        if not reply.tool_calls:
            self.last_by_kind[kind] = reply
        reply.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return AIMessage(content=reply.content, tool_calls=reply.tool_calls, usage_metadata=reply.usage_metadata)


# ---- live limits -----------------------------------------------------------


def _take_daily_slot() -> bool:
    with _usage_lock:
        today = date.today().isoformat()
        usage = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else {}
        if usage.get("date") != today:
            usage = {"date": today, "count": 0}
        if usage["count"] >= LIVE_PER_DAY:
            return False
        usage["count"] += 1
        USAGE_FILE.write_text(json.dumps(usage))
        return True


# ---- rendering -------------------------------------------------------------

CSS = """
.step {border-left: 3px solid #8888; padding: 4px 10px; margin: 6px 0;}
.step b {font-family: ui-monospace, monospace;}
.tool {background: #8881; border-radius: 6px; padding: 6px 8px; margin: 4px 0; font-family: ui-monospace, monospace;
       font-size: 12px; white-space: pre-wrap;}
.guard {border-left: 3px solid #e8a33c; background: #e8a33c22; padding: 4px 10px; margin: 4px 0; font-size: 13px;}
"""


def render_timeline(state: dict) -> str:
    results = state.get("tool_results") or {}
    events = state.get("guardrail_events") or []
    parts = []
    for step in state.get("steps") or []:
        detail = {k: v for k, v in step.items() if k not in ("node", "seconds", "calls", "tool_calls")}
        line = f"<div class='step'><b>{html.escape(step['node'])}</b> <small>{step.get('seconds', 0)}s</small> "
        line += html.escape(", ".join(f"{k}={v}" for k, v in detail.items()))
        for call in step.get("calls") or []:
            if "id" in call:
                output = results.get(call["id"], {}).get("data", "")
                shown = output[:600] + (" …" if len(output) > 600 else "")
                line += f"<div class='tool'>[{call['id']}] {html.escape(call['tool'])}({html.escape(json.dumps(call['args']))})\n{html.escape(shown)}</div>"
            else:
                why = call.get("rejected") or f"duplicate of {call.get('duplicate_of')}"
                line += f"<div class='guard'>tool call {html.escape(call['tool'])} not run: {html.escape(str(why))}</div>"
        parts.append(line + "</div>")
    for e in events:
        parts.append(f"<div class='guard'>guardrail <b>{e['layer']}/{e['check']}</b>: {html.escape(e['detail'])}</div>")
    return "".join(parts) or "<i>Pick a case and press Run.</i>"


def render_diagnosis(state: dict) -> str:
    d = state.get("diagnosis")
    if not d:
        return "_No diagnosis yet._"
    rows = "\n".join(f"| {i + 1} | {c['service']} | {c['fault_type']} | {c['confidence']:.2f} |" for i, c in enumerate(d["candidates"]))
    evidence = "\n".join(f"- **[{e['tool_call_id']}]** `{e['quote']}` — {e['why_it_matters']}" for e in d["evidence"])
    reviews = "\n".join(f"- reviewer: **{r['verdict']}** — {r['reason']}" for r in state.get("reviews") or [])
    return f"| rank | service | fault type | confidence |\n|---|---|---|---|\n{rows}\n\n**Evidence**\n{evidence}\n\n{d['summary']}\n\n{reviews}"


def render_report(state: dict) -> str:
    report = state.get("report") or {}
    path = (report.get("paths") or {}).get("dir")
    if path and Path(path, "report.md").exists():
        return Path(path, "report.md").read_text()
    return "_The report appears when the run finishes._"


# ---- event handlers --------------------------------------------------------


def _session(request: gr.Request) -> dict:
    key = getattr(request, "session_hash", None) or "local"
    return SESSIONS.setdefault(key, {"live_runs": 0, "actions": []})


def show_alert(case_id: str) -> str:
    return f"**Alert:** {case_alert(get_settings().db_path, case_id)['text']}"


def _outputs(state: dict, session: dict, note: str, pending: dict | None):
    approval_md = "_No approval needed yet._"
    if pending:
        evidence = "\n".join(f"- [{e['tool_call_id']}] `{e['quote']}`" for e in pending.get("evidence", []))
        approval_md = (
            f"### Approval needed\n**{pending['action']}({pending['target_service']})** — runbook `{pending['runbook_id']}`\n\n"
            f"{pending['reason']}\n\n{evidence}"
        )
    actions = "\n".join(session["actions"]) or "(nothing executed — approved actions are only logged here)"
    buttons = gr.update(interactive=bool(pending))
    return render_timeline(state), render_diagnosis(state), approval_md, buttons, buttons, actions, render_report(state), note


def _drive(session: dict, payload):
    """Stream the graph until it ends or pauses. Yields (state, pending_interrupt)."""
    graph, config = session["graph"], thread_config(session["thread_id"])
    pending = None
    for update in graph.stream(payload, config, stream_mode="updates"):
        if "__interrupt__" in update:
            pending = update["__interrupt__"][0].value
        yield graph.get_state(config).values, pending


def _record_actions(session: dict, state: dict) -> None:
    approval, action = state.get("approval") or {}, state.get("action") or {}
    if approval.get("approved") and action:
        session["actions"].append(f"{time.strftime('%H:%M:%S')} SIMULATED {action['action']}({action['target_service']}) for {state['case_id']}")


def run_case(case_id: str, mode: str, request: gr.Request):
    session = _session(request)
    settings = get_settings()
    note = ""
    if mode == "Run live":
        if not settings.groq_api_key:
            note, mode = "Live mode is off on this Space (no GROQ_API_KEY secret). Showing the replay instead.", "Replay"
        elif session["live_runs"] >= LIVE_PER_SESSION:
            note, mode = f"You have used your {LIVE_PER_SESSION} live runs for this session. Showing the replay instead.", "Replay"
        elif not _take_daily_slot():
            note, mode = f"The daily cap of {LIVE_PER_DAY} live runs is used up. Showing the replay instead.", "Replay"

    replay = REPLAYS[case_id]
    if mode == "Run live":
        session["live_runs"] += 1
        llm = LLMClient.from_settings(settings, only="groq")
        reviewer = settings.reviewer_enabled
        note = note or f"Live run {session['live_runs']}/{LIVE_PER_SESSION} on groq:{settings.groq_model}."
    else:
        model = replay["config"]["provider_chain"][0]
        llm = LLMClient([ModelHandle("replay", model, ReplayChatModel(replay["llm_calls"]))], cache=None)
        reviewer = replay["config"]["reviewer_enabled"]
        note = note or f"Replay of the recorded eval run ({model}, prompt {replay['config']['prompt_version']}). No API calls."

    deps = Deps(llm=llm, settings=settings, reviewer_enabled=reviewer)
    session["graph"] = build_graph(deps, InMemorySaver())
    state = initial_state(case_id, deps, auto_approve=False)
    session["thread_id"] = state["run_id"] + "-" + uuid.uuid4().hex[:4]
    try:
        for current, pending in _drive(session, state):
            yield _outputs(current, session, note, pending)
    except LLMError as err:
        yield _outputs({}, session, f"Live run failed ({err}). Free-tier quota may be used up; try Replay.", None)


def decide(approved: bool, comment: str, request: gr.Request):
    session = _session(request)
    if "graph" not in session:
        return
    try:
        for current, pending in _drive(session, Command(resume={"approved": approved, "note": comment or ""})):
            if not pending and (current.get("approval") or {}).get("approved") and current.get("status") == "diagnosed":
                if not session.get("logged_" + session["thread_id"]):
                    _record_actions(session, current)
                    session["logged_" + session["thread_id"]] = True
            verdict = "approved" if approved else "rejected"
            yield _outputs(current, session, f"You {verdict} the proposal.", pending)
    except LLMError as err:
        yield _outputs({}, session, f"Live run failed while resuming ({err}).", None)


def approve(comment: str, request: gr.Request):
    yield from decide(True, comment, request)


def reject(comment: str, request: gr.Request):
    yield from decide(False, comment, request)


# ---- layout ----------------------------------------------------------------

ABOUT = f"""
## The problem
A production alert fires ("checkout latency is up"). The on-call engineer then digs through metrics, logs and
traces, finds the right runbook and works out the root cause. That takes 1–4 hours, it is the same work every
time, and letting an AI restart or roll back services on its own is dangerous.

## What this does
An agent does the first-pass investigation. It calls 5 read-only tools (metrics, logs, traces, service graph,
runbooks), a second "reviewer" agent tries to prove the diagnosis wrong, and the result is a root cause with
quoted evidence plus a proposed fix. **Risky actions wait for a human, and nothing is ever executed**: an approved
action is only written to a log.

## Limits
15 test cases, one system (Online Boutique), one benchmark. Labels come from fault injection, not real incidents.
The replayed runs come from a small local open model (see the Results tab). Live mode uses a free-tier hosted model.

## Data credit
RCAEval (Pham et al., WWW 2025): [paper](https://arxiv.org/abs/2412.17015),
[code](https://github.com/phamquiluan/RCAEval), [data](https://doi.org/10.5281/zenodo.14590730) (CC-BY-4.0;
Hugging Face copy MIT). This Space ships a trimmed SQLite copy of the 15 test cases.

Cold start of this container (embedding model + Chroma index): {COLD_START_S}s. CPU threads: {THREADS}.
"""

with gr.Blocks(title="Incident triage agent") as demo:
    gr.Markdown("# Incident triage agent\nFirst-pass root-cause analysis with LangGraph, guardrails and a human approval gate.")
    with gr.Tab("Triage"):
        with gr.Row():
            case = gr.Dropdown(sorted(REPLAYS), value=sorted(REPLAYS)[0], label="Test case")
            mode = gr.Radio(["Replay", "Run live"], value="Replay", label="Mode")
            run_btn = gr.Button("Run", variant="primary")
        alert = gr.Markdown(show_alert(sorted(REPLAYS)[0]))
        status = gr.Markdown()
        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### Timeline")
                timeline = gr.HTML()
            with gr.Column(scale=2):
                gr.Markdown("### Diagnosis")
                diagnosis = gr.Markdown()
                approval = gr.Markdown("_No approval needed yet._")
                comment = gr.Textbox(label="Note for the agent (optional)", lines=2)
                with gr.Row():
                    approve_btn = gr.Button("Approve", interactive=False)
                    reject_btn = gr.Button("Reject", variant="stop", interactive=False)
                gr.Markdown("### Simulated action log")
                actions = gr.Code(language=None, interactive=False)
        gr.Markdown("### Final incident report")
        report = gr.Markdown()
    with gr.Tab("Results"):
        gr.Markdown(RESULTS_MD.split("## Adversarial")[0] if RESULTS_MD else "No results file.")
    with gr.Tab("Guardrails"):
        gr.Markdown("## Adversarial" + RESULTS_MD.split("## Adversarial", 1)[1] if "## Adversarial" in RESULTS_MD else "No results file.")
    with gr.Tab("About"):
        gr.Markdown(ABOUT)

    outputs = [timeline, diagnosis, approval, approve_btn, reject_btn, actions, report, status]
    case.change(show_alert, case, alert)
    run_btn.click(run_case, [case, mode], outputs)
    approve_btn.click(approve, [comment], outputs)
    reject_btn.click(reject, [comment], outputs)

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=4).launch(css=CSS)
