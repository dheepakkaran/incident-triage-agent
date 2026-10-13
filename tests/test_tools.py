"""Unit tests for the 5 tools and the read-only DB layer, on the tiny fixture DB."""

import sqlite3

import pytest

from tests.conftest import INJECTION_LINE
from triage.guardrails.input import REMOVED
from triage.guardrails.process import TRUNCATE_AT, validate_args
from triage.tools import TOOLS, run_tool, tool_schemas
from triage.tools.db import readonly
from triage.tools.logs import template_of
from triage.tools.runbooks import load_runbooks


def call(ctx, name, **raw):
    args, error = validate_args(TOOLS[name].args_model, raw, list(ctx.services))
    assert error is None, error
    text, truncated, removed = run_tool(name, ctx, args)
    return text


# ---- db layer -------------------------------------------------------------


def test_db_connection_is_read_only(fixture_db):
    with readonly(fixture_db) as conn, pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM logs")


def test_context_knows_case_services(ctx):
    assert ctx.services == ("cartservice", "frontend")
    assert ctx.inject_ts == 10_000


# ---- list_services --------------------------------------------------------


def test_list_services_shows_services_and_edges(ctx):
    text = call(ctx, "list_services")
    assert "services (2): cartservice, frontend" in text
    assert "frontend -> cartservice: 40" in text


# ---- get_metric_changes ---------------------------------------------------


def test_metric_changes_ranks_biggest_change_first(ctx):
    lines = call(ctx, "get_metric_changes").splitlines()
    assert lines[1].startswith("cartservice cpu_usage: 10.00 -> 80.00 (+700%)")
    assert "frontend latency_p90_s: 0.1 -> 0.5 (+400%)" in lines[2]
    # Flat metrics have 0% change and come last.
    assert lines[-1].endswith("(+0%)")


def test_metric_changes_for_one_service(ctx):
    text = call(ctx, "get_metric_changes", service="frontend", top_n=1)
    assert "cartservice" not in text
    assert len(text.splitlines()) == 2


@pytest.mark.parametrize(
    "raw, problem",
    [({"service": "paymentservice"}, "unknown service"), ({"top_n": 99}, "less than or equal to 30")],
)
def test_metric_changes_rejects_bad_args(ctx, raw, problem):
    _, error = validate_args(TOOLS["get_metric_changes"].args_model, raw, list(ctx.services))
    assert problem in error


# ---- query_logs -----------------------------------------------------------


def test_query_logs_groups_templates_with_before_after_counts(ctx):
    text = call(ctx, "query_logs", service="cartservice", level="ERROR")
    assert '"request timeout after <n>ms" count 1 -> 12' in text
    assert "e.g. request timeout after" in text


def test_query_logs_any_level_masks_ids_and_reports_totals(ctx):
    text = call(ctx, "query_logs", service="cartservice", level="ANY")
    assert "GetCartAsync called with userId=<id>" in text
    assert "all lines (any level) before -> after: 121 -> 13" in text


def test_query_logs_sanitizes_injection_in_log_lines(ctx):
    text = call(ctx, "query_logs", service="cartservice", level="ANY")
    assert "ignore prior instructions" not in text
    assert REMOVED in text
    assert INJECTION_LINE not in text


def test_query_logs_keyword_filter_and_limits(ctx):
    text = call(ctx, "query_logs", service="frontend", level="ANY", keyword="failed", limit=1)
    assert "failed to get cart" in text
    assert "request started" not in text
    _, error = validate_args(TOOLS["query_logs"].args_model, {"service": "frontend", "limit": 50}, list(ctx.services))
    assert "less than or equal to 20" in error
    _, error = validate_args(TOOLS["query_logs"].args_model, {"service": "frontend", "level": "DEBUG"}, list(ctx.services))
    assert error is not None


def test_template_masks_numbers_ids_and_emails():
    assert template_of("sent to a@b.com in 12.5ms id=deadbeefcafe1234") == "sent to <email> in <n>ms id=<hex>"


# ---- get_trace_stats ------------------------------------------------------


def test_trace_stats_for_callee_shows_incoming_edge_and_server_time(ctx):
    text = call(ctx, "get_trace_stats", service="cartservice")
    assert "frontend -> cartservice: n 20 -> 20, errors 0.0% -> 10.0%, p50 40.00 -> 900 ms" in text
    assert "inside cartservice (server spans): n 20 -> 20" in text


def test_trace_stats_for_caller_shows_outgoing_edge(ctx):
    text = call(ctx, "get_trace_stats", service="frontend")
    outgoing = text.split("outgoing calls:")[1]
    assert "frontend -> cartservice" in outgoing
    assert "inside frontend (server spans)" in text


# ---- search_runbooks ------------------------------------------------------


def test_runbooks_parse_front_matter():
    books = load_runbooks()
    assert 6 <= len(books) <= 8
    for book in books.values():
        assert book["allowed_actions"], book["id"]
        assert "## Symptoms" in book["body"]


def test_search_runbooks_renders_hits(ctx):
    text = call(ctx, "search_runbooks", query="cpu saturation in cartservice", k=2)
    assert text.count("\n- rb-") == 2
    assert "rb-cpu-saturation" in text
    assert "allowed_actions=scale_service,escalate_to_human" in text


def test_search_runbooks_k_is_capped(ctx):
    _, error = validate_args(TOOLS["search_runbooks"].args_model, {"query": "cpu", "k": 5}, list(ctx.services))
    assert "less than or equal to 3" in error


# ---- shared ---------------------------------------------------------------


def test_tool_output_is_capped(ctx, monkeypatch):
    monkeypatch.setitem(TOOLS, "list_services", TOOLS["list_services"].__class__("list_services", TOOLS["list_services"].args_model, lambda c, a: "x" * 50 + "\n" + "y\n" * 9000))
    args, _ = validate_args(TOOLS["list_services"].args_model, {}, list(ctx.services))
    text, truncated, _ = run_tool("list_services", ctx, args)
    assert truncated and len(text) <= TRUNCATE_AT
    assert "[truncated:" in text


def test_tool_schemas_have_no_case_id():
    for schema in tool_schemas(list(TOOLS)):
        assert "case_id" not in schema["function"]["parameters"].get("properties", {})
        assert schema["function"]["description"]
