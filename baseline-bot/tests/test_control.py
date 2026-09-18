"""Tests for the local control endpoint (kill switch, P0-14)."""
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest

from main import RUNNING, STOPPED, halt
from utils.control import ControlServer


@pytest.fixture
def bot():
    client = MagicMock()
    client.get_orders.return_value = [
        {"id": "order-1", "status": "OPEN"},
        {"id": "order-2", "status": "FILLED"},
    ]
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()
    state = {"session_id": "s-1", "session_open": True}

    def status():
        return {"state": lifecycle["value"], "session_id": state["session_id"],
                "session_open": state["session_open"]}

    server = ControlServer(status, lambda: halt(client, lifecycle, shutdown_event), port=0)
    server.start()
    yield {"client": client, "lifecycle": lifecycle, "shutdown": shutdown_event,
           "url": f"http://127.0.0.1:{server.port}"}
    server.stop()


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.status, json.loads(r.read())


def _post(url):
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, json.loads(r.read())


def test_status_reports_lifecycle_and_session(bot):
    code, body = _get(bot["url"] + "/status")
    assert code == 200
    assert body == {"state": "RUNNING", "session_id": "s-1", "session_open": True}


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
    bot["client"].cancel_order.side_effect = Exception("ORDER_NOT_CANCELLABLE")
    _, body = _post(bot["url"] + "/halt")
    assert body["unresolved"] == 1
    assert body["state"] == STOPPED


def test_unknown_paths_are_404(bot):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(bot["url"] + "/nope")
    assert exc.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(bot["url"] + "/status")  # halt must be a POST; GET-only paths reject POST
    assert exc.value.code == 404


# --- Manual override endpoint (POST /order) ------------------------------

def _post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, method="POST", data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, json.loads(r.read())


@pytest.fixture
def order_server():
    calls = []

    def order_fn(symbol, side, quantity):
        calls.append((symbol, side, quantity))
        if quantity > 1000:
            return {"ok": False, "reason": "too big"}
        return {"ok": True, "symbol": symbol, "side": side, "quantity": quantity,
                "order_id": "o-9", "status": "OPEN", "filled": 0}

    server = ControlServer(lambda: {"state": "RUNNING"}, lambda: 0,
                           port=0, order_fn=order_fn)
    server.start()
    yield {"url": f"http://127.0.0.1:{server.port}", "calls": calls}
    server.stop()


def test_order_places_through_order_fn(order_server):
    code, body = _post_json(order_server["url"] + "/order",
                            {"symbol": "NOVA", "side": "buy", "quantity": 200})
    assert code == 200
    assert body["ok"] is True and body["order_id"] == "o-9"
    assert order_server["calls"] == [("NOVA", "buy", 200)]


def test_order_refusal_is_409_with_reason(order_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_json(order_server["url"] + "/order",
                   {"symbol": "NOVA", "side": "buy", "quantity": 5000})
    assert exc.value.code == 409
    body = json.loads(exc.value.read())
    assert body["ok"] is False and body["reason"] == "too big"


def test_order_validates_side_and_quantity(order_server):
    for bad in ({"symbol": "NOVA", "side": "hold", "quantity": 10},
                {"symbol": "NOVA", "side": "buy", "quantity": 0},
                {"symbol": "", "side": "buy", "quantity": 10}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(order_server["url"] + "/order", bad)
        assert exc.value.code == 400
    assert order_server["calls"] == []  # never reached the placer


def test_order_endpoint_absent_without_order_fn(bot):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_json(bot["url"] + "/order", {"symbol": "NOVA", "side": "buy", "quantity": 1})
    assert exc.value.code == 404
