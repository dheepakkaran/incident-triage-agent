"""A scripted chat model for tests and the adversarial suite.

It returns the scripted replies in order and records every prompt it was sent, so a
test can drive the graph down an exact path and then check what the model saw.
A script item can be:
  - an AIMessage (returned as is),
  - a str (returned as a plain text reply, e.g. JSON for a structured step),
  - a callable(messages) -> AIMessage | str, for replies that depend on the prompt.
"""

import itertools
import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage

_ids = itertools.count(1)


def tool_call(name: str, args: dict | None = None, call_id: str | None = None) -> AIMessage:
    """An AIMessage asking for one tool call, like a real provider would return."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": call_id or f"call_{next(_ids)}", "type": "tool_call"}],
    )


def tool_calls(*calls: tuple[str, dict]) -> AIMessage:
    """An AIMessage asking for several tool calls in one turn."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"} for name, args in calls
        ],
    )


def as_json(obj: dict) -> str:
    return json.dumps(obj)


class ScriptedChatModel:
    """Duck-types the two LangChain chat-model methods the agent uses: bind_tools and invoke."""

    def __init__(self, script: list[AIMessage | str | Callable], name: str = "scripted"):
        self.script = list(script)
        self.model_name = name
        self.prompts: list[list[BaseMessage]] = []
        self.bound_tools: list | None = None

    def bind_tools(self, tools: list, **kwargs) -> "ScriptedChatModel":
        # Share the script and the prompt log with the bound copy.
        bound = ScriptedChatModel.__new__(ScriptedChatModel)
        bound.__dict__ = self.__dict__
        self.bound_tools = tools
        return bound

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        self.prompts.append(list(messages))
        if not self.script:
            raise AssertionError(f"ScriptedChatModel ran out of replies after {len(self.prompts) - 1} calls")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, BaseMessage):
            item = item(messages)
        reply = AIMessage(content=item) if isinstance(item, str) else item
        reply.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return reply

    @property
    def remaining(self) -> int:
        return len(self.script)
