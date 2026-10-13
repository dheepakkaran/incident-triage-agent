"""Disk cache for LLM replies, so eval reruns do not spend free-tier quota again.

Key = sha256 of (provider, model, messages, tools). Message ids are left out of the key
on purpose: LangGraph gives every message a random id, which would make every run a miss.
"""

import hashlib
import json
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, message_to_dict, messages_from_dict


def _message_key(msg: BaseMessage) -> dict:
    key = {"type": msg.type, "content": msg.content}
    if getattr(msg, "tool_calls", None):
        key["tool_calls"] = [{"name": c["name"], "args": c["args"], "id": c["id"]} for c in msg.tool_calls]
    if getattr(msg, "tool_call_id", None):
        key["tool_call_id"] = msg.tool_call_id
    return key


def cache_key(provider: str, model: str, messages: list[BaseMessage], tools: list | None) -> str:
    payload = {
        "provider": provider,
        "model": model,
        "messages": [_message_key(m) for m in messages],
        "tools": tools or [],
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class DiskCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> tuple[AIMessage, dict] | None:
        path = self._path(key)
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        message = messages_from_dict([record["message"]])[0]
        return message, record["usage"]

    def put(self, key: str, message: AIMessage, usage: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"message": message_to_dict(message), "usage": usage}
        path.write_text(json.dumps(record))
