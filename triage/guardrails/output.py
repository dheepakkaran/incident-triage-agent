"""Output guardrails: schema parsing, evidence verification, service and confidence checks.

The evidence check is the main defence against a made-up root cause: every claim must
point at a tool call that really happened, and the quote must really be in that output.
"""

import difflib
import json
import re

from pydantic import BaseModel, ValidationError

from triage.config import FAULT_TYPES

MIN_QUOTE_CHARS = 6


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a reply (models often wrap it in ``` fences or prose)."""
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    start = text.find("{")
    while start != -1:
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


def parse_structured(text: str, schema: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    data = extract_json(text)
    if data is None:
        return None, "reply was not a JSON object"
    try:
        return schema.model_validate(data), None
    except ValidationError as err:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:4])
        return None, f"JSON does not match the schema: {problems}"


def normalize(text: str) -> str:
    """Lowercase, unify quote characters, collapse whitespace."""
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("→", "->")  # models sometimes turn -> into an arrow
    return re.sub(r"\s+", " ", text).strip().strip("\"'` ")


def _parts_in_order(parts: list[str], text: str) -> bool:
    position = 0
    for part in parts:
        found = text.find(part, position)
        if found == -1:
            return False
        position = found + len(part)
    return True


def quote_in_output(quote: str, output: str) -> bool:
    """True if the quote appears in the output.

    Allowed shortenings: skip text with '...', or drop comma-separated fields of ONE line
    (e.g. keep "n 59 -> 47, p95 4.88 -> 85.56 ms" from a longer trace line). Every part
    must still appear, in order, so the quote can only contain real text.
    """
    parts = [p for p in (normalize(x) for x in re.split(r"\.\.\.|…", quote)) if p]
    if parts and _parts_in_order(parts, normalize(output)):
        return True
    fields = [p for p in (normalize(x) for x in re.split(r"\.\.\.|…|,", quote)) if p]
    return bool(fields) and any(_parts_in_order(fields, normalize(line)) for line in output.splitlines())


def verify_evidence(evidence: list, tool_results: dict[str, dict]) -> list[str]:
    """Every item must cite a real tool_call_id and quote text that is in that output."""
    errors = []
    for item in evidence:
        result = tool_results.get(item.tool_call_id)
        if result is None:
            errors.append(f"evidence cites {item.tool_call_id}, but no tool call has that id")
        elif len(normalize(item.quote).replace(" ", "")) < MIN_QUOTE_CHARS:
            errors.append(f"quote '{item.quote}' is too short to check; copy a longer exact line")
        elif not quote_in_output(item.quote, result["data"]):
            hint = closest_line(item.quote, result["data"])
            errors.append(
                f"quote '{item.quote[:80]}' is not in the output of {item.tool_call_id}"
                + (f" (closest real line: '{hint}')" if hint else "")
            )
    return errors


def closest_line(quote: str, output: str) -> str | None:
    """The line of the tool output most similar to a wrong quote, to help the model correct it."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    match = difflib.get_close_matches(quote.strip(), lines, n=1, cutoff=0.4)
    return match[0][:160] if match else None


def check_candidates(candidates: list, services: list[str]) -> list[str]:
    errors = []
    for cand in candidates:
        if cand.service not in services:
            errors.append(f"candidate service '{cand.service}' is not a known service")
        if cand.fault_type not in (*FAULT_TYPES, "unknown"):
            errors.append(f"fault_type '{cand.fault_type}' must be one of {', '.join(FAULT_TYPES)}, unknown")
    return errors


def top_confidence(candidates: list) -> float:
    return max((c.confidence for c in candidates), default=0.0)
