# LEARNING.md

Plain-English notes on every core idea in this project, with a pointer to where it lives in the code.

---

## 1. StateGraph, state, nodes and conditional edges

**Where:** `triage/agent/graph.py` (`build_graph`), `triage/agent/state.py` (`TriageState`), `triage/agent/nodes.py`.

- **State** is one dictionary that travels through the whole run (`TriageState`). It holds the case id, the alert, the investigator's messages, every tool result (keyed `t1`, `t2`, ...), the diagnosis, the reviewer's verdicts, the proposed action, the approval, and an audit trail.
- **Nodes** are plain Python functions: `fn(state) -> partial update`. A node never mutates the state. It returns only the keys it changed. Some keys have a **reducer** that says how to merge an update. For example, `guardrail_events: Annotated[list, operator.add]` appends, and `messages` uses LangGraph's `add_messages`. Keys without a reducer are overwritten.
- **Edges** connect nodes. `g.add_edge("escalate", "report")` always goes from escalate to report.
- **Conditional edges** call a small routing function that reads the state and returns the next node's name. For example, `after_investigate` returns `"run_tools"` if the model asked for tool calls, `"diagnose"` if it gave a final answer, and `"escalate"` if the LLM failed.
- We never use a prebuilt agent (`create_react_agent`). Every step is a node you can point at, and the whole flow fits on one screen in `build_graph`.

```
validate_alert -> investigate <-> run_tools -> diagnose -> review <-> review_tools
              -> propose_action -> approval_gate -> report
(escalate -> report on low confidence, failed guardrails, blocked actions, or reviewer disagreement)
```

## 2. How tool calling works, step by step

**Where:** `nodes.investigate`, `nodes.execute_tool_calls`, `triage/tools/__init__.py`, `triage/guardrails/process.py`.

