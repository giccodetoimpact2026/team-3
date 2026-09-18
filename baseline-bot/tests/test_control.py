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
