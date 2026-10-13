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

FACE = '<i class="eyes"><i class="eye l"></i><i class="eye r"></i></i><i class="mouth"></i>'

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
.orb .eye {top: 27%; width: 19%; height: 29%; background: #fff; border-radius: 32%;
           box-shadow: 0 0 4px #ffffffaa; animation: bot-blink 5s infinite; transition: all .35s ease;}
.orb .eye.l {left: 25%;} .orb .eye.r {left: 56%;}
.orb.skeptic .eye {width: 21%; height: 24%; top: 30%; border-radius: 28%; animation-delay: 1.9s;}

/* mouth: a short rounded line by default, reshaped by each mood */
.orb .mouth {left: 39%; top: 66%; width: 22%; height: var(--lw); background: #fff; border-radius: 99px;
             box-shadow: 0 0 3px #ffffff88; transition: all .35s ease;}

/* work */
.orb.think {animation: bot-morph 1.4s ease-in-out infinite alternate;}
.orb.think .eye {height: 15%; top: 35%;}
.orb.think .eyes {animation: bot-scan 1.2s ease-in-out infinite alternate;}
.orb.read .eye {height: 17%; top: 38%;}
.orb.read .eyes {transform: translateY(10%);}
/* respond */
.orb.talk {border-radius: 46%; animation: bot-pulse .6s ease-in-out 6 alternate;}
/* expressions */
.orb.happy {border-radius: 44%; animation: bot-hop .55s ease 2;}
.orb.happy .eye {height: 15%; top: 31%; border-radius: 50% 50% 18% 18% / 100% 100% 14% 14%; animation: none;}
.orb.suspicious {transform: rotate(-7deg);}
.orb.suspicious .eye.l {height: 9%; top: 38%; animation: none;}
.orb.suspicious .eyes {animation: bot-scan 1.6s ease-in-out infinite alternate;}
.orb.doubt {border-radius: 22%; animation: bot-shake .45s ease 2;}
.orb.doubt .eye {height: 13%; top: 37%; animation: none;}
.orb.doubt .eye.l {transform: rotate(16deg);} .orb.doubt .eye.r {transform: rotate(-16deg);}
.orb.surprised .eye, .orb.alert .eye {height: 30%; width: 22%; top: 23%; border-radius: 50%;}

/* mouth per mood */
.orb.think .mouth {width: 12%; left: 50%; top: 67%;}
.orb.read .mouth {width: 16%; left: 42%;}
.orb.talk .mouth {width: 20%; left: 40%; height: 12%; background: #ffffffdd; border-radius: 30% 30% 45% 45%;
                  animation: bot-talk .22s ease-in-out infinite alternate;}
.orb.happy .mouth {width: 36%; left: 32%; top: 56%; height: 16%; background: transparent; box-shadow: none;
                   border: var(--lw) solid #fff; border-top-color: transparent; border-left-color: transparent;
                   border-right-color: transparent; border-radius: 0 0 50% 50% / 0 0 100% 100%;}
.orb.suspicious .mouth {width: 20%; left: 44%; top: 68%; transform: rotate(-14deg);}
.orb.doubt .mouth {width: 30%; left: 35%; top: 66%; height: 13%; background: transparent; box-shadow: none;
                   border: var(--lw) solid #fff; border-bottom-color: transparent; border-left-color: transparent;
                   border-right-color: transparent; border-radius: 50% 50% 0 0 / 100% 100% 0 0;}
.orb.surprised .mouth, .orb.alert .mouth {width: 16%; height: 18%; left: 42%; top: 62%; background: #0b1530aa;
                   border: var(--lw) solid #fff; border-radius: 50%;}

@keyframes bot-talk {from {height: 5%; top: 68%;} to {height: 14%; top: 64%;}}
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
          <div class="face">{orb_html(agent, mood, "xl")}<div class="say"></div></div>
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
    say.textContent = LINES[agent][i++ % LINES[agent].length];
    say.classList.add("on");
    if (!reduce) {{ o.classList.remove("jump"); void o.offsetWidth; o.classList.add("jump"); }}
    clearTimeout(timer); timer = setTimeout(() => say.classList.remove("on"), 1800);
  }});
}}
</script></body></html>"""
