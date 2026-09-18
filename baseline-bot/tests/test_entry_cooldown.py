"""Entry cooldown: one strategy BUY per symbol per `entry_cooldown_seconds`.

Why this exists (measured on the sandbox, 2026-09-18): a buy priced at the ask
fills inside the submit call, so OrderManager's one-live-order gate never sees
it, and the next tick (~250 ms later) re-fires the same still-true momentum
signal. The exchange showed 25 bursts of 3 to 10 same-side orders within 2 s.
The cooldown holds strategy buys only; every risk-reducing path stays open.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from exchange.results import Outcome, SubmitResult
from main import RUNNING, build_strategies, make_price_handler


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def acknowledged(symbol, side, quantity, price, **_):
    return SubmitResult(Outcome.ACKNOWLEDGED, "k", order_id="o1", status="FILLED",
                        remaining_qty=0)


def wired(config, cooldown, clock, sellable=0, submit=acknowledged):
    orders = Mock()
    orders.available_to_sell.return_value = sellable
    orders.avg_cost.return_value = 0
    orders.submit_intent.side_effect = submit
    strategies = build_strategies(config)
    on_price = make_price_handler(
        strategies, orders, {"session_open": True, "session_id": "w1"},
        {"value": RUNNING}, entry_cooldown=cooldown, clock=clock)
    return on_price, orders, strategies


def rising(on_price, strat, ticks):
    """Feed `ticks` prints of a clear uptrend; momentum stays above threshold."""
    for i in range(ticks):
        on_price({"symbol": strat.symbol, "last_price": 10_000 + i * 100})


def buys(orders):
    return [c for c in orders.submit_intent.call_args_list if c.args[1] == "buy"]


def sells(orders):
    return [c for c in orders.submit_intent.call_args_list if c.args[1] == "sell"]


def test_without_a_cooldown_every_tick_re_fires_the_signal(config):
    """Documents the burst: the gap this feature closes."""
    on_price, orders, strategies = wired(config, cooldown=0, clock=FakeClock())
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback + 9)
    assert len(buys(orders)) == 10


def test_cooldown_allows_one_buy_per_symbol_per_window(config):
    clock = FakeClock()
    on_price, orders, strategies = wired(config, cooldown=15, clock=clock)
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback + 9)
    assert len(buys(orders)) == 1


def test_cooldown_expires(config):
    clock = FakeClock()
    on_price, orders, strategies = wired(config, cooldown=15, clock=clock)
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback + 2)
    assert len(buys(orders)) == 1
    clock.advance(15)
    on_price({"symbol": "NOVA", "last_price": 20_000})
    assert len(buys(orders)) == 2


def test_cooldown_is_per_symbol(config):
    clock = FakeClock()
    on_price, orders, strategies = wired(config, cooldown=15, clock=clock)
    nova, crvx = strategies["NOVA"], strategies["CRVX"]
    rising(on_price, nova, nova.lookback + 1)
    rising(on_price, crvx, crvx.lookback + 1)
    assert {c.args[0] for c in buys(orders)} == {"NOVA", "CRVX"}
    assert len(buys(orders)) == 2


def test_a_refused_buy_does_not_start_the_cooldown(config):
    """Only an acknowledged order spends the window; a local refusal (slot
    busy, cap, cash) is retried on the next tick as before."""
    clock = FakeClock()
    on_price, orders, strategies = wired(config, cooldown=15, clock=clock,
                                         submit=lambda *a, **k: None)
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback + 4)
    assert len(buys(orders)) == 5


def test_sells_are_never_held_back(config):
    """A risk-reducing order inside the cooldown goes straight through."""
    clock = FakeClock()
    on_price, orders, strategies = wired(config, cooldown=15, clock=clock, sellable=300)
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback + 1)
    assert len(buys(orders)) == 1
    # Still inside the window: a collapse turns momentum negative -> sell.
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 12_000 - i * 300})
    assert sells(orders), "the sell was held back by the entry cooldown"
    assert len(buys(orders)) == 1


def test_take_profit_exit_is_never_held_back(config):
    """The ExitPolicy path runs before the strategy and ignores the cooldown."""
    from risk.exits import ExitPolicy

    clock = FakeClock()
    orders = Mock()
    orders.available_to_sell.return_value = 200
    orders.avg_cost.return_value = 10_000
    orders.submit_intent.side_effect = acknowledged
    strategies = build_strategies(config)
    exits = ExitPolicy({"NOVA": {"trailing_take_profit": {"arm_pct": 0.04, "trail_pct": 0.015}}})
    on_price = make_price_handler(
        strategies, orders, {"session_open": True, "session_id": "w1"},
        {"value": RUNNING}, exits, entry_cooldown=15, clock=clock)
    on_price({"symbol": "NOVA", "last_price": 13_000})   # arm, set the peak
    on_price({"symbol": "NOVA", "last_price": 12_700})   # give back 2.3%
    symbol, side, quantity, _ = orders.submit_intent.call_args.args
    assert (symbol, side, quantity) == ("NOVA", "sell", 200)


def test_intents_carry_their_strategy_as_source(config):
    """OrderManager and the risk model key regime factors off `source`."""
    on_price, orders, strategies = wired(config, cooldown=0, clock=FakeClock())
    nova = strategies["NOVA"]
    rising(on_price, nova, nova.lookback)
    assert orders.submit_intent.call_args.kwargs["source"] == "momentum"
