"""Process guardrails: every tool call the model asks for is checked here before it runs.

Checks, in order: known tool, budget left, arguments valid (Pydantic, with the case's
service list as context), not a duplicate of an earlier call.
"""

import json

from pydantic import BaseModel, ValidationError

TRUNCATE_AT = 6000  # characters of tool output the model may see per call


def check_known_tool(name: str, allowed: list[str]) -> str | None:
    if name not in allowed:
        return f"unknown tool '{name}'. Allowed tools: {', '.join(allowed)}"
    return None


def check_budget(used: int, budget: int) -> str | None:
    if used >= budget:
        return f"tool budget exhausted ({used}/{budget} calls used); stop investigating and give your diagnosis"
    return None


def validate_args(args_model: type[BaseModel], raw_args: dict, services: list[str]) -> tuple[BaseModel | None, str | None]:
    """Pydantic validation. The service list is passed as context so `service` must be a real one."""
    try:
        return args_model.model_validate(raw_args or {}, context={"services": services}), None
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "args"
        return None, f"invalid arguments ({where}): {first['msg']}"


def call_signature(tool: str, args: dict) -> str:
    """Canonical form of a call, used to spot the same tool with the same arguments twice."""
    return json.dumps({"tool": tool, "args": args}, sort_keys=True)


def find_duplicate(tool: str, args: dict, tool_results: dict[str, dict]) -> str | None:
    """Return the id of an earlier identical call, if any."""
    signature = call_signature(tool, args)
    for call_id, result in tool_results.items():
        if call_signature(result["tool"], result["args"]) == signature:
            return call_id
    return None


def truncate(text: str, limit: int = TRUNCATE_AT) -> tuple[str, bool]:
    """Cut at a line boundary so quotes stay whole, and say that we cut."""
    if len(text) <= limit:
        return text, False
    kept, size = [], 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit - 60:
            break
        kept.append(line)
        size += len(line) + 1
    dropped = len(text.splitlines()) - len(kept)
    return "\n".join(kept) + f"\n... [truncated: {dropped} more lines not shown]", True
