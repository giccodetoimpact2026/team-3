"""Tests for the local control endpoint (kill switch + health, P0-14)."""
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from exchange.results import CancelOutcome, CancelResult, Outcome, SubmitResult
from main import RUNNING, STOPPED, halt
from orders.manager import OrderManager
from risk.limits import RiskLimits
from utils.control import ControlServer


@pytest.fixture
def bot():
    client = Mock()
    client.get_orders.return_value = [
        {"id": "order-1", "status": "OPEN"},
        {"id": "order-2", "status": "FILLED"},
    ]
    client.get_portfolio.return_value = {
        "session_id": "s-1", "cash": 1_000_000_000, "reserved_cash": 0, "positions": []}
    client.cancel_order.side_effect = lambda oid: CancelResult(CancelOutcome.CANCELLED, oid)
    client.submit_limit_order.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="o9", status="OPEN", remaining_qty=100)
    orders = OrderManager(client, RiskLimits(500, 200, "NOVA", "ELXR"), session_id="s-1")
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()
    state = {"session_id": "s-1", "session_open": True}

    def status():
        return {"state": lifecycle["value"], "session_id": state["session_id"],
                "session_open": state["session_open"], "heartbeat_age_s": 1.0,
                "orders": orders.status()}

    server = ControlServer(status, lambda: halt(orders, lifecycle, shutdown_event),
                           port=0, events_fn=orders.journal_page)
    server.start()
    yield {"client": client, "orders": orders, "lifecycle": lifecycle,
           "shutdown": shutdown_event, "url": f"http://127.0.0.1:{server.port}"}
    server.stop()


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.status, json.loads(r.read())


def _post(url):
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, json.loads(r.read())


def test_status_reports_lifecycle_session_and_health(bot):
    code, body = _get(bot["url"] + "/status")
    assert code == 200
    assert body["state"] == "RUNNING"
    assert body["session_id"] == "s-1"
    assert body["session_open"] is True
    assert body["heartbeat_age_s"] == 1.0
    assert body["orders"]["active_orders"] == 0


def test_halt_runs_halt_path_and_reports_result(bot):
    code, body = _post(bot["url"] + "/halt")
    assert code == 200
    assert body["state"] == STOPPED
    assert body["unresolved"] == 0
    bot["client"].cancel_order.assert_called_once_with("order-1")
    assert bot["lifecycle"]["value"] == STOPPED
    assert bot["shutdown"].is_set()


def test_halt_twice_is_idempotent(bot):
    _post(bot["url"] + "/halt")
    code, body = _post(bot["url"] + "/halt")
    assert code == 200
    assert body["state"] == STOPPED
    assert bot["client"].cancel_order.call_count == 1  # second call cancels nothing


def test_halt_reports_unresolved_count(bot):
    bot["client"].cancel_order.side_effect = lambda oid: CancelResult(
        CancelOutcome.UNKNOWN, oid, code="TIMEOUT")
    _, body = _post(bot["url"] + "/halt")
    assert body["unresolved"] == 1
    assert body["state"] == STOPPED


def test_orders_route_serves_the_journal_since_a_cursor(bot):
    bot["orders"].submit_intent("NOVA", "buy", 100, 4250)
    code, body = _get(bot["url"] + "/orders?since=0")
    assert code == 200
    assert body["seq"] == 1
    assert [e["status"] for e in body["events"]] == ["OPEN"]
    assert body["events"][0]["order_id"] == "o9"

    code, body = _get(bot["url"] + "/orders?since=1")
    assert body == {"seq": 1, "events": []}

    # A cursor ahead of the journal (stale, from an earlier bot process) still
    # learns the real counter so it can reset.
    _, body = _get(bot["url"] + "/orders?since=99")
    assert body["seq"] == 1


def test_orders_route_rejects_a_bad_cursor(bot):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(bot["url"] + "/orders?since=abc")
    assert exc.value.code == 400


def test_unknown_paths_are_404(bot):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(bot["url"] + "/nope")
    assert exc.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(bot["url"] + "/status")  # halt must be a POST; GET-only paths reject POST
    assert exc.value.code == 404
