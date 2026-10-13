"""Turn graph steps into a conversation: Scout (investigator), Skeptic (reviewer), you, and the system.

`messages_for(node, delta, values)` looks at what one node just returned and says, in plain
words, what happened. `render(transcript)` turns the messages into chat bubbles (HTML).
The technical detail (raw tool output, every step) stays in the "Under the hood" tab.
"""

import html

WHO = {
    "scout": ("Scout", "investigator"),
    "skeptic": ("Skeptic", "reviewer"),
    "human": ("You", "on-call engineer"),
}


def _e(text) -> str:
    return html.escape(str(text))


def _tool_phrase(name: str, args: dict) -> str:
    service = args.get("service")
    if name == "list_services":
        return "Mapping which services call which."
    if name == "get_metric_changes":
        return f"Checking which metrics moved the most{f' for <b>{_e(service)}</b>' if service else ''}."
    if name == "query_logs":
        return f"Reading <b>{_e(args.get('level', 'ERROR'))}</b> logs of <b>{_e(service)}</b>."
    if name == "get_trace_stats":
        return f"Looking at calls into and out of <b>{_e(service)}</b>."
    if name == "search_runbooks":
        return f"Searching the runbooks for <i>{_e(args.get('query', ''))[:80]}</i>."
    return f"Calling <code>{_e(name)}</code>."


def _result_snippet(call_id: str, result: dict, lines: int = 3) -> str:
    data_lines = [line for line in result["data"].splitlines()[1:] if line.strip()][:lines]
    body = "\n".join(_e(line[:150]) for line in data_lines) or "(no rows)"
    return f'<div class="snip"><span class="tid">{_e(call_id)}</span><pre>{body}</pre></div>'


def _guardrail(event: dict) -> tuple[str, str]:
    detail = event["detail"]
    return ("system", f"🛡️ Guardrail <b>{_e(event['layer'])}/{_e(event['check'])}</b>: {_e(detail[:170])}{'…' if len(detail) > 170 else ''}")


def messages_for(node: str, delta: dict, values: dict) -> list[tuple[str, str]]:
    """(who, html) messages describing what `node` just did."""
    out: list[tuple[str, str]] = []
    if node == "validate_alert":
        if delta.get("status") == "rejected":
            out.append(("system", f"⛔ Alert rejected: {_e(delta.get('escalation_reason'))}"))
        else:
            out.append(("system", "✅ Alert is valid. Scout starts investigating."))
    elif node == "investigate":
        for reply in delta.get("messages") or []:
            for call in getattr(reply, "tool_calls", None) or []:
                out.append(("scout", _tool_phrase(call["name"], call.get("args") or {})))
            text = getattr(reply, "content", "")
            if text and not getattr(reply, "tool_calls", None):
                out.append(("scout", _e(text)))
        if delta.get("escalation_reason"):
            out.append(("system", f"⚠️ {_e(delta['escalation_reason'])}"))
    elif node in ("run_tools", "review_tools"):
        who = "scout" if node == "run_tools" else "skeptic"
        snippets = "".join(_result_snippet(cid, r) for cid, r in (delta.get("tool_results") or {}).items())
        if snippets:
            out.append((who, f"<span class='muted'>Here is what came back:</span>{snippets}"))
    elif node == "diagnose":
        d = delta.get("diagnosis")
        if d:
            top = d["candidates"][0]
            others = ", ".join(f"{_e(c['service'])} ({_e(c['fault_type'])})" for c in d["candidates"][1:])
            evidence = "".join(f"<li><span class='tid'>{_e(e['tool_call_id'])}</span> <code>{_e(e['quote'])}</code></li>" for e in d["evidence"][:3])
            out.append(
                (
                    "scout",
                    f"My diagnosis: <b>{_e(top['service'])}</b> has a <b>{_e(top['fault_type'])}</b> fault "
                    f"(confidence {top['confidence']:.2f}){f'. Also possible: {others}' if others else ''}."
                    f"<ul>{evidence}</ul><span class='muted'>{_e(d['summary'])}</span>",
                )
            )
    elif node == "review":
        pending = delta.get("review_messages") or []
        if pending and getattr(pending[-1], "tool_calls", None):
            for call in pending[-1].tool_calls:
                out.append(("skeptic", "Let me check that myself. " + _tool_phrase(call["name"], call.get("args") or {})))
        for r in delta.get("reviews") or []:
            evidence = "".join(f"<li><span class='tid'>{_e(e['tool_call_id'])}</span> <code>{_e(e['quote'])}</code></li>" for e in r.get("evidence") or [])
            if r["verdict"] == "agree":
                out.append(("skeptic", f"I agree. {_e(r['reason'])}"))
            else:
                out.append(("skeptic", f"I disagree: I think <b>{_e(r['suspected_service'])}</b> is the real cause. {_e(r['reason'])}<ul>{evidence}</ul>"))
        if delta.get("revision_round") == 1:
            out.append(("system", "↩️ Skeptic sent the case back to Scout for one more look."))
    elif node == "propose_action":
        a = delta.get("action")
        if a and a["action"] != "escalate_to_human":
            wait = "Waiting for your approval." if a["needs_approval"] else "Low risk, no approval needed."
            out.append(("system", f"🛠️ Proposed fix: <b>{_e(a['action'])}({_e(a['target_service'])})</b> from runbook <code>{_e(a['runbook_id'])}</code>. {_e(a['reason'])} <b>{wait}</b>"))
        if delta.get("blocked_action"):
            out.append(("system", f"🚫 Action blocked: {_e(delta['blocked_action'].get('action'))}"))
    elif node == "approval_gate":
        approval = delta.get("approval") or {}
        action = values.get("action") or {}
        if approval.get("approved"):
            out.append(("system", f"✅ SIMULATED <b>{_e(action.get('action'))}({_e(action.get('target_service'))})</b>. Written to the action log only, nothing was executed."))
        elif approval:
            out.append(("system", "↩️ Scout takes one more look with your note." if not delta.get("escalation_reason") else "❌ Rejected twice."))
    elif node == "escalate":
        out.append(("system", f"📣 Escalated to the on-call engineer: {_e(values.get('escalation_reason') or '')}"))
    elif node == "report":
        out.append(("system", f"📄 Incident report ready. Status: <b>{_e(delta.get('status'))}</b>."))
    out += [_guardrail(e) for e in delta.get("guardrail_events") or []]
    return out


