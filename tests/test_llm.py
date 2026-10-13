"""Provider chain: retry on rate limits, fall back on failure, serve repeats from the disk cache."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from triage.llm.cache import DiskCache
from triage.llm.mock import ScriptedChatModel, tool_call
from triage.llm.providers import MAX_RETRIES, LLMClient, LLMError, ModelHandle


class RateLimited(Exception):
    status_code = 429


class BadRequest(Exception):
    status_code = 400


class FailingModel:
    """Raises the given error every time; counts calls."""

    def __init__(self, error: Exception):
        self.error, self.calls = error, 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.calls += 1
        raise self.error


MESSAGES = [HumanMessage("hello")]


def test_rate_limit_retries_then_falls_back():
    sleeps = []
    failing = FailingModel(RateLimited("429 too many requests"))
    backup = ScriptedChatModel(["from backup"])
    client = LLMClient([ModelHandle("groq", "m1", failing), ModelHandle("ollama", "m2", backup)], sleep=sleeps.append)

    reply, record = client.invoke(MESSAGES)

    assert reply.content == "from backup"
    assert failing.calls == MAX_RETRIES + 1
    assert len(sleeps) == MAX_RETRIES
    assert record["provider"] == "ollama"


def test_non_retryable_error_falls_back_immediately():
    sleeps = []
    failing = FailingModel(BadRequest("400 tool_use_failed"))
    client = LLMClient(
        [ModelHandle("groq", "m1", failing), ModelHandle("ollama", "m2", ScriptedChatModel(["ok"]))], sleep=sleeps.append
    )
    client.invoke(MESSAGES)
    assert failing.calls == 1 and sleeps == []


def test_all_providers_failing_raises():
    client = LLMClient([ModelHandle("groq", "m1", FailingModel(BadRequest("nope")))], sleep=lambda s: None)
    with pytest.raises(LLMError, match="all providers failed"):
        client.invoke(MESSAGES)


def test_no_providers_gives_a_clear_error():
    with pytest.raises(LLMError, match="No LLM provider"):
        LLMClient([])


def test_identical_call_is_served_from_cache(tmp_path):
    model = ScriptedChatModel([tool_call("list_services", {}, call_id="call_a")])
    client = LLMClient([ModelHandle("groq", "m1", model)], cache=DiskCache(tmp_path))
    tools = [{"type": "function", "function": {"name": "list_services", "parameters": {}}}]

    first, rec1 = client.invoke(MESSAGES, tools=tools)
    second, rec2 = client.invoke(MESSAGES, tools=tools)

    assert rec1["cached"] is False and rec2["cached"] is True
    assert second.tool_calls[0]["id"] == "call_a" == first.tool_calls[0]["id"]
    assert len(model.prompts) == 1  # the model was only called once


def test_cache_key_ignores_message_ids(tmp_path):
    model = ScriptedChatModel(["one"])
    client = LLMClient([ModelHandle("groq", "m1", model)], cache=DiskCache(tmp_path))
    client.invoke([HumanMessage("hi", id="run-1")])
    reply, record = client.invoke([HumanMessage("hi", id="run-2")])
    assert record["cached"] and reply.content == "one"


def test_scripted_model_records_prompts_and_runs_out_loudly():
    model = ScriptedChatModel([AIMessage(content="x")])
    model.invoke(MESSAGES)
    assert model.prompts == [MESSAGES]
    with pytest.raises(AssertionError, match="ran out"):
        model.invoke(MESSAGES)
