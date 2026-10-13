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

`boxes_html` draws the two bots at the top, facing the user, each saying where things stand;
`bot_script` makes their eyes follow the mouse and lets you click them for a quick line.
All motion stops for users who ask for reduced motion.
"""

import json

FACE = '<i class="eyes"><i class="eye l"></i><i class="eye r"></i></i>'

ORB_CSS = """
/* the round space around each bot; its ring color shows the state (nothing rotates) */
.pod {--ring: #8888; width: 32px; height: 32px; border-radius: 50%; display: inline-flex; align-items: center;
      justify-content: center; position: relative; vertical-align: middle; margin-right: 8px; flex: none;
      border: 2px solid var(--ring); transition: border-color .4s, box-shadow .4s;}
.pod.md {width: 46px; height: 46px;}
.pod.lg {width: 64px; height: 64px; margin-right: 12px;}
.pod.xl {width: 104px; height: 104px; margin: 0; border-width: 3px;}
.pod.scout {--ring: #3f8cff66; background: radial-gradient(circle at 50% 40%, #1d3f8a55, #0b183a33 70%);}
.pod.skeptic {--ring: #ff5c8a66; background: radial-gradient(circle at 50% 40%, #7a1d4855, #2a0a1a33 70%);}
.pod.think, .pod.read, .pod.suspicious, .pod.puzzled {animation: pod-pulse 1.4s ease-in-out infinite;}
.pod.scout.think, .pod.scout.read {--ring: #4aa3ff;} .pod.skeptic.think, .pod.skeptic.read, .pod.skeptic.suspicious {--ring: #ff7aa0;}
.pod.talk {--ring: #ffffffcc;}
.pod.puzzled {--ring: #f5c04a;}
.pod.happy {--ring: #2fd18a; box-shadow: 0 0 12px #2fd18a66;}
.pod.doubt {--ring: #ff3b5c; box-shadow: 0 0 12px #ff3b5c77;}
@keyframes pod-pulse {0%, 100% {box-shadow: 0 0 0 0 transparent;} 50% {box-shadow: 0 0 0 4px #ffffff14, 0 0 10px var(--ring);}}

.orb {--lw: 2px; width: 22px; height: 22px; display: inline-block; position: relative; vertical-align: middle;
      flex: none; border-radius: 34%;
      transition: border-radius .5s ease, transform .5s ease, box-shadow .4s ease;
      animation: bot-float 3.4s ease-in-out infinite;}
.orb {width: 66%; height: 66%;}
.orb.md {--lw: 2px;}
.orb.lg {--lw: 2.5px;}
.orb.xl {--lw: 3.5px;}
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
.orb.puzzled .eye.r {height: 34%; top: 29%;}
.orb.scout.puzzled .eyes {transform: translateX(16%);}   /* Skeptic sits to Scout's right */
.orb.skeptic.puzzled .eyes {transform: translateX(-16%);} /* Scout sits to Skeptic's left */
.orb.jump {animation: bot-hop .5s ease 1 !important;}
.orb.surprised .eye, .orb.alert .eye {height: 32%; width: 22%; top: 30%; border-radius: 50%;}

/* emote badge: pops above the head for a moment (::after) */
.orb::after {position: absolute; right: -22%; top: -30%; min-width: 46%; height: 46%; padding: 0 8%;
             border-radius: 99px; background: #fff; color: #10131a; font: 700 calc(var(--lw) * 6)/1 system-ui, sans-serif;
             display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px #0006;
             content: ""; opacity: 0; transform: scale(.4); transition: opacity .3s, transform .3s;}
.orb.think::after {content: "…";}
.orb.suspicious::after, .orb.puzzled::after {content: "?";}
.orb.doubt::after, .orb.surprised::after {content: "!"; background: #ff3b5c; color: #fff;}
.orb.skeptic.happy::after {content: "✓"; background: #2fd18a; color: #fff;}
.orb.scout.happy::after {content: "✦"; background: #ffd25a;}
/* "badge": shown (each agent's latest message, the header). "pop": shown with a bounce, once (newest message). */
.orb.badge.think::after, .orb.badge.suspicious::after, .orb.badge.doubt::after, .orb.badge.surprised::after,
.orb.badge.happy::after, .orb.badge.puzzled::after, .orb.surprised::after {opacity: 1; transform: scale(1);}
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
  .pod, .orb, .orb * {animation: none !important; transition: none !important;}
}
"""


def team_moods(scout: str, skeptic: str) -> tuple[str, str]:
    """The two bots react to each other in the header.

    Skeptic disagrees  -> Scout turns to look at Skeptic, puzzled ("?").
    Skeptic agrees     -> both are happy: a team win.
    Scout has a diagnosis Skeptic has not reviewed yet -> Skeptic eyes it, suspicious.
    """
    if skeptic == "doubt":
        return "puzzled", "doubt"
    if skeptic == "happy":
        return "happy", "happy"
    if scout == "happy" and not skeptic:
        return "happy", "suspicious"
    return scout, skeptic


def orb_html(agent: str, mood: str = "", size: str = "") -> str:
    """The bot inside its round space. Mood classes go on both: the ring shows the state, the bot the face."""
    return f'<span class="pod {agent} {mood} {size}" aria-hidden="true"><span class="orb {agent} {mood} {size}">{FACE}</span></span>'


LINES = {
    "scout": ["On it! Checking the metrics 🔎", "Following the call graph…", "Evidence first, then the verdict.", "Every claim gets a tool_call_id."],
    "skeptic": ["Prove it. 🤨", "Cause… or just a victim?", "Callers are usually victims.", "Show me the quote."],
}


BOX_CSS = """
.botboxes {display: flex; gap: 14px; margin: 0 0 4px; align-items: stretch;}
.botbox {flex: 1; display: flex; gap: 16px; align-items: center; padding: 16px 18px; border-radius: 18px;
         border: 1px solid #8884; background: linear-gradient(160deg, #ffffff0c, #ffffff03); cursor: pointer;
         user-select: none; transition: border-color .4s, box-shadow .4s;}
.botbox.scout.active {border-color: #3f8cff99; box-shadow: 0 0 0 1px #3f8cff33, 0 8px 28px #1f6bff22;}
.botbox.skeptic.active {border-color: #ff5c8a99; box-shadow: 0 0 0 1px #ff5c8a33, 0 8px 28px #ff4f8b22;}
.botbox .pod {margin: 0;}
.botbox .body {flex: 1; min-width: 0;}
.botbox .name {font-weight: 700; font-size: 1rem;} .botbox .role {opacity: .6; font-weight: 400; font-size: .85rem;}
.botbox .say {position: relative; margin: 8px 0 8px; padding: 10px 13px; border-radius: 4px 14px 14px 14px;
              background: #ffffff10; border: 1px solid #8883; line-height: 1.45; min-height: 4.4em; font-size: .92rem;}
.botbox.scout .say {background: #1f6bff1c; border-color: #3fc6ff44;}
.botbox.skeptic .say {background: #ff4f8b17; border-color: #ff4f8b44;}
.botbox .say.fresh {animation: say-in .35s ease-out;}
.botbox .line {display: flex; flex-wrap: wrap; gap: 6px;}
.botbox .chip {font-size: .72rem; font-weight: 500; padding: 2px 9px; border-radius: 99px; border: 1px solid #8b95a744;
               color: #8b95a7; background: #8b95a70d;}
.botbox:hover .orb .eye {height: 32%; width: 22%; top: 30%; border-radius: 50%;}
.botbox.light {background: #fff;}
.botbox.light.scout .say {background: #eaf2ff; border-color: #b9d3ff;}
.botbox.light.skeptic .say {background: #fff0f4; border-color: #ffc4d3;}
.botbox.light .pod.scout {background: radial-gradient(circle at 50% 40%, #dbe8ff, #f3f7ff 70%);}
.botbox.light .pod.skeptic {background: radial-gradient(circle at 50% 40%, #ffdbe6, #fff4f7 70%);}
@keyframes say-in {from {opacity: 0; transform: translateY(4px);} to {opacity: 1; transform: none;}}
@media (max-width: 720px) {.botboxes {flex-direction: column;}}
@media (prefers-reduced-motion: reduce) {.botbox .say.fresh {animation: none;}}
"""


def boxes_html(status: dict, active: str | None, light: bool, fresh: set[str]) -> str:
    """The two bots, facing the user, each with what it is telling the user right now.

    `status` comes from chat.bot_status; `active` is the bot working right now; `fresh`
    names the bots whose speech just changed (they get a small entrance animation).
    """
    import html

    cards = []
    for agent, name, role in (("scout", "Scout", "investigator"), ("skeptic", "Skeptic", "reviewer")):
        st = status[agent]
        classes = f"botbox {agent}{' active' if active == agent else ''}{' light' if light else ''}"
        chips = "".join(f'<span class="chip">{html.escape(part)}</span>' for part in st["line"].split(" · "))
        cards.append(
            f'<div class="{classes}" data-agent="{agent}">{orb_html(agent, st["mood"] + " badge", "xl")}'
            f'<div class="body"><div><span class="name">{name}</span> <span class="role">· {role}</span></div>'
            f'<div class="say{" fresh" if agent in fresh else ""}">{html.escape(st["say"])}</div>'
            f'<div class="line">{chips}</div></div></div>'
        )
    return '<div class="botboxes">' + "".join(cards) + "</div>"


def bot_script() -> str:
    """Our own static script (no app data inside): eyes follow the mouse, clicking a bot makes it
    hop and say a quick line. Wired once per page; it finds the boxes again after every update."""
    return """<script>
if (!window.__triageBots) {
  window.__triageBots = true;
  const LINES = __LINES__;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const doc = window.parent && window.parent.document ? window.parent.document : document;
  doc.addEventListener("mousemove", e => {
    for (const o of doc.querySelectorAll(".botbox .orb")) {
      if (o.classList.contains("puzzled")) continue;  // it is looking at its teammate
      const r = o.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
      const d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 220);
      o.querySelector(".eyes").style.transform = `translate(${dx / d * k * 12}%, ${dy / d * k * 10}%)`;
    }
  });
  const count = {scout: 0, skeptic: 0};
  doc.addEventListener("click", e => {
    const box = e.target.closest && e.target.closest(".botbox");
    if (!box) return;
    const agent = box.dataset.agent, o = box.querySelector(".orb"), say = box.querySelector(".say");
    const before = say.textContent;
    say.textContent = LINES[agent][count[agent]++ % LINES[agent].length];
    if (!reduce) { o.classList.remove("jump"); void o.offsetWidth; o.classList.add("jump"); }
    setTimeout(() => { if (say.isConnected) say.textContent = before; o.classList.remove("jump"); }, 1800);
  });
}
</script>""".replace("__LINES__", json.dumps(LINES))
