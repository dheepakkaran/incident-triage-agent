"""The two agents' faces, in the style of a code-built character icon (rounded-square body,
rounded-square eyes, a body that morphs smoothly between states). Own colors and personalities:
Scout (investigator) is blue, Skeptic (reviewer) is amber-magenta. Nothing rotates.

States (wait -> work -> respond) and expressions, set by a mood class:

  (none)     wait: floats gently, blinks
  think      work: body wobbles into a squircle, eyes narrow and scan (calling a tool)
  read       work: eyes look down (reading a tool result)
  talk       respond: body rounds out and pulses (explaining)
  happy      eyes curve up into smiling arcs, a small hop (diagnosis / agree)
  suspicious one eye squints, body tilts (the reviewer checking)
  doubt      eyes slant inward, body sharpens, a shake (the reviewer disagrees)
  surprised  eyes grow and go round (hover)

`header_html` is the interactive strip at the top (its own iframe, so it may run JavaScript):
eyes follow the mouse, hovering surprises them, clicking makes a bot react and say a line.
All motion stops for users who ask for reduced motion.
"""

import json

FACE = '<i class="eyes"><i class="eye l"></i><i class="eye r"></i></i>'

ORB_CSS = """
.orb {--lw: 2px; width: 22px; height: 22px; display: inline-block; position: relative; vertical-align: middle;
      margin-right: 8px; flex: none; border-radius: 34%;
      transition: border-radius .5s ease, transform .5s ease, box-shadow .4s ease;
      animation: bot-float 3.4s ease-in-out infinite;}
.orb.md {width: 32px; height: 32px;}
.orb.lg {width: 46px; height: 46px; margin-right: 12px; --lw: 2.5px;}
.orb.xl {width: 74px; height: 74px; margin: 0; --lw: 3.5px;}
.orb i {position: absolute; display: block; font-style: normal;}

/* bodies: soft gradient, gentle glow, no moving lights */
.orb.scout {background: linear-gradient(150deg, #8fe3ff 0%, #2f86ff 48%, #1440b8 100%);
            box-shadow: 0 3px 12px #2f86ff55, inset 0 -3px 6px #0a2a7355, inset 0 2px 4px #ffffff55;}
.orb.skeptic {background: linear-gradient(150deg, #ffd88a 0%, #ff6b8f 52%, #9a1f57 100%);
              box-shadow: 0 3px 12px #ff5c8a55, inset 0 -3px 6px #5a0f3c55, inset 0 2px 4px #ffffff55;
              animation-delay: -1.7s;}

/* rounded-square eyes */
.orb .eyes {inset: 0; transition: transform .2s ease-out;}
.orb .eye {top: 34%; width: 19%; height: 29%; background: #fff; border-radius: 32%;
           box-shadow: 0 0 4px #ffffffaa; animation: bot-blink 5s infinite; transition: all .35s ease;}
.orb .eye.l {left: 25%;} .orb .eye.r {left: 56%;}
.orb.skeptic .eye {width: 21%; height: 24%; top: 37%; border-radius: 28%; animation-delay: 1.9s;}

/* work */
.orb.think {animation: bot-morph 1.4s ease-in-out infinite alternate;}
.orb.think .eye {height: 15%; top: 42%;}
.orb.think .eyes {animation: bot-scan 1.2s ease-in-out infinite alternate;}
.orb.read .eye {height: 17%; top: 45%;}
.orb.read .eyes {transform: translateY(10%);}
/* respond */
.orb.talk {border-radius: 46%; animation: bot-pulse .6s ease-in-out 6 alternate;}
/* expressions */
.orb.happy {border-radius: 44%; animation: bot-hop .55s ease 2;}
.orb.happy .eye {height: 15%; top: 38%; border-radius: 50% 50% 18% 18% / 100% 100% 14% 14%; animation: none;}
.orb.suspicious {transform: rotate(-7deg);}
.orb.suspicious .eye.l {height: 9%; top: 45%; animation: none;}
.orb.suspicious .eyes {animation: bot-scan 1.6s ease-in-out infinite alternate;}
.orb.doubt {border-radius: 22%; animation: bot-shake .45s ease 2;}
.orb.doubt .eye {height: 13%; top: 44%; animation: none;}
.orb.doubt .eye.l {transform: rotate(16deg);} .orb.doubt .eye.r {transform: rotate(-16deg);}
.orb.surprised .eye, .orb.alert .eye {height: 32%; width: 22%; top: 30%; border-radius: 50%;}

/* emote badge: pops above the head for a moment (::after) */
.orb::after {position: absolute; right: -22%; top: -30%; min-width: 46%; height: 46%; padding: 0 8%;
             border-radius: 99px; background: #fff; color: #10131a; font: 700 calc(var(--lw) * 6)/1 system-ui, sans-serif;
             display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px #0006;
             content: ""; opacity: 0; transform: scale(.4); transition: opacity .3s, transform .3s;}
.orb.think::after {content: "…";}
.orb.suspicious::after {content: "?";}
.orb.doubt::after, .orb.surprised::after {content: "!"; background: #ff3b5c; color: #fff;}
.orb.skeptic.happy::after {content: "✓"; background: #2fd18a; color: #fff;}
.orb.scout.happy::after {content: "✦"; background: #ffd25a;}
/* "badge": shown (each agent's latest message, the header). "pop": shown with a bounce, once (newest message). */
.orb.badge.think::after, .orb.badge.suspicious::after, .orb.badge.doubt::after, .orb.badge.surprised::after,
.orb.badge.happy::after, .orb.surprised::after {opacity: 1; transform: scale(1);}
.orb.pop.badge::after {animation: bot-badge .5s ease;}
/* cheek glow when happy (::before) */
.orb::before {content: ""; position: absolute; left: 14%; right: 14%; top: 60%; height: 14%; opacity: 0;
              background: radial-gradient(closest-side, #ff8fb8cc, transparent) left / 34% 100% no-repeat,
                          radial-gradient(closest-side, #ff8fb8cc, transparent) right / 34% 100% no-repeat;
              transition: opacity .4s;}
.orb.happy::before {opacity: 1;}
/* squash and stretch whenever the newest message's bot changes state */
.new .orb {animation: bot-squash .55s cubic-bezier(.3, 1.6, .5, 1) 1, bot-float 3.4s ease-in-out .55s infinite;}

@keyframes bot-badge {0% {opacity: 0; transform: scale(.4) translateY(30%);} 60% {opacity: 1; transform: scale(1.15);}
                      100% {opacity: 1; transform: scale(1);}}
@keyframes bot-squash {0% {transform: scale(1, 1);} 30% {transform: scale(1.18, .82);} 60% {transform: scale(.92, 1.1);} 100% {transform: scale(1, 1);}}
@keyframes bot-float {0%, 100% {transform: translateY(0);} 50% {transform: translateY(-7%);}}
@keyframes bot-blink {0%, 94%, 100% {transform: scaleY(1);} 97% {transform: scaleY(.1);}}
@keyframes bot-morph {from {border-radius: 34%;} to {border-radius: 46% 26% 44% 30% / 30% 44% 28% 46%;}}
@keyframes bot-scan {from {transform: translateX(-9%);} to {transform: translateX(9%);}}
@keyframes bot-pulse {from {transform: scale(1);} to {transform: scale(1.07);}}
@keyframes bot-hop {0%, 100% {transform: translateY(0);} 40% {transform: translateY(-24%);}}
@keyframes bot-shake {0%, 100% {transform: translateX(0);} 25% {transform: translateX(-10%);} 75% {transform: translateX(10%);}}
@media (prefers-reduced-motion: reduce) {
  .orb, .orb * {animation: none !important; transition: none !important;}
}
"""


