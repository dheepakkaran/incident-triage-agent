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
import theme  # noqa: E402

THREADS = core.configure_environment()

import streamlit as st  # noqa: E402

# Secrets stay on the server: copy the key into the process environment, never into the page.
try:
    for secret in ("GROQ_API_KEY", "GOOGLE_API_KEY"):
        if secret in st.secrets and not os.getenv(secret):
            os.environ[secret] = st.secrets[secret]
except FileNotFoundError:  # no secrets file when running locally without one
    pass

st.set_page_config(page_title="Incident triage agent", page_icon="🚨", layout="wide")

# The agents' faces (eyes, moods, reactions) live in demo/orbs.py.
ORB_CSS = "<style>" + orbs.ORB_CSS + orbs.BOX_CSS + """
.orb-h {display: flex; align-items: center; font-size: 1.35rem; font-weight: 600; margin: 1.2rem 0 .4rem;}
</style>"""
st.markdown(theme.APP_CSS, unsafe_allow_html=True)
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

LIGHT = getattr(getattr(st.context, "theme", None), "type", "dark") == "light"
ss = st.session_state
ss.setdefault("live_runs", 0)
ss.setdefault("actions", [])
ss.setdefault("run_values", {})
ss.setdefault("pending", None)
ss.setdefault("message", "")
ss.setdefault("transcript", [])
ss.setdefault("live", False)
ss.setdefault("last_says", {})
REPLAY_PACE_S = 0.6  # pause between messages in replay (model calls are paced separately, in core.py)


# ---- running -------------------------------------------------------------


def boxes(typing: str | None = None) -> str:
    """The two bots at the top: what each is telling you right now, with the matching face."""
    values = ss.run_values
    status = chat.bot_status(values, ss.transcript, typing, ss.pending, ss.get("reviewer_on", True))
    # Team reaction: when Skeptic spoke after Scout (and Scout is not working), Scout reacts to it.
    order = {who: i for i, (who, *_rest) in enumerate(ss.transcript)}
    if order.get("skeptic", -1) > order.get("scout", -1) and typing != "scout":
        status["scout"]["mood"], status["skeptic"]["mood"] = orbs.team_moods(status["scout"]["mood"], status["skeptic"]["mood"])
    fresh = {agent for agent in status if ss.last_says.get(agent) != status[agent]["say"]}
    ss.last_says = {agent: status[agent]["say"] for agent in status}
    running = bool(values) and not values.get("report") and not ss.pending
    active = typing or (ss.transcript[-1][0] if running and ss.transcript and ss.transcript[-1][0] in ("scout", "skeptic") else None)
    return orbs.boxes_html(status, active, LIGHT, fresh)


def run_graph(payload, chat_box, boxes_box) -> None:
    """Stream the graph; each finished node adds its messages to the conversation as it happens."""
    try:
        for node, delta, values, pending in core.drive(ss.graph, ss.run_id, payload):
            ss.run_values, ss.pending = values, pending
            messages = chat.messages_for(node, delta, values) if node else []
            if node == "report":
                mode = "agent time, live" if ss.live else "agent time, replayed 4x faster than the recorded run"
                agent_s = sum(step.get("seconds", 0) for step in values.get("steps") or [])  # excludes your thinking time
                messages.append(chat.summary_card(values, agent_s, mode))
            for message in messages:
                ss.transcript.append(message)
                chat_box.markdown(chat.render(ss.transcript, light=LIGHT), unsafe_allow_html=True)
                boxes_box.markdown(boxes(), unsafe_allow_html=True)
                if not ss.live:
                    time.sleep(REPLAY_PACE_S)
            # While the next step runs, the agent that owns it is "typing".
            typing = None if pending else core.next_agent(ss.graph, ss.run_id)
            if typing:  # stays up while that agent's next step runs (the real or replayed model call)
                chat_box.markdown(chat.render(ss.transcript, typing=typing, light=LIGHT), unsafe_allow_html=True)
                boxes_box.markdown(boxes(typing), unsafe_allow_html=True)
    except LLMError as err:
        ss.message = f"The live model failed ({str(err)[:200]}). Free-tier quota may be used up; try Replay."
        return
    approval, action = ss.run_values.get("approval") or {}, ss.run_values.get("action") or {}
    if not ss.pending and approval.get("approved") and action and ss.run_values.get("status") == "diagnosed":
        ss.actions.append(f"{time.strftime('%H:%M:%S')}  SIMULATED {action['action']}({action['target_service']})  [{ss.run_values['case_id']}]")


