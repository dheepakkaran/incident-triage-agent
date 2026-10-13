"""Build one leak-free alert per case, the way a monitoring system would page someone.

Only the entry service (frontend) is used. Its HTTP route is worked out from the
frontend's own outbound calls (a request that calls PlaceOrder is the checkout route),
which the frontend knows about itself. The alert never names a downstream service or a
fault type. It reports either a route's p95 latency change or the frontend error rate.
"""

import sqlite3
from datetime import UTC, datetime

import pandas as pd

ENTRY_SERVICE = "frontend"
MIN_TRACES = 20  # per window, so a p95 is not computed from a handful of requests
ERROR_ALERT_RATE = 0.05  # page on errors when at least 5% of requests fail ...
ERROR_ALERT_FACTOR = 3.0  # ... and that is at least 3x the baseline rate


def route_of(operations: set[str]) -> str:
    """Map the frontend's outbound calls in one request to the HTTP route that made them."""
    if "CheckoutService/PlaceOrder" in operations:
        return "POST /cart/checkout"
    if "CartService/AddItem" in operations:
        return "POST /cart"
    if "ShippingService/GetQuote" in operations:
        return "GET /cart"
    if "ProductCatalogService/GetProduct" in operations and (
        "ProductCatalogService/ListProducts" not in operations
    ):
        return "GET /product"
    if "ProductCatalogService/ListProducts" in operations:
        return "GET /"
    return "other"


def frontend_requests(conn: sqlite3.Connection, case_id: str) -> pd.DataFrame:
    """One row per frontend request: start, duration, route, and whether any outbound call failed."""
    spans = pd.read_sql_query(
        "SELECT trace_id, parent_id, operation, start_ts, duration_ms, status_code "
        "FROM spans WHERE case_id = ? AND service = ?",
        conn,
        params=(case_id, ENTRY_SERVICE),
    )
    roots = spans[spans.parent_id.isna()].copy()
    calls = spans[spans.parent_id.notna()]
    operations = calls.groupby("trace_id").operation.agg(set)
    failed = calls.assign(bad=calls.status_code.fillna(0) != 0).groupby("trace_id").bad.any()
    roots["route"] = roots.trace_id.map(lambda t: route_of(operations.get(t, set())))
    roots["failed"] = roots.trace_id.map(failed).fillna(False).astype(bool)
    return roots


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M UTC")


def build_alert(conn: sqlite3.Connection, case_id: str) -> dict:
    inject = conn.execute("SELECT inject_ts FROM cases WHERE case_id = ?", (case_id,)).fetchone()[0]
    requests = frontend_requests(conn, case_id)
    before = requests[requests.start_ts < inject]
    after = requests[requests.start_ts >= inject]
    since = _hhmm(inject)

    err_before, err_after = before.failed.mean(), after.failed.mean()
    if err_after >= ERROR_ALERT_RATE and err_after >= ERROR_ALERT_FACTOR * max(err_before, 0.001):
        text = f"{ENTRY_SERVICE} error rate {err_after:.1%} since {since} (baseline {err_before:.1%})"
        return {"case_id": case_id, "service": ENTRY_SERVICE, "signal": "error_rate", "started_at": inject, "text": text}

    worst = None
    for route, group in after.groupby("route"):
        base = before[before.route == route]
        if route == "other" or len(base) < MIN_TRACES or len(group) < MIN_TRACES:
            continue
        p95_before, p95_after = base.duration_ms.quantile(0.95), group.duration_ms.quantile(0.95)
        ratio = p95_after / p95_before
        if worst is None or ratio > worst[1]:
            worst = (route, ratio, p95_before, p95_after)
    route, ratio, p95_before, p95_after = worst
    text = (
        f"{ENTRY_SERVICE} p95 latency on {route} up {ratio:.1f}x since {since} "
        f"({p95_before:.0f}ms -> {p95_after:.0f}ms)"
    )
    return {"case_id": case_id, "service": ENTRY_SERVICE, "signal": "latency_p95", "started_at": inject, "text": text}
