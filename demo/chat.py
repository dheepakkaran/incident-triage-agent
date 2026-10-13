"""Turn graph steps into a conversation: Scout (investigator), Skeptic (reviewer), you, and the system.

`messages_for(node, delta, values)` looks at what one node just returned and says, in plain
words, what happened. `render(transcript)` turns the messages into chat bubbles (HTML).
The technical detail (raw tool output, every step) stays in the "Under the hood" tab.
"""

import html
import re

from orbs import orb_html

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


def _confidence_meter(confidence: float) -> str:
    level = "high" if confidence >= 0.8 else "mid" if confidence >= 0.6 else "low"
    return (
        f'<div class="meter {level}"><span class="bar"><span class="fill" style="--v: {confidence * 100:.0f}%"></span></span>'
        f'<span class="mlabel">confidence {confidence:.2f}</span></div>'
    )


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
                out.append(("scout", _tool_phrase(call["name"], call.get("args") or {}), "think"))
            text = getattr(reply, "content", "")
            if text and not getattr(reply, "tool_calls", None):
                out.append(("scout", _e(text), "talk"))
        if delta.get("escalation_reason"):
            out.append(("system", f"⚠️ {_e(delta['escalation_reason'])}"))
    elif node in ("run_tools", "review_tools"):
        who = "scout" if node == "run_tools" else "skeptic"
        snippets = "".join(_result_snippet(cid, r) for cid, r in (delta.get("tool_results") or {}).items())
        if snippets:
            out.append((who, f"<span class='muted'>Here is what came back:</span>{snippets}", "read"))
    elif node == "diagnose":
        d = delta.get("diagnosis")
        if d:
            top = d["candidates"][0]
            others = ", ".join(f"{_e(c['service'])} ({_e(c['fault_type'])})" for c in d["candidates"][1:])
            evidence = "".join(f"<li><span class='tid'>{_e(e['tool_call_id'])}</span> <code>{_e(e['quote'])}</code></li>" for e in d["evidence"][:3])
            out.append(
                (
                    "scout",
                    f"My diagnosis: <b>{_e(top['service'])}</b> has a <b>{_e(top['fault_type'])}</b> fault"
                    f"{f'. Also possible: {others}' if others else ''}.{_confidence_meter(top['confidence'])}"
                    f"<ul>{evidence}</ul><span class='muted'>{_e(d['summary'])}</span>",
                    "happy",
                )
            )
    elif node == "review":
        pending = delta.get("review_messages") or []
        if pending and getattr(pending[-1], "tool_calls", None):
            for call in pending[-1].tool_calls:
                out.append(("skeptic", "Let me check that myself. " + _tool_phrase(call["name"], call.get("args") or {}), "suspicious"))
        for r in delta.get("reviews") or []:
            evidence = "".join(f"<li><span class='tid'>{_e(e['tool_call_id'])}</span> <code>{_e(e['quote'])}</code></li>" for e in r.get("evidence") or [])
            if r["verdict"] == "agree":
                out.append(("skeptic", f"I agree. {_e(r['reason'])}", "happy"))
            else:
                out.append(("skeptic", f"I disagree: I think <b>{_e(r['suspected_service'])}</b> is the real cause. {_e(r['reason'])}<ul>{evidence}</ul>", "doubt"))
        if delta.get("revision_round") == 1:
            out.append(("handoff", "Skeptic sent the case back to Scout for one more look", "skeptic>scout"))
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
        elif approval and not delta.get("escalation_reason"):
            out.append(("handoff", "You sent it back: Scout takes one more look with your note", "human>scout"))
        elif approval:
            out.append(("system", "❌ Rejected twice."))
    elif node == "escalate":
        out.append(("system", f"📣 Escalated to the on-call engineer: {_e(values.get('escalation_reason') or '')}"))
    elif node == "report":
        out.append(("system", f"📄 Incident report ready. Status: <b>{_e(delta.get('status'))}</b>."))
    out += [_guardrail(e) for e in delta.get("guardrail_events") or []]
    return out


def _calls(values: dict, node: str) -> int:
    """Tool calls made in `node` steps over the whole run (the reviewer's counter resets each round)."""
    return sum(len(step.get("calls") or []) for step in values.get("steps") or [] if step["node"] == node)


