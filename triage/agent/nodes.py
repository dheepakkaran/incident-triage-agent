"""The graph's nodes. Each one reads the state and returns a partial update.

Order: validate_alert -> investigate <-> run_tools -> diagnose -> review <-> review_tools
       -> propose_action -> approval_gate -> report      (escalate -> report on any failure)

Every node appends one entry to `steps` (the timeline) and its duration to `timings`.
"""

import time
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from triage.agent import prompts
from triage.config import Settings
from triage.guardrails.actions import (
    check_allowlist,
    check_runbook_fault,
    check_runbook_match,
    check_target,
    simulate_action,
    to_proposal,
)
from triage.guardrails.input import validate_alert as check_alert
from triage.guardrails.input import wrap_tool_output
from triage.guardrails.output import (
    check_candidates,
    check_fault_evidence,
    parse_structured,
    top_confidence,
    verify_evidence,
)
from triage.guardrails.process import check_budget, check_known_tool, find_duplicate, validate_args
from triage.llm.providers import LLMClient, LLMError
from triage.schemas import ActionChoice, Approval, Diagnosis, ReviewVerdict, ToolResult
from triage.tools import INVESTIGATOR_TOOLS, REVIEWER_TOOLS, TOOLS, run_tool, tool_schemas
from triage.tools import runbooks as runbook_search
from triage.tools.db import ToolContext
from triage.tools.services import call_edges

MAX_JSON_RETRIES = 2  # structured output: 1 try + 2 retries with the error message


@dataclass
class Deps:
    """Things nodes need that are not state: the LLM client and settings."""

    llm: LLMClient  # Scout (investigator)
    settings: Settings
    reviewer_enabled: bool
    extras: dict = field(default_factory=dict)
    reviewer_llm: LLMClient | None = None  # Skeptic (reviewer); None = same client as Scout

    @property
    def review_llm(self) -> LLMClient:
        return self.reviewer_llm or self.llm


# ---- small helpers -------------------------------------------------------


def _step(node: str, started: float, **detail) -> dict:
    seconds = round(time.perf_counter() - started, 3)
    return {"steps": [{"node": node, "seconds": seconds, **detail}], "timings": {node: seconds}}


def _event(state: dict, layer: str, check: str, detail: str) -> dict:
    return {"layer": layer, "check": check, "case_id": state["case_id"], "detail": detail}


def _ctx(state: dict, deps: Deps) -> ToolContext:
    return ToolContext(state["case_id"], deps.settings.db_path, tuple(state["services"]), state["inject_ts"])


def _escalate(reason: str, events: list | None = None, **extra) -> dict:
    return {"escalation_reason": reason, "guardrail_events": events or [], **extra}


def _structured_call(deps: Deps, messages: list, schema, check, purpose: str, state: dict, first_reply=None, llm=None):
    """Ask for JSON, parse it with Pydantic, run `check` (extra guardrails), retry with the error.

    `first_reply` lets a caller pass a reply it already has (the reviewer's last turn) as attempt 1.
    Returns (parsed_or_None, last_error, llm_records, guardrail_events).
    """
    records, events, error = [], [], None
    for attempt in range(1 + MAX_JSON_RETRIES):
        if attempt == 0 and first_reply is not None:
            reply = first_reply
        else:
            reply, record = (llm or deps.llm).invoke(messages, purpose=f"{purpose}#{attempt + 1}")
            records.append(record)
        parsed, error = parse_structured(reply.content, schema)
        check_name = "schema"
        if parsed is not None:
            problems = check(parsed)
            error = "; ".join(problems) if problems else None
            check_name = "evidence" if any("quote" in p or "cites" in p for p in problems) else "service"
        if error is None:
            return parsed, None, records, events
        events.append(_event(state, "output", check_name, f"{purpose} attempt {attempt + 1}: {error}"))
        messages = messages + [AIMessage(content=str(reply.content)), HumanMessage(prompts.RETRY_JSON.format(error=error))]
    return None, error, records, events


