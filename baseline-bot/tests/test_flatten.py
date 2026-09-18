"""Shared exit path: sell a whole position into the resting bids, used by the
crash guard and the stop-loss."""
from unittest.mock import Mock

import pytest

from exchange.results import Outcome, SubmitResult
from risk.flatten import Flattener, StopLossResponder
from risk.model import StopTrigger


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def bids(*levels):
    return [{"price": p, "quantity": q, "team_name": "BOT"} for p, q in levels]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client():
    c = Mock()
    c.get_orderbook.return_value = {"symbol": "NOVA", "bids": bids((8200, 500), (8150, 600)),
                                    "asks": []}
    return c


@pytest.fixture
def orders():
    o = Mock()
    o.flatten.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="d1", status="FILLED",
        remaining_qty=0, filled_qty=1000)
    o.has_active.return_value = False
    return o


def test_flatten_prices_at_the_bid_level_that_absorbs_the_position(client, orders, clock):
    f = Flattener(client, orders, floor_pct=0.03, clock=clock)
    f.flatten("NOVA", 8200)
    symbol, pricer = orders.flatten.call_args.args
    assert symbol == "NOVA"
    assert pricer(1000) == 8150
    assert pricer(400) == 8200


def test_flatten_floor_is_relative_to_the_trigger_price(client, orders, clock):
    client.get_orderbook.return_value = {"bids": bids((8200, 10), (100, 5000)), "asks": []}
    f = Flattener(client, orders, floor_pct=0.03, clock=clock)
    f.flatten("NOVA", 8000)
    _, pricer = orders.flatten.call_args.args
    assert pricer(1000) == 7760


def test_flatten_sells_at_the_floor_when_the_book_read_fails(client, orders, clock):
    client.get_orderbook.side_effect = RuntimeError("boom")
    f = Flattener(client, orders, floor_pct=0.03, clock=clock)
    f.flatten("NOVA", 8000)
    _, pricer = orders.flatten.call_args.args
    assert pricer(1000) == 7760


def test_flatten_reprices_once_if_the_order_is_still_resting(client, orders, clock):
    orders.flatten.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="d1", status="OPEN", remaining_qty=1000)
    orders.has_active.return_value = True
    f = Flattener(client, orders, floor_pct=0.03, reprice_after=5.0, clock=clock)
    f.flatten("NOVA", 8000)
    f.on_price("NOVA", 7900)
    assert orders.flatten.call_count == 1
    clock.advance(6)
    f.on_price("NOVA", 7900)
    assert orders.flatten.call_count == 2
    clock.advance(6)
    f.on_price("NOVA", 7800)
    assert orders.flatten.call_count == 2


def test_flatten_records_the_last_exit_per_symbol(client, orders, clock):
    f = Flattener(client, orders, clock=clock)
    f.flatten("NOVA", 8000)
    assert f.status()["NOVA"] == {"at": 1000.0, "order_id": "d1", "status": "FILLED",
                                  "repriced": False}


def test_stop_loss_responder_flattens_only_on_a_trigger(client, orders, clock):
    model = Mock()
    model.on_price.side_effect = [None, StopTrigger("NOVA", 8990, 9000, 10000, 0.10), None]
    responder = StopLossResponder(model, Flattener(client, orders, clock=clock))
    responder.on_price("NOVA", 9000)
    assert not orders.flatten.called
    responder.on_price("NOVA", 8990)
    orders.flatten.assert_called_once()
    responder.on_price("NOVA", 8990)
    assert orders.flatten.call_count == 1