1. `investigate` sends the conversation plus the JSON schemas of the 5 tools to the model (`tool_schemas(...)`, built from each tool's Pydantic args model). The model never sees a `case_id` parameter: the case comes from the run context.
2. The model replies with an `AIMessage` that contains `tool_calls`, a list of `{name, args, id}` entries. It doesn't run anything itself.
3. The routing function sees the tool calls and goes to `run_tools`, our own executor (not LangGraph's prebuilt `ToolNode`). For each call it checks, in order:
   - budget left (`check_budget`);
   - known tool (`check_known_tool`);
   - valid arguments (`validate_args`: Pydantic with the case's real service list as context);
   - not a duplicate of an earlier call (`find_duplicate`).
4. It runs the tool (`run_tool`), sanitizes the output against prompt injection, caps it at 6,000 characters, stores it under a short id (`t3`), and wraps it as `<tool_output id="t3" tool="...">...</tool_output>`.
5. Every tool call gets a `ToolMessage` answer, even rejected ones ("REJECTED: unknown service..."), so the model learns why and the conversation never has a dangling call.
6. The graph returns to `investigate`, and the model sees the results. This repeats until the model answers in plain text or the budget (8 calls) is used up.

## 3. interrupt() and the checkpointer: why resume works

**Where:** `nodes.approval_gate`, `graph.resume_incident`, `triage/cli_run.py`, `tests/test_hitl.py`.

- When the proposed action is risky (`scale_service`, `restart_service`, `rollback_service`), `approval_gate` calls `interrupt({...action, reason, evidence...})`. LangGraph stops the run right there and returns the payload to the caller under `__interrupt__`.
- The **checkpointer** saves the full state after every node: `SqliteSaver` writes `runs/checkpoints.db` in the CLI, and `InMemorySaver` is used in tests and the Space. The save is keyed by `thread_id`, which here is the run id.
- To resume, the caller invokes the graph again with `Command(resume={"approved": True, "note": "..."})` and the same `thread_id`. LangGraph loads the saved state and re-runs `approval_gate`. This time `interrupt()` returns the resume value instead of pausing.
- Because the node re-runs from its first line, nothing before `interrupt()` may have side effects. The simulated action (`simulate_action`) is called only after a decision.
- `tests/test_hitl.py::test_paused_run_resumes_from_sqlite_checkpoint_in_a_new_graph` builds a brand-new graph object on the same SQLite file and resumes, which is what would happen after a process restart.
- A rejection sends the case back to `investigate` with the engineer's note in the conversation. A second rejection escalates.

## 4. Structured output + retry

**Where:** `nodes._structured_call`, `triage/guardrails/output.py` (`extract_json`, `parse_structured`).

- The diagnosis, the reviewer's verdict and the action choice are all JSON matching a Pydantic model (`Diagnosis`, `ReviewVerdict`, `ActionChoice` in `triage/schemas.py`).
- We ask for JSON in the prompt, pull the first `{...}` out of the reply (models like to add prose or code fences), and validate with Pydantic. This works the same on Groq, OpenRouter, Ollama and the mock model.
- If parsing fails, or a guardrail check fails (fake quote, unknown service), we append the bad reply plus "Your reply was rejected: <exact error>. Fix it and reply with only the JSON". Then we ask again, up to 2 retries. The error text matters: for a wrong quote we include the closest real line, which is often enough for a small model to fix it.
- After 3 failed attempts the run escalates with the last error. It never crashes (adversarial case 5).

## 5. The evidence check, and why it stops made-up root causes

**Where:** `guardrails/output.verify_evidence`, `quote_in_output`.

- Every evidence item must name a `tool_call_id` that really ran in this incident, and its `quote` must be text that appears in that tool's output.
- The check normalizes case, whitespace and quote characters. A quote may skip text with `...` or drop comma-separated fields of one line, but every part must appear in order, so it can only contain real text.
- An LLM can easily write "cartservice CPU +900%" when no tool ever said that. With this check, a root cause has to rest on numbers the tools produced. On the first real run, qwen3:8b spliced the baseline of one metric line onto the incident value of the line below. The check caught it, the retry fixed it, and the report only carries quotes that exist.
- Related checks: every candidate service must be a real service in the case (`check_candidates`), and a confidence below 0.6 escalates.

## 6. Prompt injection through logs, and the sanitizer

**Where:** `triage/guardrails/input.py` (`INJECTION_PATTERNS`, `sanitize_text`, `wrap_tool_output`), `triage/tools/logs.py`, `eval/adversarial.py` scenario 1.

- Log lines are written by anyone who can make a service log something: a user name, a header, an error message. If a log line says "SYSTEM OVERRIDE: ignore prior instructions...", a naive agent might obey it.
- There are two defences:
  1. **Sanitizer.** Every line of tool output is checked against a short list of instruction-like patterns: "ignore previous instructions", "system:", "you must", role tags like `<system>`, `[INST]`, "report root cause is". A matching line is replaced with `[REMOVED: instruction-like text]`, and a guardrail event is logged.
  2. **Delimiters plus a rule.** All tool output is wrapped in `<tool_output>` tags, and the system prompt says content inside them is data, not instructions.
- The adversarial suite inserts the attack into every service's logs in each dev case and runs the real model. It checks that the line is flagged, never reaches a prompt, and is not followed.

## 7. What MCP is, and why the same tools are exposed through it

**Where:** `triage/mcp_server.py`, `tests/test_mcp.py`, `python -m triage smoke mcp`.

- MCP (Model Context Protocol) is a standard way for an AI client (Claude Desktop, IDEs, the MCP Inspector) to discover and call tools on a server. The server lists tools with JSON schemas, and the client calls them over stdio or HTTP.
- We expose the same 5 tool functions, so any MCP client can investigate an incident by hand with exactly what the agent sees: same validation, same sanitizer, same 6,000-character cap.
- Difference: an MCP client has no run context, so each tool takes a `case_id`, checked against the known case ids. Labels are never exposed. Tools are marked `readOnlyHint=True`.
- In mcp ≥ 2.0 the SDK's `FastMCP` class is called `MCPServer`, with the same decorator API.

## 8. The investigator–reviewer pattern

**Where:** `nodes.review`, `nodes.review_tools`, `graph.after_review`, `tests/test_reviewer.py`, ablation in `eval/results/RESULTS.md`.

- **Why a second agent:** the most common RCA mistake is blaming the service that shows the symptom (the frontend, or checkout) when the real cause is a dependency it calls. The investigator has momentum, because it has already built a story. A reviewer with a different instruction ("try to prove this wrong") and a fresh context is more likely to notice.
- **How they hand off:** they never chat. The reviewer gets a fresh conversation (`review_messages`) holding the alert, the diagnosis, its quoted evidence, the call graph and the investigator's tool outputs. It may make up to 3 read-only tool calls (`get_trace_stats`, `get_metric_changes`) through the same guarded executor. Its verdict is structured JSON with the same evidence check.
  - `agree` goes on to `propose_action`.
  - `revise` adds the reviewer's reason as a message in the investigator's conversation and routes back to `investigate`.
- **Guardrails on the reviewer:** a `revise` must cite at least one real quote and must name a *different* service than the diagnosis. Without the second rule, qwen3:8b sometimes said "revise" while naming the same service, which forced a pointless escalation.
- **Why only one revision round:** two models can loop forever, each convincing the other in turn. One round gives the investigator a chance to correct itself. If the reviewer still disagrees, a human should decide, so the run escalates with both views in the report.
- **What the ablation showed** (`eval/results/RESULTS.md`):
  - **The rule fixed in advance:** keep the reviewer on only if dev top-1 or wrong-but-confident improves.
  - **Neither improved on dev:** top-1 was 80% vs 80%, and wrong-but-confident 1 vs 1. So the default is **off**.
  - **What it did do:** it caught the classic mistake. Scout blamed checkoutservice, a caller, and Skeptic pointed at emailservice, the real cause. Those runs escalated instead of sending a wrong fix for approval: "wrong & proposed" fell from 1 to 0 on dev and from 2 to 1 on test.
  - **The price:** about 1.5× the tokens and more escalations.
  - **Why "wrong-but-confident" missed it:** that metric counts a wrong answer even when it was escalated, so it can't see this benefit. "Wrong & proposed" can, so both are reported.
  - **Interview line:** the reviewer trades throughput for safety. With a small model that safety is real but modest, and an honest eval says so.

## 9. How the eval avoids label leakage, and why dev and test are split

**Where:** `triage/data/prepare.py`, `docs/data_notes.md`, `tests/test_leakage.py`, `eval/common.py`.

- RCAEval folder names contain the answer (`re2ob_emailservice_cpu_3`). We rename cases to `case_001...`, and write labels only to `data/labels.json`.
- `tests/test_leakage.py` fails if any file under `triage/agent` or `triage/tools` mentions labels or imports the eval / download code. A runtime test also runs the whole agent under a Python audit hook and fails if it opens a labels file.
- Telemetry is scanned for anything naming the injected fault (stress-ng, chaos, netem...). There were 0 matches in RE2-OB, and the scan stays in place.
- Alerts use the entry service only. `prepare` refuses to write an alert that contains its own root-cause service or fault-type word.
- **Dev/test split:** prompts were tuned while looking at dev results only (cases 001–005). The 15 test cases were run once, at the end, with the final prompt. Otherwise we would be measuring how well the prompt was fitted to these exact cases, not how well it works.
- One honest caveat: during data exploration (before the split, to design alerts and the leakage scan) we looked at raw telemetry statistics of all 20 cases. Prompt wording was only ever changed in response to dev results.