def render(transcript: list[tuple[str, str]]) -> str:
    parts = []
    for who, text in transcript:
        if who in ("scout", "skeptic"):
            name, role = WHO[who]
            parts.append(
                f'<div class="msg {who}"><span class="orb {who}" aria-hidden="true"></span>'
                f'<div class="bubble"><div class="who">{name} <small>· {role}</small></div>{text}</div></div>'
            )
        elif who == "human":
            parts.append(f'<div class="msg human"><div class="bubble"><div class="who">You</div>{text}</div><span class="me">🙋</span></div>')
        elif who == "alert":
            parts.append(f'<div class="msg alert"><div class="bubble">🚨 <b>Alert</b> · {text}</div></div>')
        else:
            parts.append(f'<div class="sys">{text}</div>')
    return '<div class="chat">' + "".join(parts) + "</div>"


CHAT_CSS = """
<style>
.chat {display: flex; flex-direction: column; gap: 10px; margin: 6px 0 14px;}
.msg {display: flex; align-items: flex-start; gap: 10px; max-width: 92%;}
.msg .orb {margin-top: 6px;}
.msg.human {align-self: flex-end; flex-direction: row;}
.msg.human .me {font-size: 22px; margin-top: 4px;}
.bubble {border-radius: 14px; padding: 9px 13px; line-height: 1.45; border: 1px solid #8883; overflow-wrap: anywhere;}
.msg.scout .bubble {background: #1f6bff1c; border-color: #3fc6ff55;}
.msg.skeptic .bubble {background: #ff4f8b17; border-color: #ff4f8b55;}
.msg.human .bubble {background: #8882;}
.msg.alert {max-width: 100%; width: 100%;}
.msg.alert .bubble {background: #e8a33c22; border-color: #e8a33c66; width: 100%;}
.who {font-weight: 600; font-size: .85rem; margin-bottom: 2px;}
.who small {font-weight: 400; opacity: .7;}
.bubble ul {margin: 4px 0 2px 1.1rem; padding: 0;}
.bubble code {font-size: .8rem;}
.muted {opacity: .75; font-size: .9rem;}
.snip {margin-top: 6px;}
.snip pre {margin: 2px 0 0; padding: 6px 8px; border-radius: 8px; background: #8881; font-size: .74rem; white-space: pre-wrap;}
.tid {display: inline-block; font-family: ui-monospace, monospace; font-size: .72rem; padding: 0 6px;
      border-radius: 6px; background: #8883;}
.sys {align-self: center; text-align: center; font-size: .82rem; opacity: .85; max-width: 90%;}
</style>
"""
