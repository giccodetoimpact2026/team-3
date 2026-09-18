"""Order-path tests: stacking, pending exposure, reconciliation, typed errors.

Every test here fails against the pre-OrderManager code path.
"""
from unittest.mock import Mock

import pytest
import requests

from exchange.client import ExchangeClient
from exchange.ratelimit import RollingWindow
from exchange.results import CancelOutcome, Outcome, SubmitResult
from orders.manager import OrderManager
from risk.limits import RiskLimits


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def response(status_code, payload=None, headers=None):
    r = Mock()
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.headers = headers or {}
    r.json = Mock(return_value=payload if payload is not None else {})
    return r


def ack(order_id="o1", status="OPEN", remaining=100, filled=0):
    return {"status": "success", "data": {
        "order": {"id": order_id, "status": status, "remaining_qty": remaining},
        "trades": [{"quantity": filled}] if filled else []}}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client():
    c = Mock()
    c.get_orders.return_value = []
    c.get_portfolio.return_value = {
        "session_id": "w1", "cash": 1_000_000_000, "reserved_cash": 0, "positions": []}
    c.submit_limit_order.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="o1", status="OPEN", remaining_qty=100)
    return c


@pytest.fixture
def manager(client, clock):
    risk = RiskLimits(500, 200, "NOVA", "ELXR")
    return OrderManager(client, risk, session_id="w1", clock=clock)


# --- P0-01: a persistent signal must not stack orders --------------------

def test_persistent_signal_submits_once(manager, client):
    for _ in range(10):
        manager.submit_intent("NOVA", "buy", 100, 4250)
    assert client.submit_limit_order.call_count == 1


def test_symbol_unblocks_once_the_order_is_terminal(manager, client, clock):
    manager.submit_intent("NOVA", "buy", 100, 4250)
    client.get_orders.return_value = [
        {"id": "o1", "status": "FILLED", "remaining_qty": 0, "idempotency_key": "k"}]
    clock.advance(2)
    manager.poll()
    manager.submit_intent("NOVA", "buy", 100, 4250)
    assert client.submit_limit_order.call_count == 2


# --- P0-02: pending exposure counts against the cap ---------------------

def test_cap_counts_pending_orders(client, clock):
    """Cap 500 at 100 a time: five resting buys, then refusal — even at 0 filled."""
    manager = OrderManager(client, RiskLimits(500, 200, "NOVA", "ELXR"),
                           session_id="w1", max_active_per_symbol=99, clock=clock)
    submitted = []

    def submit(symbol, side, quantity, price, key):
        submitted.append(key)
        return SubmitResult(Outcome.ACKNOWLEDGED, key, order_id=f"o{len(submitted)}",
                            status="OPEN", remaining_qty=quantity)

    client.submit_limit_order.side_effect = submit
    for _ in range(8):
        manager.submit_intent("NOVA", "buy", 100, 4250)
    assert len(submitted) == 5
    assert manager.projected_position("NOVA") == 500


def test_unknown_intent_counts_against_the_cap(manager, client):
    client.submit_limit_order.return_value = SubmitResult(Outcome.UNKNOWN, "k1")
    manager.submit_intent("NOVA", "buy", 100, 4250)
    assert manager.projected_position("NOVA") == 100


# --- long-only: reserved_qty must not be sold twice ---------------------

def test_reserved_shares_are_not_available_to_sell(manager, client):
    client.get_portfolio.return_value = {
        "session_id": "w1", "cash": 0, "reserved_cash": 0,
        "positions": [{"symbol": "NOVA", "quantity": 100, "reserved_qty": 100}]}
    manager.refresh_positions(force=True)
    assert manager.available_to_sell("NOVA") == 0
    assert manager.submit_intent("NOVA", "sell", 100, 4250) is None
    client.submit_limit_order.assert_not_called()


def test_unreserved_shares_are_sellable(manager, client):
    client.get_portfolio.return_value = {
        "session_id": "w1", "cash": 0, "reserved_cash": 0,
        "positions": [{"symbol": "NOVA", "quantity": 100, "reserved_qty": 0}]}
    manager.refresh_positions(force=True)
    assert manager.available_to_sell("NOVA") == 100
    assert manager.submit_intent("NOVA", "sell", 100, 4250) is not None


# --- P0-03: 504 reconciliation ------------------------------------------

def test_unknown_blocks_symbol_until_reconciled(manager, client, clock):
    client.submit_limit_order.return_value = SubmitResult(Outcome.UNKNOWN, "k1")
    result = manager.submit_intent("NOVA", "buy", 100, 4250)
    assert result.outcome is Outcome.UNKNOWN
    key = result.idempotency_key

    # Blocked while unresolved, and never resubmitted.
    assert manager.submit_intent("NOVA", "buy", 100, 4250) is None
    assert client.submit_limit_order.call_count == 1

    # A read inside the settle window proves nothing.
    manager.poll(force=True)
    assert manager.has_active("NOVA")

    # It did land: adopt it rather than placing a second order.
    clock.advance(3)
    client.get_orders.return_value = [
        {"id": "o9", "status": "OPEN", "symbol": "NOVA", "side": "buy",
         "quantity": 100, "price": 4250, "remaining_qty": 100, "idempotency_key": key}]
    manager.poll(force=True)
    assert manager.projected_position("NOVA") == 100
    assert client.submit_limit_order.call_count == 1


