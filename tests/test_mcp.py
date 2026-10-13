"""The MCP server exposes the 5 tools, validates case_id and arguments, and never shows labels."""

import asyncio

import pytest

from triage import mcp_server


def run(coro):
    return asyncio.run(coro)


def text_of(result) -> str:
    content = result.content if hasattr(result, "content") else result
    return "\n".join(getattr(c, "text", str(c)) for c in content)


def test_lists_exactly_the_five_tools_all_read_only():
    tools = run(mcp_server.server.list_tools())
    assert sorted(t.name for t in tools) == sorted(
        ["list_services", "get_metric_changes", "query_logs", "get_trace_stats", "search_runbooks"]
    )
    for tool in tools:
        assert "case_id" in tool.input_schema["properties"]
        assert tool.annotations.read_only_hint is True


def test_get_metric_changes_on_a_known_case():
    out = mcp_server.get_metric_changes("case_001", top_n=3)
    assert out.splitlines()[1].startswith("cartservice cpu_usage")


@pytest.mark.parametrize("bad", ["case_999", "../data/labels.json", "case_001; DROP TABLE logs"])
def test_unknown_case_id_is_rejected(bad):
    with pytest.raises(ValueError, match="unknown case_id"):
        mcp_server.list_services(bad)


def test_bad_arguments_are_rejected():
    with pytest.raises(ValueError, match="unknown service"):
        mcp_server.get_trace_stats("case_001", "x'; DROP TABLE logs;--")
    with pytest.raises(ValueError, match="less than or equal to 20"):
        mcp_server.query_logs("case_001", "frontend", limit=500)


def test_alerts_resource_has_no_labels():
    text = mcp_server.alerts()
    assert text.startswith("case_001: frontend p95 latency")
    assert "label" not in text.lower() and "root_cause" not in text.lower()
