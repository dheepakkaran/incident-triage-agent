"""Demo logic shared by the Streamlit app, kept free of UI code.

Two modes:
  - Replay: the REAL LangGraph graph runs, but the LLM is a scripted model that returns the
    replies recorded in the eval run. No API calls. The approval gate still pauses with
    interrupt() and resumes with Command(resume=...).
  - Live: the same graph with Groq. The key comes from the server-side secret GROQ_API_KEY
    and is never sent to the browser. Limits: a few live runs per session, a daily cap.
"""

import json
import os
import threading
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "telemetry.db"
DB_REPO = os.getenv("DEMO_DB_REPO", "dheepakkaran/incident-triage-telemetry")  # a public HF *dataset*
LIVE_PER_SESSION = int(os.getenv("LIVE_RUNS_PER_SESSION", "3"))
LIVE_PER_DAY = int(os.getenv("LIVE_RUNS_PER_DAY", "50"))
# Replay plays each model call back at 4x its recorded speed (0.5 s to 2.5 s), so the
# "typing" bubble lasts about as long, relatively, as the model really thought.
REPLAY_SPEEDUP, REPLAY_MIN_S, REPLAY_MAX_S = 4.0, 0.5, 2.5


def cpu_quota() -> int:
    """CPUs this container may really use (cgroup quota), not the host's core count."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            return max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # macOS
        return min(os.cpu_count() or 2, 4)


def configure_environment() -> int:
    """Call before importing torch / numpy. Returns the thread count used."""
    threads = str(cpu_quota())
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, threads)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TELEMETRY_DB", str(DB_PATH))
    os.environ.setdefault("RUNS_DIR", "/tmp/triage-demo/runs")
    os.environ.setdefault("CACHE_DIR", "/tmp/triage-demo/cache")
    os.environ.setdefault("RUNBOOKS_DIR", str(HERE.parent / "runbooks"))
    os.environ.setdefault("LLM_CACHE_ENABLED", "false")
    os.environ.setdefault("INVESTIGATOR_PROVIDER_CHAIN", "groq")  # Scout
    os.environ.setdefault("REVIEWER_PROVIDER_CHAIN", "google,groq")  # Skeptic: Gemma, Groq if Google is down
    # The project default is reviewer OFF (dev ablation). The demo exists to show the two agents
    # working together, so it turns the reviewer on; replays come from the reviewer-on test run.
    os.environ.setdefault("REVIEWER_ENABLED", "true")
    return int(threads)


def ensure_db() -> Path:
    """The trimmed test-split DB is too big for GitHub, so it lives in a public HF dataset."""
    if not DB_PATH.exists():
        from huggingface_hub import hf_hub_download

        hf_hub_download(DB_REPO, "telemetry.db", repo_type="dataset", local_dir=DB_PATH.parent)
    return DB_PATH


def load_replays() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((HERE / "replays").glob("case_*.json"))}


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

    The eval auto-approved, so a recording never covers a rejection. After the recording
    runs out, the investigator says it re-checked and every other step repeats its last
    recorded reply. That is enough to exercise the real reject -> re-investigate path.
    """

    def __init__(self, llm_calls: list[dict], pace: bool = False):
        from langchain_core.messages import AIMessage

        self._AIMessage = AIMessage
        self.replies = [self._message(c["reply"]) for c in llm_calls]
        self.latencies = [c.get("latency_s", 0.0) for c in llm_calls]
        self.pace = pace
        self.last_by_kind: dict = {}

    def _message(self, reply: dict):
        calls = [{"name": c["name"], "args": c["args"], "id": c["id"], "type": "tool_call"} for c in reply["tool_calls"]]
        return self._AIMessage(content=reply["content"], tool_calls=calls)

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        kind = _kind(messages)
        if self.replies:
            reply = self.replies.pop(0)
            if self.pace:
                import time

                recorded = self.latencies.pop(0)
                time.sleep(min(max(recorded / REPLAY_SPEEDUP, REPLAY_MIN_S), REPLAY_MAX_S))
        elif kind == "investigate":
            reply = self._AIMessage(content="Re-checked the evidence after the engineer's feedback; it still points the same way.")
        else:
            reply = self.last_by_kind.get(kind, self._AIMessage(content="{}"))
        if not reply.tool_calls:
            self.last_by_kind[kind] = reply
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return self._AIMessage(content=reply.content, tool_calls=reply.tool_calls, usage_metadata=usage)