def summary_card(values: dict, elapsed_s: float, mode: str) -> tuple[str, str]:
    """One card with the outcome: root cause, fix, decision, guardrails, time."""
    d, action, approval = values.get("diagnosis") or {}, values.get("action") or {}, values.get("approval") or {}
    status = values.get("status", "")
    top = (d.get("candidates") or [{}])[0]
    cause = f"<b>{_e(top['service'])}</b> · {_e(top['fault_type'])} fault" if top else "no diagnosis"
    fix = f"<b>{_e(action['action'])}({_e(action['target_service'])})</b> · <code>{_e(action['runbook_id'])}</code>" if action else "none proposed"
    if status == "escalated":
        decision = f"📣 escalated: {_e((values.get('escalation_reason') or '')[:120])}"
    elif approval.get("auto"):
        decision = _e(approval.get("note", "approved"))
    elif approval:
        decision = "approved by you" if approval.get("approved") else "rejected by you"
    else:
        decision = "no approval needed"
    rows = [
        ("🎯", "Root cause", cause),
        ("🔧", "Fix", fix),
        ("🙋", "Decision", decision),
        ("🛡️", "Guardrails fired", str(len(values.get("guardrail_events") or []))),
        ("🔎", "Tool calls", f"{_calls(values, 'run_tools')} by Scout, {_calls(values, 'review_tools')} by Skeptic"),
        ("⏱", "Time", f"{elapsed_s:.1f} s ({mode})"),
    ]
    body = "".join(f'<div class="srow"><span class="sic">{icon}</span><span class="sk">{key}</span><span class="sv">{val}</span></div>' for icon, key, val in rows)
    badge = {"diagnosed": "ok", "escalated": "warn", "rejected": "bad"}.get(status, "")
    return ("summary", f'<div class="shead">Outcome <span class="sbadge {badge}">{_e(status)}</span></div>{body}')


def last_moods(transcript: list) -> dict:
    """Each agent's latest mood, so the header faces match the conversation."""
    moods = {"scout": "", "skeptic": ""}
    for entry in transcript:
        if entry[0] in moods and len(entry) > 2:
            moods[entry[0]] = entry[2]
    return moods


def _mini(who: str) -> str:
    return orb_html(who, "", "") if who in WHO and who != "human" else '<span class="me">🙋</span>'


def render(transcript: list, typing: str | None = None, light: bool = False) -> str:
    """Chat bubbles. `typing` ('scout' or 'skeptic') adds a live "is typing" bubble at the end."""
    parts = []
    latest = {who: i for i, (who, *_rest) in enumerate(transcript)}  # each speaker's latest message
    for i, entry in enumerate(transcript):
        who, text = entry[0], entry[1]
        mood = entry[2] if len(entry) > 2 else ""
        newest = i == len(transcript) - 1 and not typing
        new = " new" if newest else ""  # only the newest message pops in
        badge = (" badge" if latest.get(who) == i else "") + (" pop" if newest else "")
        if who in ("scout", "skeptic"):
            name, role = WHO[who]
            parts.append(
                f'<div class="msg {who}{new}">{orb_html(who, mood + badge, "md")}'
                f'<div class="bubble"><div class="who">{name} <small>· {role}</small></div>{text}</div></div>'
            )
        elif who == "human":
            parts.append(f'<div class="msg human{new}"><div class="bubble"><div class="who">You</div>{text}</div><span class="me">🙋</span></div>')
        elif who == "handoff":
            source, target = (entry[2] if len(entry) > 2 else "skeptic>scout").split(">")
            parts.append(
                f'<div class="handoff{new}">{_mini(source)}<span class="flow"><i></i></span>{_mini(target)}'
                f'<span class="htext">{text}</span></div>'
            )
        elif who == "summary":
            parts.append(f'<div class="summary{new}">{text}</div>')
        elif who == "alert":
            parts.append(f'<div class="msg alert"><div class="bubble">🚨 <b>Alert</b> · {text}</div></div>')
        else:
            parts.append(f'<div class="sys{new}">{text}</div>')
    if typing in WHO:
        name, role = WHO[typing]
        parts.append(
            f'<div class="msg {typing} new">{orb_html(typing, "think", "md")}'
            f'<div class="bubble typing"><div class="who">{name} <small>· {role}</small></div>'
            '<span class="dots"><i></i><i></i><i></i></span></div></div>'
        )
    return f'<div class="chat{" light" if light else ""}">' + "".join(parts) + "</div>"