def test_not_landed_needs_two_absent_reads(manager, client, clock):
    client.submit_limit_order.return_value = SubmitResult(Outcome.UNKNOWN, "k1")
    manager.submit_intent("NOVA", "buy", 100, 4250)
    clock.advance(3)

    manager.poll(force=True)
    assert manager.has_active("NOVA")          # one absent read is not proof
    manager.poll(force=True)
    assert not manager.has_active("NOVA")      # confirmed: it never landed

    client.submit_limit_order.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k2", order_id="o2", status="OPEN", remaining_qty=100)
    assert manager.submit_intent("NOVA", "buy", 100, 4250) is not None


def test_each_intent_gets_a_distinct_key(manager, client):
    keys = []
    client.submit_limit_order.side_effect = (
        lambda s, sd, q, p, k: keys.append(k) or SubmitResult(Outcome.REJECTED, k,
                                                              code="PRICE_OUT_OF_BAND"))
    manager.submit_intent("NOVA", "buy", 100, 4250)
    manager.submit_intent("NOVA", "buy", 100, 4300)
    assert len(set(keys)) == 2


# --- P0-04: typed results, nothing logged as submitted on failure -------

@pytest.mark.parametrize("status,payload,headers,expected", [
    (201, ack(), {}, Outcome.ACKNOWLEDGED),
    (422, {"status": "error", "error": {"code": "PRICE_OUT_OF_BAND"}}, {}, Outcome.REJECTED),
    (402, {"status": "error", "error": {"code": "INSUFFICIENT_FUNDS"}}, {}, Outcome.REJECTED),
    (409, {"status": "error", "error": {"code": "ORDER_DUPLICATE"}}, {}, Outcome.REJECTED),
    (503, {"status": "error", "error": {"code": "MARKET_HALTED"}}, {}, Outcome.REJECTED),
    (429, {"status": "error", "error": {"code": "RATE_LIMIT_EXCEEDED"}},
     {"Retry-After": "12"}, Outcome.RATE_LIMITED),
    (500, {"status": "error", "error": {"code": "INTERNAL_ERROR"}}, {}, Outcome.RETRYABLE),
    (504, {"status": "error", "error": {"code": "GATEWAY_TIMEOUT"}}, {}, Outcome.UNKNOWN),
])
def test_submit_classifies_every_documented_status(status, payload, headers, expected):
    c = ExchangeClient("http://test", "test")
    c.session = Mock()
    c.session.post = Mock(return_value=response(status, payload, headers))
    result = c.submit_limit_order("NOVA", "buy", 100, 4250, "k1")
    assert result.outcome is expected
    assert result.idempotency_key == "k1"


def test_submit_never_raises_on_timeout():
    c = ExchangeClient("http://test", "test")
    c.session = Mock()
    c.session.post = Mock(side_effect=requests.Timeout())
    result = c.submit_limit_order("NOVA", "buy", 100, 4250, "k1")
    assert result.outcome is Outcome.UNKNOWN
    assert result.idempotency_key == "k1"


def test_immediate_fill_is_booked_from_the_submit_response():
    c = ExchangeClient("http://test", "test")
    c.session = Mock()
    c.session.post = Mock(return_value=response(
        201, ack(status="FILLED", remaining=0, filled=100)))
    result = c.submit_limit_order("NOVA", "buy", 100, 4250, "k1")
    assert result.acknowledged and result.filled_qty == 100 and result.status == "FILLED"


def test_rate_limit_carries_retry_after():
    c = ExchangeClient("http://test", "test")
    c.session = Mock()
    c.session.post = Mock(return_value=response(
        429, {"status": "error", "error": {"code": "RATE_LIMIT_EXCEEDED"}},
        {"Retry-After": "12", "X-RateLimit-Remaining": "0"}))
    result = c.submit_limit_order("NOVA", "buy", 100, 4250, "k1")
    assert result.retry_after == 12.0 and result.rate_remaining == 0


def test_rejection_leaves_no_tracked_state(manager, client):
    client.submit_limit_order.return_value = SubmitResult(
        Outcome.REJECTED, "k1", code="PRICE_OUT_OF_BAND")
    manager.submit_intent("NOVA", "buy", 100, 4250)
    assert manager.projected_position("NOVA") == 0
    assert not manager.has_active("NOVA")


# --- P0-09 / Fix #6: portfolio gaps -------------------------------------

def test_absent_symbol_is_flat():
    c = ExchangeClient("http://test", "test")
    c.get_portfolio = Mock(return_value={"positions": []})
    assert c.get_positions("ELXR") == 0


