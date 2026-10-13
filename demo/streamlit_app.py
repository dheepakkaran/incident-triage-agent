"""Streamlit demo for the incident triage agent (Streamlit Community Cloud or local).

    streamlit run demo/streamlit_app.py

Replay mode (default) runs the real graph on recorded model replies, with no API calls.
Live mode uses Groq through the server-side secret GROQ_API_KEY.
"""

import html
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `import triage` works when Streamlit runs this file directly
sys.path.insert(0, str(ROOT / "demo"))

import chat  # noqa: E402
import core  # noqa: E402
import orbs  # noqa: E402

THREADS = core.configure_environment()

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

# Secrets stay on the server: copy the key into the process environment, never into the page.
try:
    if "GROQ_API_KEY" in st.secrets and not os.getenv("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
except FileNotFoundError:  # no secrets file when running locally without one
    pass

st.set_page_config(page_title="Incident triage agent", page_icon="🚨", layout="wide")

# The agents' faces (eyes, moods, reactions) live in demo/orbs.py.
ORB_CSS = "<style>" + orbs.ORB_CSS + """
.orb-h {display: flex; align-items: center; font-size: 1.35rem; font-weight: 600; margin: 1.2rem 0 .4rem;}
</style>"""
st.markdown(ORB_CSS, unsafe_allow_html=True)
st.markdown(chat.CHAT_CSS, unsafe_allow_html=True)


def orb(agent: str, large: bool = False, mood: str = "") -> str:
    return orbs.orb_html(agent, mood, "lg" if large else "")


def orb_heading(agent: str, text: str) -> None:
    st.markdown(f'<div class="orb-h">{orb(agent, large=True)}{text}</div>', unsafe_allow_html=True)


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
ss.setdefault("transcript", [])
ss.setdefault("live", False)
REPLAY_PACE_S = 0.35  # replay is instant; space the messages out so the conversation is readable


# ---- running -------------------------------------------------------------


def run_graph(payload, chat_box) -> None:
    """Stream the graph; each finished node adds its messages to the conversation as it happens."""
    try:
        for node, delta, values, pending in core.drive(ss.graph, ss.run_id, payload):
            ss.run_values, ss.pending = values, pending
            for message in chat.messages_for(node, delta, values) if node else []:
                ss.transcript.append(message)
                chat_box.markdown(chat.render(ss.transcript), unsafe_allow_html=True)
                if not ss.live:
                    time.sleep(REPLAY_PACE_S)
    except LLMError as err:
        ss.message = f"The live model failed ({str(err)[:200]}). Free-tier quota may be used up; try Replay."
        return
    approval, action = ss.run_values.get("approval") or {}, ss.run_values.get("action") or {}
    if not ss.pending and approval.get("approved") and action and ss.run_values.get("status") == "diagnosed":
        ss.actions.append(f"{time.strftime('%H:%M:%S')}  SIMULATED {action['action']}({action['target_service']})  [{ss.run_values['case_id']}]")


def start(case_id: str, mode: str, chat_box) -> None:
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
    ss.live = live
    ss.graph, state, description = core.start_run(case_id, "live" if live else "replay", REPLAYS[case_id])
    ss.reviewer_on = REPLAYS[case_id]["config"]["reviewer_enabled"] if not live else settings.reviewer_enabled
    ss.run_id = state["run_id"]
    ss.message = ss.message or description + (f" ({ss.live_runs}/{core.LIVE_PER_SESSION} live runs used)" if live else "")
    ss.transcript = [("alert", html.escape(state["alert"]["text"]))]
    run_graph(state, chat_box)


def decide(approved: bool, note: str, chat_box) -> None:
    said = "✅ Approve." if approved else "❌ Reject."
    ss.transcript.append(("human", said + (f" {html.escape(note)}" if note else "")))
    run_graph(core.resume_payload(approved, note), chat_box)


# ---- rendering -----------------------------------------------------------


ACTOR = {
    "investigate": "scout",
    "run_tools": "scout",
    "diagnose": "scout",
    "review": "skeptic",
    "review_tools": "skeptic",
}
OTHER = {"validate_alert": "🛡️", "propose_action": "🛠️", "approval_gate": "🙋", "escalate": "📣", "report": "📄"}
NAMES = {"scout": "Scout", "skeptic": "Skeptic"}


def show_timeline(values: dict) -> None:
    results = values.get("tool_results") or {}
    for step in values.get("steps") or []:
        extra = {k: v for k, v in step.items() if k not in ("node", "seconds", "calls", "tool_calls")}
        detail = ", ".join(f"{k}={v}" for k, v in extra.items())
        agent = ACTOR.get(step["node"])
        who = f"{orb(agent)}<b>{NAMES[agent]}</b>" if agent else OTHER.get(step["node"], "")
        st.markdown(
            f"{who} <code>{step['node']}</code> · {step.get('seconds', 0)}s {('· ' + html.escape(detail)) if detail else ''}",
            unsafe_allow_html=True,
        )
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


def show_reviewer(values: dict) -> None:
    """The second agent: its verdict each round, its evidence, its own tool calls, and what happened next."""
    if not values:
        st.caption("Runs after the diagnosis.")
        return
    if not ss.get("reviewer_on", True):
        st.caption("The reviewer was switched off for this run (REVIEWER_ENABLED=false).")
        return
    reviews = values.get("reviews") or []
    if not reviews:
        reason = values.get("escalation_reason")
        st.caption(f"The reviewer did not run: the case escalated before review ({reason})." if reason else "Waiting for the diagnosis...")
        return
    results = values.get("tool_results") or {}
    rounds = [s for s in values.get("steps") or [] if s["node"] in ("review", "review_tools")]
    review_calls = [c for s in rounds if s["node"] == "review_tools" for c in s.get("calls") or [] if "id" in c]
    for i, r in enumerate(reviews, start=1):
        badge = "✅ AGREE" if r["verdict"] == "agree" else "↩️ REVISE"
        suspect = f" · suspects **{r['suspected_service']}**" if r["verdict"] == "revise" and r.get("suspected_service") else ""
        st.markdown(f"**Round {i}: {badge}**{suspect}")
        st.markdown(f"> {r['reason']}")
        for e in r.get("evidence") or []:
            st.markdown(f"- [{e['tool_call_id']}] `{e['quote']}`")
    for c in review_calls:
        with st.expander(f"reviewer checked [{c['id']}] {c['tool']}({c['args']})"):
            st.code(results.get(c["id"], {}).get("data", "")[:1500], language=None)
    last = reviews[-1]["verdict"]
    if last == "agree":
        st.caption("Outcome: Skeptic agreed, so Scout's diagnosis went on to the action step.")
    elif len(reviews) == 1:
        st.caption("Outcome: sent back to Scout once, with Skeptic's reason.")
    else:
        st.caption("Outcome: Skeptic still said 'revise' after one revision round, so the case was escalated with both views.")


def show_report(values: dict) -> None:
    path = ((values.get("report") or {}).get("paths") or {}).get("dir")
    if path and Path(path, "report.md").exists():
        st.markdown(Path(path, "report.md").read_text())
    else:
        st.caption("The report appears when the run finishes.")


# ---- page ----------------------------------------------------------------

st.title("🚨 Incident triage agent")
st.caption("First-pass root-cause analysis with LangGraph, guardrails, a reviewer agent and a human approval gate. Nothing is ever executed.")
header_box = st.empty()  # filled at the end of the script, so the faces match the latest messages
triage_tab, hood_tab, results_tab, guard_tab, about_tab = st.tabs(
    ["💬 Triage", "🔧 Under the hood", "Results", "Guardrails", "About"]
)

with triage_tab:
    cases = sorted(REPLAYS)
    left, mid, right = st.columns([2, 2, 1])
    case_id = left.selectbox("Test case", cases)
    mode = mid.radio("Mode", ["Replay", "Run live"], horizontal=True)
    # Clicks are recorded once by callbacks and consumed here. A plain `if st.button(...)` can
    # fire again when a long replay is interrupted by another widget change.
    right.button("Run", type="primary", use_container_width=True, on_click=lambda: ss.update(run_request=True))
    caption = st.empty()
    chat_box = st.empty()
    if ss.pop("run_request", False):
        start(case_id, mode, chat_box)
    caption.caption(ss.message or "Replay runs the real agent graph on the model replies recorded in the eval. No API calls.")

    values = ss.run_values if ss.run_values.get("case_id") == case_id else {}
    if values and ss.transcript:
        chat_box.markdown(chat.render(ss.transcript), unsafe_allow_html=True)
    else:
        alert_text = html.escape(case_alert(get_settings().db_path, case_id)["text"])
        chat_box.markdown(chat.render([("alert", alert_text), ("system", "Press <b>Run</b> and the agents talk it through here.")]), unsafe_allow_html=True)

    decision = ss.pop("decision", None)
    if decision is not None and values and ss.pending:
        decide(decision["approved"], decision["note"], chat_box)
        st.rerun()

    pending = ss.pending if values else None
    if pending:
        st.markdown(f"**🙋 Your call:** approve **{pending['action']}({pending['target_service']})**?")
        note_key = f"note-{ss.run_id}-{len(ss.transcript)}"
        st.text_input("Note for Scout (optional)", key=note_key)
        yes, no, _ = st.columns([1, 1, 3])
        yes.button("✅ Approve", use_container_width=True, on_click=lambda: ss.update(decision={"approved": True, "note": ss.get(note_key, "")}))
        no.button("❌ Reject", use_container_width=True, on_click=lambda: ss.update(decision={"approved": False, "note": ss.get(note_key, "")}))

    if values.get("report"):
        with st.expander("📄 Incident report", expanded=not pending):
            show_report(values)
    with st.expander("Simulated action log"):
        st.code("\n".join(ss.actions) or "(nothing executed: approved actions are only logged here)", language=None)

with hood_tab:
    hood_values = ss.run_values if ss.run_values.get("case_id") == case_id else {}
    if not hood_values:
        st.caption("Run a case on the Triage tab; every step, tool output and guardrail decision shows up here.")
    else:
        timeline_col, side_col = st.columns([3, 2])
        with timeline_col:
            st.subheader("Timeline")
            show_timeline(hood_values)
        with side_col:
            orb_heading("scout", "Scout's diagnosis")
            show_diagnosis(hood_values)
            orb_heading("skeptic", "Skeptic's review")
            show_reviewer(hood_values)

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


# ---- interactive header (drawn last so the faces match this run's latest reactions) ----
moods = chat.last_moods(ss.transcript) if ss.run_values else {"scout": "", "skeptic": ""}
dark = getattr(getattr(st.context, "theme", None), "type", "dark") != "light"
with header_box:
    components.html(orbs.header_html(moods["scout"], moods["skeptic"], dark), height=160)