def orb_html(agent: str, mood: str = "", size: str = "") -> str:
    return f'<span class="orb {agent} {mood} {size}" aria-hidden="true">{FACE}</span>'


LINES = {
    "scout": ["On it! Checking the metrics 🔎", "Following the call graph…", "Evidence first, then the verdict.", "Every claim gets a tool_call_id."],
    "skeptic": ["Prove it. 🤨", "Cause… or just a victim?", "Callers are usually victims.", "Show me the quote."],
}


def header_html(scout_mood: str, skeptic_mood: str, dark: bool) -> str:
    """Interactive 'meet the agents' strip. Rendered with st.components.v1.html (an iframe)."""
    text, muted, card = ("#e6e9ef", "#9aa4b2", "#ffffff10") if dark else ("#1d2330", "#5b6577", "#0000000a")
    bots = ""
    for agent, name, role, blurb, mood in (
        ("scout", "Scout", "investigator", "Calls the 5 read-only tools, follows the call graph, writes the diagnosis with quoted evidence.", scout_mood),
        ("skeptic", "Skeptic", "reviewer", "Tries to prove Scout wrong: is the blamed service the cause, or a victim of a dependency?", skeptic_mood),
    ):
        bots += f"""
        <div class="bot" data-agent="{agent}">
          <div class="face">{orb_html(agent, mood + " badge pop", "xl")}<div class="say"></div></div>
          <div class="txt"><b>{name}</b> · {role}<br><small>{blurb}</small><br><span class="hint">click me</span></div>
        </div>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{ORB_CSS}
html, body {{margin: 0; background: transparent; font-family: "Source Sans Pro", system-ui, sans-serif; color: {text};}}
.row {{display: flex; gap: 14px; padding: 30px 2px 4px;}}
.bot {{flex: 1; display: flex; align-items: center; gap: 16px; padding: 12px 16px; border-radius: 16px;
       background: {card}; border: 1px solid #8884; cursor: pointer; user-select: none;}}
.face {{position: relative;}}
.txt small {{color: {muted};}}
.hint {{font-size: 11px; color: {muted}; opacity: .7;}}
.say {{position: absolute; left: 50%; bottom: 110%; transform: translateX(-20%) scale(.6); opacity: 0; white-space: nowrap;
       background: {text}; color: {"#0e1117" if dark else "#fff"}; font-size: 12px; padding: 4px 9px; border-radius: 10px;
       transition: all .2s; pointer-events: none;}}
.say.on {{opacity: 1; transform: translateX(-20%) scale(1);}}
.orb.jump {{animation: bot-hop .5s ease 1 !important;}}
@media (max-width: 640px) {{.row {{flex-direction: column;}}}}
</style></head><body><div class="row">{bots}</div>
<script>
const LINES = {json.dumps(LINES)};
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const orbs = [...document.querySelectorAll(".orb")];

function look(x, y) {{  // turn every pair of eyes toward (x, y), in this frame coordinates
  for (const o of orbs) {{
    const r = o.getBoundingClientRect(), dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 160);
    o.querySelector(".eyes").style.transform = `translate(${{dx / d * k * 12}}%, ${{dy / d * k * 10}}%)`;
  }}
}}
document.addEventListener("mousemove", e => look(e.clientX, e.clientY));
try {{  // also follow the mouse over the rest of the page (same-origin frame)
  const frame = window.frameElement;
  window.parent.document.addEventListener("mousemove", e => {{
    const r = frame.getBoundingClientRect();
    look(e.clientX - r.left, e.clientY - r.top);
  }});
}} catch (err) {{}}

for (const bot of document.querySelectorAll(".bot")) {{
  const o = bot.querySelector(".orb"), say = bot.querySelector(".say"), agent = bot.dataset.agent;
  let i = 0, timer;
  bot.addEventListener("mouseenter", () => o.classList.add("surprised"));
  bot.addEventListener("mouseleave", () => o.classList.remove("surprised"));
  bot.addEventListener("click", () => {{
    o.classList.remove("pop"); void o.offsetWidth; o.classList.add("pop");
    say.textContent = LINES[agent][i++ % LINES[agent].length];
    say.classList.add("on");
    if (!reduce) {{ o.classList.remove("jump"); void o.offsetWidth; o.classList.add("jump"); }}
    clearTimeout(timer); timer = setTimeout(() => say.classList.remove("on"), 1800);
  }});
}}
</script></body></html>"""