def _tool_outputs_block(state: dict) -> str:
    results = state.get("tool_results") or {}
    return "\n".join(wrap_tool_output(cid, r["tool"], r["data"]) for cid, r in results.items()) or "(no tool output)"


# ---- nodes ---------------------------------------------------------------


def validate_alert(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    try:
        ctx = ToolContext.for_case(state["case_id"], deps.settings.db_path)
    except KeyError as err:
        event = _event(state, "input", "case", str(err))
        return {"status": "rejected", **_escalate(str(err), [event]), **_step("validate_alert", started, ok=False)}

    alert, error = check_alert(state["alert"], list(ctx.services))
    if error:
        event = _event(state, "input", "alert", error)
        return {
            "status": "rejected",
            "services": list(ctx.services),
            **_escalate(error, [event]),
            **_step("validate_alert", started, ok=False, error=error),
        }

    system = prompts.INVESTIGATOR_SYSTEM.format(budget=deps.settings.max_tool_calls, fault_hints=prompts.FAULT_TYPE_HINTS)
    edges = [f"{a} -> {b}" for a, b, _ in call_edges(ctx)]
    return {
        "status": "running",
        "services": list(ctx.services),
        "inject_ts": ctx.inject_ts,
        "call_graph": edges,
        "messages": [SystemMessage(system), HumanMessage(f"Alert: {alert.text}\nStart the investigation.")],
        "tool_calls_used": 0,
        "revision_round": 0,
        "rejections": 0,
        **_step("validate_alert", started, ok=True),
    }


def investigate(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    used, budget = state.get("tool_calls_used", 0), deps.settings.max_tool_calls
    if used >= budget:
        return _step("investigate", started, skipped="tool budget already used")
    try:
        reply, record = deps.llm.invoke(state["messages"], tools=tool_schemas(INVESTIGATOR_TOOLS), purpose="investigate")
    except LLMError as err:
        return {**_escalate(f"LLM unavailable during investigation: {err}"), **_step("investigate", started, error=True)}
    calls = [{"tool": c["name"], "args": c["args"]} for c in reply.tool_calls]
    return {"messages": [reply], "llm_calls": [record], **_step("investigate", started, tool_calls=calls)}


def execute_tool_calls(state: dict, deps: Deps, calls: list[dict], allowed: list[str], used: int, budget: int):
    """The guarded tool executor shared by the investigator and the reviewer.

    For each requested call: known tool? budget left? valid args? duplicate? Then run it,
    sanitize and cap the output, and store it under a short id (t1, t2, ...).
    Every call gets an answer, so the conversation never has a dangling tool call.
    """
    ctx = _ctx(state, deps)
    known = dict(state.get("tool_results") or {})
    new_results, messages, events, timeline = {}, [], [], []
    for call in calls:
        name, raw_args = call["name"], call.get("args") or {}
        problem = check_budget(used, budget)
        if problem:
            events.append(_event(state, "process", "budget", f"{name} not run: {problem}"))
        else:
            used += 1
            problem = check_known_tool(name, allowed)
            if problem:
                events.append(_event(state, "process", "unknown_tool", problem))
        args = None
        if not problem:
            args, problem = validate_args(TOOLS[name].args_model, raw_args, list(ctx.services))
            if problem:
                events.append(_event(state, "process", "args", f"{name}({raw_args}) rejected: {problem}"))
        if problem:
            messages.append(ToolMessage(content=f"REJECTED: {problem}", tool_call_id=call["id"], name=name))
            timeline.append({"tool": name, "args": raw_args, "rejected": problem})
            continue

        normalized = args.model_dump()
        duplicate = find_duplicate(name, normalized, {**known, **new_results})
        if duplicate:
            previous = {**known, **new_results}[duplicate]
            events.append(_event(state, "process", "duplicate", f"{name}({normalized}) repeats {duplicate}"))
            note = f"NOTE: duplicate call, same result as {duplicate}. Cite {duplicate}.\n"
            messages.append(ToolMessage(content=note + wrap_tool_output(duplicate, name, previous["data"]), tool_call_id=call["id"], name=name))
            timeline.append({"tool": name, "args": normalized, "duplicate_of": duplicate})
            continue

        call_id = f"t{len(known) + len(new_results) + 1}"
        try:
            text, truncated, removed = run_tool(name, ctx, args)
        except Exception as err:  # a tool bug must not crash the run; the model is told what happened
            text, truncated, removed = f"tool error: {type(err).__name__}: {err}", False, []
        if removed:
            events.append(_event(state, "input", "injection", f"{call_id}: neutralized {len(removed)} instruction-like line(s): {removed[0][:120]}"))
        result = ToolResult(tool_call_id=call_id, tool=name, args=normalized, data=text, truncated=truncated)
        new_results[call_id] = result.model_dump()
        messages.append(ToolMessage(content=wrap_tool_output(call_id, name, text), tool_call_id=call["id"], name=name))
        timeline.append({"tool": name, "args": normalized, "id": call_id, "chars": len(text), "truncated": truncated})
    return new_results, messages, events, used, timeline


def run_tools(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    calls = state["messages"][-1].tool_calls
    results, messages, events, used, timeline = execute_tool_calls(
        state, deps, calls, INVESTIGATOR_TOOLS, state.get("tool_calls_used", 0), deps.settings.max_tool_calls
    )
    return {
        "tool_results": results,
        "messages": messages,
        "guardrail_events": events,
        "tool_calls_used": used,
        **_step("run_tools", started, calls=timeline),
    }


def _diagnose_input(state: dict) -> str:
    parts = [f"Alert: {state['alert']['text']}"]
    notes = [m.content for m in state.get("messages", []) if isinstance(m, AIMessage) and m.content and not m.tool_calls]
    if notes:
        parts.append(f"Investigator notes: {notes[-1]}")
    for review in state.get("reviews") or []:
        if review["verdict"] == "revise":
            parts.append(f"A reviewer challenged an earlier diagnosis: {review['reason']} (suspects {review['suspected_service']})")
    if state.get("approval") and not state["approval"]["approved"]:
        parts.append(f"The on-call engineer rejected the last proposal. Their note: {state['approval']['note']}")
    parts.append("Tool outputs:\n" + _tool_outputs_block(state))
    return "\n\n".join(parts)


def diagnose(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    services = state["services"]
    system = prompts.DIAGNOSE_SYSTEM.format(services=", ".join(services), fault_hints=prompts.FAULT_TYPE_HINTS)
    messages = [SystemMessage(system), HumanMessage(_diagnose_input(state))]
    results = state.get("tool_results") or {}

    def check(d: Diagnosis) -> list[str]:
        problems = check_candidates(d.candidates, services) + verify_evidence(d.evidence, results)
        return problems or check_fault_evidence(d.candidates, d.evidence)

    try:
        diagnosis, error, records, events = _structured_call(deps, messages, Diagnosis, check, "diagnose", state)
    except LLMError as err:
        return {**_escalate(f"LLM unavailable during diagnosis: {err}"), **_step("diagnose", started, error=True)}

    if diagnosis is None:
        reason = f"diagnosis failed output guardrails after {1 + MAX_JSON_RETRIES} attempts: {error}"
        return {"llm_calls": records, **_escalate(reason, events), **_step("diagnose", started, ok=False)}

    update = {"diagnosis": diagnosis.model_dump(), "llm_calls": records, "guardrail_events": events}
    confidence = top_confidence(diagnosis.candidates)
    if confidence < deps.settings.confidence_threshold:
        event = _event(state, "output", "confidence", f"top confidence {confidence:.2f} < {deps.settings.confidence_threshold}")
        update.update(_escalate(f"low confidence ({confidence:.2f})", events + [event]))
    top = diagnosis.candidates[0]
    return {**update, **_step("diagnose", started, top=f"{top.service}/{top.fault_type}", confidence=confidence)}


def _review_input(state: dict) -> str:
    d = state["diagnosis"]
    candidates = ", ".join(f"{c['service']} ({c['fault_type']}, {c['confidence']:.2f})" for c in d["candidates"])
    evidence = "\n".join(f"- [{e['tool_call_id']}] \"{e['quote']}\" -- {e['why_it_matters']}" for e in d["evidence"])
    return (
        f"Alert: {state['alert']['text']}\n\n"
        f"Diagnosis (ranked): {candidates}\nSummary: {d['summary']}\n\nEvidence:\n{evidence}\n\n"
        f"Call graph (caller -> callee):\n" + "\n".join(state.get("call_graph") or []) + "\n\n"
        f"Investigator tool outputs (you may cite these ids):\n{_tool_outputs_block(state)}"
    )


def review(state: dict, deps: Deps) -> dict:
    """The reviewer agent: a separate LLM conversation that tries to prove the diagnosis wrong."""
    started = time.perf_counter()
    messages = list(state.get("review_messages") or [])
    used = state.get("reviewer_tool_calls", 0)
    if not messages:
        system = prompts.REVIEWER_SYSTEM.format(budget=deps.settings.reviewer_max_tool_calls)
        messages = [SystemMessage(system), HumanMessage(_review_input(state))]
        used = 0
    budget_left = deps.settings.reviewer_max_tool_calls - used
    try:
        tools = tool_schemas(REVIEWER_TOOLS) if budget_left > 0 else None
        reply, record = deps.review_llm.invoke(messages, tools=tools, purpose="review")
    except LLMError as err:
        return {**_escalate(f"LLM unavailable during review: {err}"), **_step("review", started, error=True)}

    if reply.tool_calls and budget_left > 0:
        calls = [{"tool": c["name"], "args": c["args"]} for c in reply.tool_calls]
        return {"review_messages": messages + [reply], "reviewer_tool_calls": used, "llm_calls": [record], **_step("review", started, tool_calls=calls)}

    # Final answer: parse the verdict (retrying with the error), then check its evidence.
    services, results = state["services"], state.get("tool_results") or {}

    def check(v: ReviewVerdict) -> list[str]:
        problems = verify_evidence(v.evidence, results)
        if v.verdict == "revise":
            current_top = state["diagnosis"]["candidates"][0]["service"]
            if v.suspected_service == current_top:
                problems.append(f"'revise' must name a different service than the diagnosis ({current_top}); if you agree, say 'agree'")
            elif f"{v.suspected_service} -> {current_top}" in (state.get("call_graph") or []):
                problems.append(
                    f"{v.suspected_service} calls {current_top}, so it is upstream and shows {current_top}'s symptoms. "
                    f"Revise only toward a service {current_top} depends on or an unrelated one; otherwise say 'agree'"
                )
            elif v.suspected_service not in services:
                problems.append(f"suspected_service '{v.suspected_service}' is not a known service")
            if not v.evidence:
                problems.append("a 'revise' verdict must cite at least one quote")
        return problems

    try:
        verdict, error, records, events = _structured_call(
            deps, messages, ReviewVerdict, check, "review", state, first_reply=reply, llm=deps.review_llm
        )
    except LLMError as err:
        return {**_escalate(f"LLM unavailable during review: {err}"), **_step("review", started, error=True)}
    records = [record] + records
    if verdict is None:
        reason = f"reviewer output failed guardrails: {error}"
        return {"llm_calls": records, "review_messages": [], **_escalate(reason, events), **_step("review", started, ok=False)}

    update = {"reviews": [verdict.model_dump()], "review_messages": [], "llm_calls": records, "guardrail_events": events}
    detail = {"verdict": verdict.verdict, "suspected": verdict.suspected_service}
    if verdict.verdict == "agree":
        return {**update, **_step("review", started, **detail)}

    if state.get("revision_round", 0) >= 1:
        top = state["diagnosis"]["candidates"][0]["service"]
        reason = f"investigator and reviewer still disagree after one revision (investigator: {top}, reviewer: {verdict.suspected_service})"
        return {**update, **_escalate(reason, events), **_step("review", started, **detail)}

    # Send the case back to the investigator once, with the reviewer's reason in its context.
    left = deps.settings.max_tool_calls - state.get("tool_calls_used", 0)
    note = (
        f"A reviewer challenged your diagnosis. Reviewer: {verdict.reason} "
        f"(suspects {verdict.suspected_service}). Re-check with tools if needed ({left} calls left), then summarize again."
    )
    return {**update, "revision_round": 1, "messages": [HumanMessage(note)], **_step("review", started, **detail)}


def review_tools(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    messages = list(state["review_messages"])
    results, tool_messages, events, used, timeline = execute_tool_calls(
        state, deps, messages[-1].tool_calls, REVIEWER_TOOLS, state.get("reviewer_tool_calls", 0), deps.settings.reviewer_max_tool_calls
    )
    return {
        "tool_results": results,
        "review_messages": messages + tool_messages,
        "reviewer_tool_calls": used,
        "guardrail_events": events,
        **_step("review_tools", started, calls=timeline),
    }


def propose_action(state: dict, deps: Deps) -> dict:
    started = time.perf_counter()
    top = state["diagnosis"]["candidates"][0]
    query = f"{top['fault_type']} fault in {top['service']}: {state['diagnosis']['summary']}"[:300]
    hits = runbook_search.search(query, 3)
    # Semantic search can miss; the runbook written for the diagnosed fault type is always a candidate.
    exact = runbook_search.by_fault_type(top["fault_type"])
    if exact and exact["id"] not in {h["id"] for h in hits}:
        hits.append(exact)
    runbooks = {h["id"]: h for h in hits}
    call_id = f"t{len(state.get('tool_results') or {}) + 1}"
    text = runbook_search.render_hits(query, hits)
    result = ToolResult(tool_call_id=call_id, tool="search_runbooks", args={"query": query, "k": 3}, data=text)

    matching = [rb_id for rb_id, rb in runbooks.items() if rb["fault_type"] == top["fault_type"]]
    hint = f"Runbook for fault_type={top['fault_type']}: {matching[0]}." if matching else "No retrieved runbook matches the fault type."
    messages = [
        SystemMessage(prompts.ACTION_SYSTEM),
        HumanMessage(
            f"Diagnosis: {top['service']} ({top['fault_type']}, confidence {top['confidence']:.2f}). "
            f"{state['diagnosis']['summary']}\n{hint}\n\n{wrap_tool_output(call_id, 'search_runbooks', text)}"
        ),
    ]

    def check(choice: ActionChoice) -> list[str]:
        problem = check_runbook_fault(choice, runbooks, top["fault_type"])
        return [problem] if problem else []

    try:
        choice, error, records, events = _structured_call(deps, messages, ActionChoice, check, "propose_action", state)
    except LLMError as err:
        return {**_escalate(f"LLM unavailable while choosing an action: {err}"), **_step("propose_action", started, error=True)}
    update = {"tool_results": {call_id: result.model_dump()}, "runbooks": runbooks, "llm_calls": records}
    if choice is None:
        return {**update, **_escalate(f"no valid action proposal: {error}", events), **_step("propose_action", started, ok=False)}

    # Action guardrails: allowlist, real target, runbook allows it.
    for layer_check, problem in (
        ("allowlist", check_allowlist(choice)),
        ("target", check_target(choice, state["services"])),
        ("runbook_match", check_runbook_match(choice, runbooks)),
    ):
        if problem:
            event = _event(state, "action", layer_check, problem)
            blocked = {"blocked_action": choice.model_dump()}
            return {**update, **blocked, **_escalate(f"action blocked: {problem}", events + [event]), **_step("propose_action", started, blocked=problem)}

    proposal = to_proposal(choice)
    if proposal.action == "escalate_to_human":
        return {**update, "action": proposal.model_dump(), **_escalate(f"model chose to escalate: {proposal.reason}", events), **_step("propose_action", started, action=proposal.action)}
    detail = {"action": f"{proposal.action}({proposal.target_service})", "risk": proposal.risk}
    return {**update, "action": proposal.model_dump(), "guardrail_events": events, **_step("propose_action", started, **detail)}


def approval_gate(state: dict, deps: Deps) -> dict:
    """Risky actions pause the graph here with interrupt() until a human answers.

    Code before interrupt() runs again on resume, so nothing above it has side effects.
    """
    started = time.perf_counter()
    proposal = state["action"]
    if not proposal["needs_approval"]:
        approval = Approval(approved=True, note="low risk: no approval needed", auto=True)
    elif state.get("auto_approve"):
        approval = Approval(approved=True, note="auto-approved by the eval policy (--auto-approve)", auto=True)
    else:
        evidence = (state.get("diagnosis") or {}).get("evidence", [])[:3]
        decision = interrupt(
            {
                "action": proposal["action"],
                "target_service": proposal["target_service"],
                "reason": proposal["reason"],
                "runbook_id": proposal["runbook_id"],
                "evidence": evidence,
            }
        )
        approval = Approval(approved=bool(decision.get("approved")), note=str(decision.get("note", ""))[:500])

    if approval.approved:
        outcome = simulate_action(proposal, state["case_id"], state["run_id"], deps.settings.runs_dir)
        return {"approval": approval.model_dump(), "status": "diagnosed", **_step("approval_gate", started, approved=True, outcome=outcome)}

    rejections = state.get("rejections", 0) + 1
    update = {"approval": approval.model_dump(), "rejections": rejections}
    if rejections >= 2:
        return {**update, **_escalate(f"the on-call engineer rejected the proposal twice: {approval.note}"), **_step("approval_gate", started, approved=False)}
    note = (
        f"The on-call engineer rejected {proposal['action']}({proposal['target_service']}). Their note: "
        f"{approval.note or '(none)'}. Re-check the diagnosis, then summarize again."
    )
    return {**update, "messages": [HumanMessage(note)], **_step("approval_gate", started, approved=False)}


def escalate(state: dict, deps: Deps) -> dict:
    """Hand over to a human with whatever was found. Never executes anything."""
    started = time.perf_counter()
    lines = [f"ESCALATED: {state.get('escalation_reason') or 'unknown reason'}"]
    diagnosis = state.get("diagnosis")
    if diagnosis:
        for c in diagnosis["candidates"]:
            lines.append(f"candidate: {c['service']} ({c['fault_type']}, confidence {c['confidence']:.2f})")
        for e in diagnosis["evidence"]:
            lines.append(f"evidence [{e['tool_call_id']}]: {e['quote']}")
    for r in state.get("reviews") or []:
        lines.append(f"reviewer: {r['verdict']} (suspects {r['suspected_service']}): {r['reason']}")
    if state.get("blocked_action"):
        lines.append(f"blocked action (not executed): {state['blocked_action']}")
    if not diagnosis and state.get("tool_results"):
        lines.append(f"tool outputs collected: {', '.join(state['tool_results'])}")
    status = "rejected" if state.get("status") == "rejected" else "escalated"
    return {"status": status, "escalation_note": "\n".join(lines), **_step("escalate", started)}


def report(state: dict, deps: Deps) -> dict:
    from triage.tracing import write_run_files

    started = time.perf_counter()
    status = state.get("status")
    if status not in ("diagnosed", "escalated", "rejected"):
        status = "diagnosed" if state.get("action") else "escalated"
    final = {**state, "status": status}
    report_dict = write_run_files(final, deps)
    return {"status": status, "report": report_dict, **_step("report", started, status=status)}


def new_run_id(case_id: str) -> str:
    return f"{case_id}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