CHAT_CSS = """
<style>
.chat {display: flex; flex-direction: column; gap: 10px; margin: 6px 0 14px;}
.msg {display: flex; align-items: flex-start; gap: 10px; max-width: 92%;}
.msg .pod {margin-top: 2px;}
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
.new {animation: msg-pop .35s ease-out;}
/* confidence meter */
.meter {display: flex; align-items: center; gap: 8px; margin: 6px 0 2px;}
.meter .bar {flex: 0 0 140px; height: 7px; border-radius: 99px; background: #8883; overflow: hidden;}
.meter .fill {display: block; height: 100%; width: var(--v); border-radius: 99px;}
.new .meter .fill {animation: meter-fill .9s ease-out;}
.meter.high .fill {background: linear-gradient(90deg, #1fb978, #2fd18a);}
.meter.mid .fill {background: linear-gradient(90deg, #e3a21a, #f5c04a);}
.meter.low .fill {background: linear-gradient(90deg, #e2334f, #ff5c74);}
.meter .mlabel {font-size: .78rem; opacity: .8;}
@keyframes meter-fill {from {width: 0;}}
/* handoff: avatars joined by a flowing arrow */
.handoff {align-self: center; display: flex; align-items: center; gap: 4px; font-size: .82rem; opacity: .95;}
.handoff .pod {margin: 0; width: 26px; height: 26px; border-width: 1.5px;}
.handoff .flow {position: relative; width: 70px; height: 2px; margin: 0 2px;
                background: repeating-linear-gradient(90deg, #aab3c2 0 6px, transparent 6px 11px); background-size: 22px 2px;
                animation: flow .7s linear infinite;}
.handoff .flow i {position: absolute; right: -2px; top: -4px; border: 5px solid transparent; border-left: 7px solid #aab3c2;
                  border-right: 0;}
.handoff .htext {margin-left: 8px;}
.handoff .me {font-size: 18px;}
@keyframes flow {to {background-position: 22px 0;}}
/* outcome card */
.summary {border-radius: 16px; padding: 12px 16px; border: 1px solid #8885; background: linear-gradient(160deg, #ffffff0d, #ffffff04);
          box-shadow: 0 6px 24px #0004; margin-top: 4px;}
.shead {font-weight: 700; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;}
.sbadge {font-size: .72rem; font-weight: 700; padding: 1px 8px; border-radius: 99px; text-transform: uppercase; letter-spacing: .04em;}
.sbadge.ok {background: #2fd18a33; color: #2fd18a;} .sbadge.warn {background: #f5c04a33; color: #e3a21a;}
.sbadge.bad {background: #ff3b5c33; color: #ff3b5c;}
.srow {display: grid; grid-template-columns: 24px 130px 1fr; gap: 6px; padding: 4px 0; border-top: 1px solid #8882; font-size: .9rem;}
.srow:first-of-type {border-top: none;}
.sk {opacity: .7;}
/* light theme */
.chat.light .msg.scout .bubble {background: #eaf2ff; border-color: #b9d3ff;}
.chat.light .msg.skeptic .bubble {background: #fff0f4; border-color: #ffc4d3;}
.chat.light .msg.human .bubble {background: #f1f2f5;}
.chat.light .msg.alert .bubble {background: #fff6e5; border-color: #f3d394;}
.chat.light .pod.scout {background: radial-gradient(circle at 50% 40%, #dbe8ff, #f3f7ff 70%);}
.chat.light .pod.skeptic {background: radial-gradient(circle at 50% 40%, #ffdbe6, #fff4f7 70%);}
.chat.light .snip pre, .chat.light .tid {background: #eef0f4;}
.chat.light .summary {background: #fff; box-shadow: 0 6px 22px #1d23301a;}
.chat.light .handoff .flow {background: repeating-linear-gradient(90deg, #6b7486 0 6px, transparent 6px 11px); background-size: 22px 2px;}
.chat.light .handoff .flow i {border-left-color: #6b7486;}
.typing .dots {display: inline-flex; gap: 4px; padding: 4px 0 2px;}
.typing .dots i {width: 7px; height: 7px; border-radius: 50%; background: currentColor; opacity: .35;
                 animation: dot-bounce 1s ease-in-out infinite;}
.typing .dots i:nth-child(2) {animation-delay: .15s;} .typing .dots i:nth-child(3) {animation-delay: .3s;}
@keyframes dot-bounce {0%, 60%, 100% {transform: translateY(0); opacity: .35;} 30% {transform: translateY(-4px); opacity: 1;}}
@keyframes msg-pop {from {opacity: 0; transform: translateY(8px) scale(.97);} to {opacity: 1; transform: none;}}
@media (prefers-reduced-motion: reduce) {.new, .typing .dots i, .handoff .flow, .new .meter .fill {animation: none;}}
</style>
"""


# ---- what each bot says to YOU in its box, live -----------------------------