def start(case_id: str, mode: str, chat_box, boxes_box) -> None:
    settings = get_settings()
    live = mode == "Live"
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
    ss.run_values, ss.pending = {"case_id": case_id}, None
    run_graph(state, chat_box, boxes_box)


def decide(approved: bool, note: str, chat_box, boxes_box) -> None:
    said = "✅ Approve." if approved else "❌ Reject."
    ss.transcript.append(("human", said + (f" {html.escape(note)}" if note else "")))
    ss.pending = None
    run_graph(core.resume_payload(approved, note), chat_box, boxes_box)


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

st.markdown(theme.hero_html(), unsafe_allow_html=True)
st.html(orbs.bot_script(), unsafe_allow_javascript=True)  # our own static script: eyes + click reactions

cases = sorted(REPLAYS)
with st.container(border=True):
    left, mid, right = st.columns([2.2, 1.6, 1], vertical_alignment="bottom")
    case_id = left.selectbox("Test case", cases, help="The 15 held-out test cases (case_006 to case_020).")
    mode = mid.segmented_control(
        "Mode", ["Replay", "Live"], default="Replay", selection_mode="single",
        help="Replay: the recorded eval run, no API calls. Live: Groq, rate limited.",
    ) or "Replay"
    # Clicks are recorded once by callbacks and consumed here. A plain `if st.button(...)` can
    # fire again when a long replay is interrupted by another widget change.
    right.button("Run triage", type="primary", use_container_width=True, icon=":material/play_arrow:",
                 on_click=lambda: ss.update(run_request=True))
    caption = st.empty()
if ss.run_values and ss.run_values.get("case_id") != case_id:  # a new case was picked: the bots start fresh
    ss.run_values, ss.transcript, ss.pending = {}, [], None

st.markdown(theme.label("Agents"), unsafe_allow_html=True)
boxes_box = st.empty()

st.markdown(theme.label("Details"), unsafe_allow_html=True)
triage_tab, hood_tab, results_tab, guard_tab, about_tab = st.tabs(
    [":material/forum: Conversation", ":material/account_tree: Under the hood", ":material/monitoring: Results",
     ":material/shield: Guardrails", ":material/info: About"]
)

with triage_tab:
    chat_box = st.empty()
    if ss.pop("run_request", False):
        start(case_id, mode, chat_box, boxes_box)
    caption.markdown(f'<div class="hint">{html.escape(ss.message or "Replay runs the real agent graph on the model replies recorded in the eval. No API calls.")}</div>', unsafe_allow_html=True)

    values = ss.run_values if ss.run_values.get("case_id") == case_id else {}
    if values and ss.transcript:
        chat_box.markdown(chat.render(ss.transcript, light=LIGHT), unsafe_allow_html=True)
    else:
        alert_text = html.escape(case_alert(get_settings().db_path, case_id)["text"])
        chat_box.markdown(chat.render([("alert", alert_text), ("system", "Press <b>Run</b> and the agents talk it through here.")], light=LIGHT), unsafe_allow_html=True)

    decision = ss.pop("decision", None)
    if decision is not None and values and ss.pending:
        decide(decision["approved"], decision["note"], chat_box, boxes_box)
        st.rerun()

    pending = ss.pending if values else None
    if pending:
        with st.container(border=True):
            st.markdown(
                '<div class="approval-title">Approval required</div>'
                f'<div class="approval-action">{html.escape(pending["action"])}({html.escape(pending["target_service"])})</div>'
                f'<div class="hint">Runbook <code>{html.escape(pending["runbook_id"])}</code> · {html.escape(pending["reason"])}</div>',
                unsafe_allow_html=True,
            )
            note_key = f"note-{ss.run_id}-{len(ss.transcript)}"
            st.text_input("Note for Scout (optional)", key=note_key, placeholder="e.g. check the disk metrics again")
            yes, no, _ = st.columns([1, 1, 3])
            yes.button("Approve", type="primary", use_container_width=True, icon=":material/check:",
                       on_click=lambda: ss.update(decision={"approved": True, "note": ss.get(note_key, "")}))
            no.button("Reject", use_container_width=True, icon=":material/close:",
                      on_click=lambda: ss.update(decision={"approved": False, "note": ss.get(note_key, "")}))

    if values.get("report"):
        with st.expander("Incident report", expanded=not pending, icon=":material/description:"):
            show_report(values)
    with st.expander("Simulated action log", icon=":material/receipt_long:"):
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


# ---- the two bots (drawn last, so they reflect everything above) ----
boxes_box.markdown(boxes(), unsafe_allow_html=True)
