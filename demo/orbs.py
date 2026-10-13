"""The two agents' faces: animated orbs with eyes and moods.

Scout (investigator) is a blue orb with a scanning satellite; Skeptic (reviewer) is an
amber-magenta orb with a spinning inspection ring. Both blink. A mood class changes the face:

  think      pupils scan side to side (calling a tool)
  read       pupils look down (reading a tool result)
  talk       mouth moves (explaining)
  happy      ^ ^ eyes, a smile and a small hop (diagnosis / agree)
  suspicious one eye squints, a brow goes up (the reviewer checking)
  doubt      narrowed eyes, frowning brows, a shake, a redder glow (the reviewer disagrees)
  alert      wide eyes (a guardrail fired)

`header_html` is the interactive strip at the top (its own iframe, so it may run JavaScript):
eyes follow the mouse, hovering surprises them, clicking makes a bot react and say a line.
All motion stops for users who ask for reduced motion.
"""

import json

FACE = '<i class="brow l"></i><i class="brow r"></i><i class="eye l"><i class="pupil"></i></i><i class="eye r"><i class="pupil"></i></i><i class="mouth"></i>'

ORB_CSS = """
.orb {width: 22px; height: 22px; border-radius: 50%; display: inline-block; position: relative;
      vertical-align: middle; margin-right: 8px; flex: none;}
.orb.md {width: 32px; height: 32px;}
.orb.lg {width: 46px; height: 46px; margin-right: 12px;}
.orb.xl {width: 72px; height: 72px; margin: 0;}
.orb i {position: absolute; display: block; font-style: normal;}

/* bodies */
.orb.scout {background: radial-gradient(circle at 32% 28%, #f0fdff 0%, #5ad8ff 30%, #1f6bff 68%, #0a2a73 100%);
            box-shadow: 0 0 10px 2px #3fc6ff88; animation: orb-breathe 2.8s ease-in-out infinite;}
.orb.scout::after {content: ""; position: absolute; width: 22%; height: 22%; border-radius: 50%;
                   background: #c9f6ff; box-shadow: 0 0 6px #c9f6ff; top: -12%; left: 39%;
                   transform-origin: 50% 277%; animation: orb-spin 3.2s linear infinite;}
.orb.skeptic {background: radial-gradient(circle at 36% 30%, #fff4dc 0%, #ffbe55 28%, #ff4f8b 64%, #4e0b36 100%);
              box-shadow: 0 0 10px 2px #ff4f8b77; transition: box-shadow .3s;}
.orb.skeptic::before {content: ""; position: absolute; inset: -18%; border-radius: 50%;
                      background: conic-gradient(from 0deg, transparent 0 62%, #ffd27a 80%, transparent 96%);
                      -webkit-mask: radial-gradient(circle, transparent 57%, #000 60%);
                      mask: radial-gradient(circle, transparent 57%, #000 60%);
                      animation: orb-spin 1.6s linear infinite;}

/* face */
.orb .eye {top: 34%; width: 21%; height: 27%; background: #fff; border-radius: 50%;
           box-shadow: inset 0 -1px 2px #0003; animation: orb-blink 4.7s infinite; transition: all .25s;}
.orb .eye.l {left: 25%;} .orb .eye.r {left: 54%;}
.orb.skeptic .eye {border-radius: 50% 50% 45% 45%; animation-delay: 1.3s;}
.orb .pupil {width: 56%; height: 56%; top: 22%; left: 22%; border-radius: 50%; background: #0b1530;
             transition: transform .18s ease-out;}
.orb.skeptic .pupil {background: #3a0820;}
.orb .mouth {left: 40%; top: 68%; width: 20%; height: 7%; border-radius: 0 0 8px 8px; background: #0b1530;
             opacity: 0; transition: all .25s;}
.orb.skeptic .mouth {background: #3a0820;}
.orb .brow {top: 23%; width: 24%; height: 6%; border-radius: 3px; background: #3a0820; opacity: 0; transition: all .25s;}
.orb .brow.l {left: 23%;} .orb .brow.r {left: 53%;}

/* moods */
.orb.think .pupil {animation: orb-scan 1.3s ease-in-out infinite alternate;}
.orb.read .pupil {transform: translateY(38%);}
.orb.read .eye {height: 22%; top: 38%;}
.orb.talk .mouth {opacity: 1; animation: orb-talk .28s ease-in-out 8 alternate;}
.orb.happy {animation: orb-hop .55s ease 2;}
.orb.happy .eye {height: 12%; top: 40%; background: transparent; border-radius: 50% 50% 0 0;
                 border-top: 2px solid #fff; box-shadow: none; animation: none;}
.orb.happy .pupil {display: none;}
.orb.happy .mouth {opacity: 1; height: 12%; width: 26%; left: 37%; top: 63%; border-radius: 0 0 12px 12px;}
.orb.suspicious .eye.l {height: 9%; top: 43%; animation: none;}
.orb.suspicious .brow.r {opacity: 1; transform: rotate(-14deg) translateY(-40%);}
.orb.suspicious .pupil {animation: orb-scan 1.6s ease-in-out infinite alternate;}
.orb.doubt {animation: orb-shake .45s ease 2; box-shadow: 0 0 14px 4px #ff2d55aa;}
.orb.doubt .eye {height: 11%; top: 42%; animation: none;}
.orb.doubt .brow {opacity: 1;}
.orb.doubt .brow.l {transform: rotate(16deg);} .orb.doubt .brow.r {transform: rotate(-16deg);}
.orb.doubt .mouth {opacity: 1; height: 5%; top: 70%; border-radius: 3px; transform: rotate(-8deg);}
.orb.alert .eye {transform: scale(1.25);}
.orb.surprised .eye {transform: scale(1.3);}
.orb.surprised .mouth {opacity: 1; width: 12%; height: 12%; left: 44%; border-radius: 50%;}

@keyframes orb-breathe {0%, 100% {box-shadow: 0 0 8px 1px #3fc6ff66; transform: scale(1);}
                        50% {box-shadow: 0 0 16px 4px #3fc6ffaa; transform: scale(1.06);}}
@keyframes orb-spin {to {transform: rotate(360deg);}}
@keyframes orb-blink {0%, 93%, 100% {transform: scaleY(1);} 96% {transform: scaleY(.08);}}
@keyframes orb-scan {from {transform: translateX(-40%);} to {transform: translateX(40%);}}
@keyframes orb-talk {from {height: 4%;} to {height: 13%;}}
@keyframes orb-hop {0%, 100% {transform: translateY(0);} 40% {transform: translateY(-22%);}}
@keyframes orb-shake {0%, 100% {transform: translateX(0);} 25% {transform: translateX(-10%);} 75% {transform: translateX(10%);}}
@media (prefers-reduced-motion: reduce) {
  .orb, .orb *, .orb::before, .orb::after {animation: none !important; transition: none !important;}
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
.orb.jump {{animation: orb-hop .5s ease 1 !important;}}
@media (max-width: 640px) {{.row {{flex-direction: column;}}}}
</style></head><body><div class="row">{bots}</div>
<script>
const LINES = {json.dumps(LINES)};
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const orbs = [...document.querySelectorAll(".orb")];

function look(x, y) {{  // point every pupil at (x, y), in this frame's coordinates
  for (const o of orbs) {{
    if (o.classList.contains("happy")) continue;
    const r = o.getBoundingClientRect(), dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 160);
    for (const p of o.querySelectorAll(".pupil")) p.style.transform = `translate(${{dx / d * k * 42}}%, ${{dy / d * k * 42}}%)`;
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