def _plain(markup: str, limit: int = 150) -> str:
    """Bubble HTML -> one short plain sentence for the bot's speech."""
    text = re.sub(r"</?(b|i|code|small)>", "", markup)  # inline tags: no gap
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))  # block tags: a space
    text = re.sub(r"\s+([.,!?:])", r"\1", re.sub(r"\s+", " ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _latest(transcript: list, who: str) -> tuple[str, str] | None:
    for entry in reversed(transcript):
        if entry[0] == who:
            return entry[1], (entry[2] if len(entry) > 2 else "")
    return None


def bot_status(values: dict, transcript: list, typing: str | None, pending: dict | None, reviewer_on: bool) -> dict:
    """{'scout': {mood, say, line}, 'skeptic': {...}}: each bot talks to the user about where things stand."""
    status = values.get("status")
    finished = bool(values.get("report"))
    d = values.get("diagnosis") or {}
    top = (d.get("candidates") or [{}])[0]
    reviews = values.get("reviews") or []
    scout_checks = sum(len(s.get("calls") or []) for s in values.get("steps") or [] if s["node"] == "run_tools")
    skeptic_checks = sum(len(s.get("calls") or []) for s in values.get("steps") or [] if s["node"] == "review_tools")

    # Scout
    last = _latest(transcript, "scout")
    if not values:
        scout = ("", "Hi! Pick a test case and press Run. I'll dig through the metrics, logs and traces to find what broke.")
    elif status == "rejected":
        scout = ("doubt", "That alert doesn't look valid, so I stopped before investigating.")
    elif pending:
        scout = ("talk", f"My suggestion: {pending['action']}({pending['target_service']}). It's risky, so it's your call: approve or reject below 👇")
    elif finished and status == "diagnosed":
        action = values.get("action") or {}
        did = f"{action.get('action')}({action.get('target_service')}) was simulated, nothing really ran" if action else "no action was needed"
        scout = ("happy", f"Done! It was {top.get('service')} ({top.get('fault_type')}). {did}. The full report is below.")
    elif finished:
        reason = _plain(values.get("escalation_reason") or "", 90)
        scout = ("read", f"This one needs a human: {reason}. Everything I found is in the report below.")
    elif typing == "scout" and last and last[1] == "read":
        scout = ("think", "Got it. Checking the next thing…")
    elif last and last[1] == "happy" and top:
        scout = ("happy", f"I think it's {top['service']}: a {top['fault_type']} problem. I'm {top['confidence'] * 100:.0f}% sure.")
    elif last and last[1] == "read":
        results = values.get("tool_results") or {}
        latest_tool = list(results.values())[-1]["tool"] if results else ""
        thing = {"list_services": "service map", "get_metric_changes": "metrics", "query_logs": "logs",
                 "get_trace_stats": "traces", "search_runbooks": "runbooks"}.get(latest_tool, "results")
        scout = ("read", f"Got the {thing} back. Reading them now…")
    elif last:
        verb = {"think": "I'm on it: ", "talk": "Here's what I see: "}.get(last[1], "")
        scout = (last[1] or "think", verb + _plain(last[0], 120))
    else:
        scout = ("think", "Looking at the alert…")
    scout_line = f"🔎 {scout_checks} checks · 🎯 {top.get('service', '—') if top else '—'}"

    # Skeptic
    last = _latest(transcript, "skeptic")
    if not values:
        skeptic = ("", "And I'll double-check Scout's work: is the blamed service really the cause, or just a victim?")
    elif not reviewer_on:
        skeptic = ("", "I'm switched off for this run, so Scout's diagnosis goes straight to the action step.")
    elif finished and status == "escalated" and len(reviews) >= 1 and reviews[-1]["verdict"] == "revise":
        skeptic = ("doubt", f"We still disagree: I think it's {reviews[-1]['suspected_service']}. A human should decide.")
    elif last and last[1] == "doubt" and reviews:
        skeptic = ("doubt", f"I don't buy it. I think it's {reviews[-1]['suspected_service']}: {_plain(reviews[-1]['reason'], 90)}")
    elif last and last[1] == "happy":
        skeptic = ("happy", f"I agree with Scout ✅ {_plain(reviews[-1]['reason'], 100) if reviews else ''}")
    elif typing == "skeptic" or (last and last[1] in ("suspicious", "read")):
        skeptic = ("suspicious", "Let me double-check that myself…")
    elif d:
        skeptic = ("suspicious", "Scout has a diagnosis. Let me review it…")
    else:
        skeptic = ("read", "Watching Scout work… I'll review the diagnosis when it's ready.")
    verdicts = " → ".join(r["verdict"] for r in reviews) or "no verdict yet"
    skeptic_line = f"🧐 {verdicts} · {skeptic_checks} checks"

    return {
        "scout": {"mood": scout[0], "say": scout[1], "line": scout_line},
        "skeptic": {"mood": skeptic[0], "say": skeptic[1], "line": skeptic_line},
    }
