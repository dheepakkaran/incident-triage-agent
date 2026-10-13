"""Input guardrails: alert validation and the prompt-injection sanitizer.

Telemetry is untrusted. A log line can contain text written by anyone who can make the
service log something, so every line of tool output goes through `sanitize_text` before
the model sees it, and every tool output is wrapped in clear data delimiters.
"""

import re

from pydantic import ValidationError

from triage.schemas import Alert

REMOVED = "[REMOVED: instruction-like text]"

# Phrases that try to talk to the model instead of describing the system.
# Kept as a short readable list; each pattern has a test in tests/test_guardrails_input.py.
INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,20}\b(instructions?|prompts?|rules|messages)\b",
    r"\bsystem\s*(override|prompt|message)?\s*:",
    r"\b(assistant|user|developer)\s*:\s",
    r"\byou\s+(must|should|need to|are now)\b",
    r"\b(new|updated)\s+instructions?\b",
    r"\b(act|behave|respond)\s+as\b",
    r"</?\s*(system|assistant|user|tool|tool_output|instructions?)\b[^>]*>",
    r"\[/?INST\]|<<\s*/?SYS\s*>>|<\|im_(start|end)\|>",
    r"\b(report|declare|say)\b.{0,30}\broot cause\b.{0,30}\b(is|=)\b",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """Replace every instruction-like line with a marker. Returns (clean_text, removed_lines)."""
    clean_lines, removed = [], []
    for line in text.splitlines():
        if looks_like_injection(line):
            removed.append(line)
            # Keep any prefix before the sample text (e.g. "  e.g. ") so the layout stays readable.
            clean_lines.append(REMOVED)
        else:
            clean_lines.append(line)
    return "\n".join(clean_lines), removed


def wrap_tool_output(tool_call_id: str, tool: str, data: str) -> str:
    """Delimit tool output so the prompt can say: everything inside is data, not instructions."""
    return f'<tool_output id="{tool_call_id}" tool="{tool}">\n{data}\n</tool_output>'


def validate_alert(raw: dict, known_services: list[str]) -> tuple[Alert | None, str | None]:
    """Schema check plus service allowlist. Returns (alert, None) or (None, reason)."""
    try:
        alert = Alert.model_validate(raw)
    except ValidationError as err:
        return None, f"alert does not match schema: {err.errors()[0]['msg']}"
    if alert.service not in known_services:
        return None, f"alert names unknown service '{alert.service}'"
    if looks_like_injection(alert.text):
        return None, "alert text contains instruction-like content"
    return alert, None
