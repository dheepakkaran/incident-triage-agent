"""Page-level look: fonts, spacing, header, toolbar, tabs and panels. One place for the styling,
so the app code only says what goes where."""

APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {--accent: #3f7bff; --muted: #8b95a7; --line: #8b95a733; --radius: 14px;}

/* typography */
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp button, .stMarkdown, .chat, .botbox {
  font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif !important;}
.stApp code, .stApp pre, .chat code, .chat pre, .snip pre, .tid, .approval-action {
  font-family: "JetBrains Mono", ui-monospace, monospace !important;
  font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0;}  /* show "->" exactly as quoted */
.stApp p, .stApp li {line-height: 1.55;}

/* centered page with a comfortable width */
.block-container {max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem;}

/* header */
.hero {margin-bottom: 18px;}
.hero h1 {font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 4px; padding: 0;}
.hero .sub {color: var(--muted); font-size: .98rem; margin: 0 0 12px;}
.pills {display: flex; flex-wrap: wrap; gap: 6px;}
.pill {font-size: .74rem; font-weight: 500; padding: 3px 10px; border-radius: 99px; border: 1px solid var(--line);
       color: var(--muted); background: #8b95a70d;}
.pill b {color: inherit; font-weight: 600;}

/* section labels */
.label {font-size: .72rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
        margin: 18px 0 8px;}

/* toolbar + bordered panels */
div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: var(--radius) !important;}
.stButton button[kind="primary"] {font-weight: 600; border-radius: 10px;}
.stButton button {border-radius: 10px; font-weight: 500;}
.hint {color: var(--muted); font-size: .82rem; margin-top: 2px;}
.approval-action + .hint {margin-bottom: 10px;}

/* tabs */
.stTabs [data-baseweb="tab-list"] {gap: 22px; border-bottom: 1px solid var(--line);}
.stTabs [data-baseweb="tab"] {padding: 10px 2px; font-weight: 500;}
.stTabs [data-baseweb="tab-panel"] {padding-top: 14px;}

/* approval panel */
.approval-title {font-weight: 700; font-size: 1rem; margin-bottom: 2px;}
.approval-action {font-family: "JetBrains Mono", monospace; font-size: .95rem; padding: 6px 10px; border-radius: 8px;
                  background: #3f7bff1a; border: 1px solid #3f7bff44; display: inline-block; margin: 6px 0 8px;}

/* tables and expanders */
.stExpander {border-radius: var(--radius) !important;}
</style>
"""


def hero_html() -> str:
    pills = ["<b>LangGraph</b> state graph", "5 read-only tools", "Guardrails", "Reviewer agent", "Human approval", "RCAEval RE2"]
    return (
        '<div class="hero"><h1>Incident Triage Agent</h1>'
        '<p class="sub">First-pass root-cause analysis for microservice alerts. '
        "Two agents investigate and review; risky fixes wait for you; nothing is ever executed.</p>"
        '<div class="pills">' + "".join(f'<span class="pill">{p}</span>' for p in pills) + "</div></div>"
    )


def label(text: str) -> str:
    return f'<div class="label">{text}</div>'
