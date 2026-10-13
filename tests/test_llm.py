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


def test_thinking_blocks_are_dropped_and_server_errors_retried():
    from triage.llm.providers import _is_retryable

    thinking_reply = AIMessage(content=[{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"a": 1}'}])
    client = LLMClient([ModelHandle("google", "gemma", ScriptedChatModel([thinking_reply]))])
    reply, _ = client.invoke(MESSAGES)
    assert reply.content == '{"a": 1}'

    class GoogleStyleError(Exception):
        code = 500

    assert _is_retryable(GoogleStyleError("500 INTERNAL"))


def test_fallback_cache_never_beats_the_primary(tmp_path):
    cache = DiskCache(tmp_path)
    old_backup = LLMClient([ModelHandle("ollama", "small", ScriptedChatModel(["old answer"]))], cache=cache)
    old_backup.invoke(MESSAGES)  # the fallback's answer is now cached
    primary = ScriptedChatModel(["fresh answer"])
    client = LLMClient([ModelHandle("groq", "big", primary), ModelHandle("ollama", "small", ScriptedChatModel([]))], cache=cache)
    reply, record = client.invoke(MESSAGES)
    assert reply.content == "fresh answer" and record["provider"] == "groq" and not record["cached"]


def test_long_rate_limit_wait_moves_on_when_a_backup_exists():
    class SlowRateLimit(Exception):
        status_code = 429

        class response:  # noqa: N801 - mimics an HTTP response object
            headers = {"retry-after": "60"}

    sleeps = []
    failing = FailingModel(SlowRateLimit("429"))
    client = LLMClient([ModelHandle("groq", "m1", failing), ModelHandle("ollama", "m2", ScriptedChatModel(["backup"]))], sleep=sleeps.append)
    reply, record = client.invoke(MESSAGES)
    assert record["provider"] == "ollama" and failing.calls == 1 and sleeps == []  # no 60 s wait

    sleeps.clear()
    alone = FailingModel(SlowRateLimit("429"))
    with pytest.raises(LLMError):
        LLMClient([ModelHandle("groq", "m1", alone)], sleep=sleeps.append).invoke(MESSAGES)
    assert len(sleeps) == MAX_RETRIES and sleeps[0] > 60  # the last provider waits it out