def test_malformed_position_entry_does_not_raise():
    c = ExchangeClient("http://test", "test")
    c.get_portfolio = Mock(return_value={"positions": [
        {"symbol": "NOVA"}, {"quantity": 5}, None, {"symbol": "ELXR", "quantity": "x"}]})
    assert c.get_positions("NOVA") == 0
    assert c.get_positions("ELXR") == 0


def test_portfolio_read_failure_does_not_raise_into_the_caller(manager, client):
    client.get_portfolio.side_effect = requests.ConnectionError()
    manager.refresh_positions(force=True)
    assert manager.projected_position("NOVA") == 0


# --- guards -------------------------------------------------------------

def test_blocked_manager_refuses_everything(manager, client):
    manager.block_new_intents()
    assert manager.submit_intent("NOVA", "buy", 100, 4250) is None
    client.submit_limit_order.assert_not_called()


def test_non_positive_size_is_never_sent(manager, client):
    assert manager.submit_intent("NOVA", "buy", 0, 4250) is None
    assert manager.submit_intent("NOVA", "buy", 100, 0) is None
    client.submit_limit_order.assert_not_called()


def test_insufficient_unreserved_cash_is_not_sent(manager, client):
    client.get_portfolio.return_value = {
        "session_id": "w1", "cash": 500_000, "reserved_cash": 490_000, "positions": []}
    manager.refresh_positions(force=True)
    assert manager.submit_intent("NOVA", "buy", 100, 4250) is None
    client.submit_limit_order.assert_not_called()


def test_order_rate_limit_defers_instead_of_being_refused(client, clock):
    manager = OrderManager(client, RiskLimits(10_000, 200, "NOVA", "ELXR"),
                           session_id="w1", max_active_per_symbol=99,
                           order_limiter=RollingWindow(3, 30.0, clock=clock),
                           clock=clock)
    client.submit_limit_order.side_effect = lambda s, sd, q, p, k: SubmitResult(
        Outcome.ACKNOWLEDGED, k, order_id=k, status="OPEN", remaining_qty=q)
    for _ in range(6):
        manager.submit_intent("NOVA", "buy", 100, 4250)
    assert client.submit_limit_order.call_count == 3
    clock.advance(31)
    manager.submit_intent("NOVA", "buy", 100, 4250)
    assert client.submit_limit_order.call_count == 4


# --- shutdown / session surface P3 consumes -----------------------------

def test_cancel_all_tolerates_already_filled(manager, client):
    manager.submit_intent("NOVA", "buy", 100, 4250)
    client.get_orders.return_value = [{"id": "o1", "status": "OPEN"},
                                      {"id": "o2", "status": "OPEN"}]
    client.cancel_order.side_effect = [
        Mock(outcome=CancelOutcome.CANCELLED, settled=True, code=None, rate_remaining=None,
             retry_after=None),
        Mock(outcome=CancelOutcome.NOT_CANCELLABLE, settled=True,
             code="ORDER_NOT_CANCELLABLE", rate_remaining=None, retry_after=None),
    ]
    cancelled, unresolved = manager.cancel_all()
    assert cancelled == ["o1"] and unresolved == []
    assert manager.blocked


def test_cancel_all_reports_unresolved(manager, client):
    client.get_orders.return_value = [{"id": "o1", "status": "OPEN"}]
    client.cancel_order.return_value = Mock(
        outcome=CancelOutcome.UNKNOWN, settled=False, code="GATEWAY_TIMEOUT",
        rate_remaining=None, retry_after=None)
    cancelled, unresolved = manager.cancel_all()
    assert cancelled == [] and unresolved == ["o1"]


def test_cancel_all_blocks_before_cancelling(manager, client):
    """The block must be visible to a concurrent submit from the first cancel on."""
    client.get_orders.return_value = [{"id": "o1", "status": "OPEN"}]
    seen = {}

    def cancel(order_id):
        seen["blocked"] = manager.blocked
        return Mock(outcome=CancelOutcome.CANCELLED, settled=True, code=None,
                    rate_remaining=None, retry_after=None)

    client.cancel_order.side_effect = cancel
    manager.cancel_all()
    assert seen["blocked"] is True


def test_session_reset_drops_all_window_state(manager, client):
    manager.submit_intent("NOVA", "buy", 100, 4250)
    assert manager.has_active("NOVA")
    manager.reset_for_session("w2")
    assert not manager.has_active("NOVA")
    assert manager.projected_position("NOVA") == 0


def test_cancel_result_settles_on_not_cancellable():
    c = ExchangeClient("http://test", "test")
    c.session = Mock()
    c.session.delete = Mock(return_value=response(
        400, {"status": "error", "error": {"code": "ORDER_NOT_CANCELLABLE"}}))
    result = c.cancel_order("o1")
    assert result.outcome is CancelOutcome.NOT_CANCELLABLE and result.settled