# ---- live limits -----------------------------------------------------------


class DailyCap:
    """Server-side count of live runs today, shared by every session in this process."""

    def __init__(self, limit: int, path: Path = Path("/tmp/triage-demo/live-usage.json")):
        self.limit, self.path, self.lock = limit, path, threading.Lock()

    def take(self) -> bool:
        with self.lock:
            today = date.today().isoformat()
            usage = json.loads(self.path.read_text()) if self.path.exists() else {}
            if usage.get("date") != today:
                usage = {"date": today, "count": 0}
            if usage["count"] >= self.limit:
                return False
            usage["count"] += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(usage))
            return True

    def used(self) -> int:
        usage = json.loads(self.path.read_text()) if self.path.exists() else {}
        return usage.get("count", 0) if usage.get("date") == date.today().isoformat() else 0


# ---- running the graph -----------------------------------------------------


def start_run(case_id: str, mode: str, replay: dict):
    """Build a graph for one run. Returns (graph, initial_state, description)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from triage.agent.graph import build_graph, initial_state
    from triage.agent.nodes import Deps
    from triage.config import get_settings
    from triage.llm.providers import LLMClient, ModelHandle

    settings = get_settings()
    reviewer_llm = None
    if mode == "live":
        llm = LLMClient.from_settings(settings, role="investigator")
        reviewer_llm = LLMClient.from_settings(settings, role="reviewer")
        reviewer = settings.reviewer_enabled
        description = (
            f"Live: Scout on {llm.primary.provider}:{llm.primary.model}, "
            f"Skeptic on {reviewer_llm.primary.provider}:{reviewer_llm.primary.model}"
        )
    else:
        model = replay["config"]["provider_chain"][0]
        llm = LLMClient([ModelHandle("replay", model, ReplayChatModel(replay["llm_calls"], pace=True))], cache=None)
        reviewer = replay["config"]["reviewer_enabled"]
        description = f"Replay of the recorded eval run ({model}, prompt {replay['config']['prompt_version']}); no API calls"
    deps = Deps(llm=llm, reviewer_llm=reviewer_llm, settings=settings, reviewer_enabled=reviewer)
    graph = build_graph(deps, InMemorySaver())
    return graph, initial_state(case_id, deps, auto_approve=False), description


def drive(graph, run_id: str, payload):
    """Stream the graph until it ends or pauses.

    Yields (node, delta, state_values, pending_interrupt). `delta` is what that node returned,
    so the chat view can say what just happened; `state_values` is the full state so far.
    """
    from triage.agent.graph import thread_config

    config = thread_config(run_id)
    # durability="sync": each step's checkpoint is saved before it is reported, so get_state()
    # below is never one step behind (the default saves asynchronously).
    for update in graph.stream(payload, config, stream_mode="updates", durability="sync"):
        pending = update["__interrupt__"][0].value if "__interrupt__" in update else None
        node = next((k for k in update if k != "__interrupt__"), None)
        yield node, (update.get(node) or {}) if node else {}, graph.get_state(config).values, pending


AGENT_OF_NODE = {"investigate": "scout", "run_tools": "scout", "diagnose": "scout", "review": "skeptic", "review_tools": "skeptic"}


def next_agent(graph, run_id: str) -> str | None:
    """Which agent works next ('scout' / 'skeptic'), from the graph's own list of next nodes."""
    from triage.agent.graph import thread_config

    for node in graph.get_state(thread_config(run_id)).next:
        if node in AGENT_OF_NODE:
            return AGENT_OF_NODE[node]
    return None


def resume_payload(approved: bool, note: str):
    from langgraph.types import Command

    return Command(resume={"approved": approved, "note": note or ""})
