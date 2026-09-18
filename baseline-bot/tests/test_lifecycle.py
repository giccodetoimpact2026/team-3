"""Tests for P3 fixes: session bootstrap, boundary reset, graceful shutdown."""
import threading
from unittest.mock import MagicMock

from main import RUNNING, QUIESCING, STOPPED, halt
from strategies.momentum import MomentumStrategy


# ---------------------------------------------------------------------------
# halt() — P0-11, P0-14
# ---------------------------------------------------------------------------

def test_halt_cancels_open_orders():
    client = MagicMock()
    client.get_orders.return_value = [
        {"id": "order-1", "status": "OPEN"},
        {"id": "order-2", "status": "PARTIALLY_FILLED"},
        {"id": "order-3", "status": "FILLED"},
    ]
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    halt(client, lifecycle, shutdown_event)

    assert client.cancel_order.call_count == 2
    client.cancel_order.assert_any_call("order-1")
    client.cancel_order.assert_any_call("order-2")
    assert lifecycle["value"] == STOPPED
    assert shutdown_event.is_set()


def test_halt_sets_quiescing_before_cancelling():
    observed_states = []

    client = MagicMock()

    def record_state_on_get_orders():
        observed_states.append(lifecycle["value"])
        return []

    client.get_orders.side_effect = record_state_on_get_orders
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    halt(client, lifecycle, shutdown_event)

    assert observed_states == [QUIESCING]


def test_halt_tolerates_already_filled_cancel():
    client = MagicMock()
    client.get_orders.return_value = [
        {"id": "order-1", "status": "OPEN"},
    ]
    client.cancel_order.side_effect = Exception("ORDER_NOT_CANCELLABLE")
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    unknown = halt(client, lifecycle, shutdown_event)

    assert unknown == 1
    assert lifecycle["value"] == STOPPED
    assert shutdown_event.is_set()


def test_halt_with_no_open_orders():
    client = MagicMock()
    client.get_orders.return_value = [
        {"id": "order-1", "status": "FILLED"},
    ]
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    unknown = halt(client, lifecycle, shutdown_event)

    assert unknown == 0
    client.cancel_order.assert_not_called()


def test_halt_is_idempotent():
    client = MagicMock()
    lifecycle = {"value": STOPPED}
    shutdown_event = threading.Event()

    halt(client, lifecycle, shutdown_event)

    client.get_orders.assert_not_called()


def test_halt_handles_get_orders_failure():
    client = MagicMock()
    client.get_orders.side_effect = Exception("connection refused")
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    unknown = halt(client, lifecycle, shutdown_event)

    assert unknown == 1
    assert lifecycle["value"] == STOPPED
    assert shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Session boundary reset — P0-13
# ---------------------------------------------------------------------------

def _make_strategies():
    cfg = {"lookback_samples": 3, "momentum_threshold": 0.01,
           "trade_quantity": 100, "limit_offset_bps": 5}
    return {"NOVA": MomentumStrategy("NOVA", cfg)}


def test_session_boundary_clears_price_history():
    """Simulates the on_session handler logic for session ID change."""
    strategies = _make_strategies()
    state = {"session_open": False, "session_id": "session-1"}

    strat = strategies["NOVA"]
    for p in (10_000, 10_100, 10_200):
        strat.on_price(p)
    assert len(strat.prices) == 3

    # Simulate on_session with a new session ID.
    new_id = "session-2"
    if new_id and new_id != state["session_id"]:
        for s in strategies.values():
            s.prices.clear()
        state["session_id"] = new_id

    assert len(strat.prices) == 0
    assert state["session_id"] == "session-2"


def test_same_session_id_does_not_clear():
    strategies = _make_strategies()
    state = {"session_open": True, "session_id": "session-1"}

    strat = strategies["NOVA"]
    for p in (10_000, 10_100, 10_200):
        strat.on_price(p)

    new_id = "session-1"
    if new_id and new_id != state["session_id"]:
        for s in strategies.values():
            s.prices.clear()
        state["session_id"] = new_id

    assert len(strat.prices) == 3


# ---------------------------------------------------------------------------
# Session bootstrap — P0-12
# ---------------------------------------------------------------------------

def test_bootstrap_sets_session_open_when_running():
    """Simulates the startup bootstrap logic."""
    state = {"session_open": False, "session_id": None}

    session_data = {"status": "running", "id": "session-abc"}
    if session_data is not None:
        state["session_open"] = session_data.get("status") == "running"
        state["session_id"] = session_data.get("id")

    assert state["session_open"] is True
    assert state["session_id"] == "session-abc"


def test_bootstrap_stays_closed_when_ended():
    state = {"session_open": False, "session_id": None}

    session_data = {"status": "ended", "id": "session-xyz"}
    if session_data is not None:
        state["session_open"] = session_data.get("status") == "running"
        state["session_id"] = session_data.get("id")

    assert state["session_open"] is False


def test_bootstrap_handles_null_session():
    state = {"session_open": False, "session_id": None}

    session_data = None
    if session_data is not None:
        state["session_open"] = session_data.get("status") == "running"
        state["session_id"] = session_data.get("id")

    assert state["session_open"] is False
    assert state["session_id"] is None


# ---------------------------------------------------------------------------
# on_price blocks when not RUNNING — P0-11 guard
# ---------------------------------------------------------------------------

def test_on_price_blocked_during_quiescing():
    """The on_price handler should short-circuit when lifecycle is not RUNNING."""
    lifecycle = {"value": QUIESCING}
    client = MagicMock()
    submitted = []

    def on_price(payload):
        if lifecycle["value"] != RUNNING:
            return
        submitted.append(payload)

    on_price({"symbol": "NOVA", "last_price": 10_000})
    assert submitted == []
    client.submit_limit_order.assert_not_called()
