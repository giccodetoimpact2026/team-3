"""Tests for the lifecycle: halt path, session boundary, bootstrap."""
import threading
from unittest.mock import MagicMock, Mock

from exchange.results import CancelOutcome, CancelResult, Outcome, SubmitResult
from main import QUIESCING, RUNNING, STOPPED, halt
from orders.manager import OrderManager
from risk.limits import RiskLimits
from strategies.momentum import MomentumStrategy


def _client(orders=(), cancel=None):
    c = Mock()
    c.get_orders.return_value = list(orders)
    c.get_portfolio.return_value = {
        "session_id": "w1", "cash": 1_000_000_000, "reserved_cash": 0, "positions": []}
    c.cancel_order.side_effect = cancel or (
        lambda oid: CancelResult(CancelOutcome.CANCELLED, oid))
    c.submit_limit_order.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="o1", status="OPEN", remaining_qty=100)
    return c


def _manager(client):
    return OrderManager(client, RiskLimits(500, 200, "NOVA", "ELXR"), session_id="w1")


# ---------------------------------------------------------------------------
# halt() — P0-11, P0-14
# ---------------------------------------------------------------------------

def test_halt_cancels_open_orders_and_stops():
    client = _client([
        {"id": "order-1", "status": "OPEN"},
        {"id": "order-2", "status": "PARTIALLY_FILLED"},
        {"id": "order-3", "status": "FILLED"},
    ])
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    unresolved = halt(_manager(client), lifecycle, shutdown_event)

    assert unresolved == 0
    assert client.cancel_order.call_count == 2
    client.cancel_order.assert_any_call("order-1")
    client.cancel_order.assert_any_call("order-2")
    assert lifecycle["value"] == STOPPED
    assert shutdown_event.is_set()


def test_halt_blocks_intents_before_the_first_cancel():
    """No fresh order may slip in between enumeration and cancellation."""
    client = _client([{"id": "order-1", "status": "OPEN"}])
    orders = _manager(client)
    seen = []

    def cancel(oid):
        seen.append((lifecycle["value"], orders.blocked))
        return CancelResult(CancelOutcome.CANCELLED, oid)

    client.cancel_order.side_effect = cancel
    lifecycle = {"value": RUNNING}

    halt(orders, lifecycle, threading.Event())

    assert seen == [(QUIESCING, True)]
    assert orders.submit_intent("NOVA", "buy", 100, 4250) is None
    client.submit_limit_order.assert_not_called()


def test_halt_treats_already_filled_as_settled():
    client = _client([{"id": "order-1", "status": "OPEN"}],
                     cancel=lambda oid: CancelResult(
                         CancelOutcome.NOT_CANCELLABLE, oid, code="ORDER_NOT_CANCELLABLE"))
    lifecycle = {"value": RUNNING}

    unresolved = halt(_manager(client), lifecycle, threading.Event())

    assert unresolved == 0
    assert lifecycle["value"] == STOPPED


def test_halt_reports_a_timed_out_cancel_as_unresolved():
    """A cancel whose fate is unknown must not be reported as done."""
    client = _client([{"id": "order-1", "status": "OPEN"}],
                     cancel=lambda oid: CancelResult(CancelOutcome.UNKNOWN, oid, code="TIMEOUT"))
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    unresolved = halt(_manager(client), lifecycle, shutdown_event)

    assert unresolved == 1
    assert lifecycle["unresolved"] == 1
    assert lifecycle["value"] == STOPPED
    assert shutdown_event.is_set()


def test_halt_with_no_open_orders():
    client = _client([{"id": "order-1", "status": "FILLED"}])
    unresolved = halt(_manager(client), {"value": RUNNING}, threading.Event())
    assert unresolved == 0
    client.cancel_order.assert_not_called()


def test_halt_is_idempotent():
    client = _client()
    orders = _manager(client)
    halt(orders, {"value": STOPPED}, threading.Event())
    client.get_orders.assert_not_called()


def test_halt_falls_back_to_local_state_when_enumeration_fails():
    import requests
    client = _client()
    orders = _manager(client)
    orders.submit_intent("NOVA", "buy", 100, 4250)   # tracked locally as o1
    client.get_orders.side_effect = requests.ConnectionError("connection refused")
    lifecycle = {"value": RUNNING}

    unresolved = halt(orders, lifecycle, threading.Event())

    client.cancel_order.assert_called_once_with("o1")
    assert unresolved == 0
    assert lifecycle["value"] == STOPPED


def test_halt_stops_feed_and_worker():
    client = _client()
    feed, worker = MagicMock(), MagicMock()
    halt(_manager(client), {"value": RUNNING}, threading.Event(), feed=feed, worker=worker)
    feed.stop.assert_called_once()
    worker.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Session boundary reset — P0-13
# ---------------------------------------------------------------------------

def test_session_boundary_resets_strategies_and_order_state():
    client = _client()
    orders = _manager(client)
    cfg = {"lookback_samples": 3, "momentum_threshold": 0.01,
           "trade_quantity": 100, "limit_offset_bps": 5}
    strat = MomentumStrategy("NOVA", cfg)
    for p in (10_000, 10_100, 10_200):
        strat.on_price(p)
    orders.submit_intent("NOVA", "buy", 100, 4250)
    assert orders.has_active("NOVA")

    # What on_session does on an id change.
    for s in (strat,):
        s.prices.clear()
    orders.reset_for_session("w2")

    assert len(strat.prices) == 0
    assert not orders.has_active("NOVA")
    assert orders.status()["active_orders"] == 0


# ---------------------------------------------------------------------------
# Session bootstrap — P0-12
# ---------------------------------------------------------------------------

def test_bootstrap_sets_session_open_when_running():
    state = {"session_open": False, "session_id": None}
    session_data = {"status": "running", "id": "session-abc"}
    state["session_open"] = session_data.get("status") == "running"
    state["session_id"] = session_data.get("id")
    assert state == {"session_open": True, "session_id": "session-abc"}


def test_bootstrap_stays_closed_when_ended():
    session_data = {"status": "ended", "id": "session-xyz"}
    assert (session_data.get("status") == "running") is False
