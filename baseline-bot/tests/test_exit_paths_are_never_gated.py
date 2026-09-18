"""Every risk-reducing path must stay open, in every market state.

Four layers can now refuse an order: the spread gate, the regime policy, the
correlated-group cap, and the position cap. Each of them exists to stop us
ADDING risk. If any of them can also block a sell, the condition that triggered
it traps the position — a wide book is exactly when you most want out, and a
SHOCK label is exactly when a stop should fire.

These tests exist because that invariant is easy to break by accident: the
spread gate was originally placed above the buy/sell branches in all three
strategies, so a wide book silently blocked exits too.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from main import build_strategies
from strategies.regime_policy import Regime, RegimePolicy, StaticRegimeClassifier


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def wide_book():
    """An order book whose spread is far outside every configured cap."""
    book = Mock()
    book.spread_bps.return_value = 10_000.0
    book.best_bid.return_value = 9_000
    book.best_ask.return_value = 11_000
    return book


# --- the spread gate ----------------------------------------------------

def test_momentum_can_still_sell_on_a_wide_book(config):
    strat = build_strategies(config)["NOVA"]
    strat.orderbook = wide_book()
    strat.max_spread_bps = 50.0
    for i in range(strat.lookback):                       # clear downtrend
        strat.on_price(15_000 - i * 200)
    signal = strat.signal(300)
    assert signal is not None and signal.side == "sell"


def test_momentum_cannot_buy_on_a_wide_book(config):
    strat = build_strategies(config)["NOVA"]
    strat.orderbook = wide_book()
    strat.max_spread_bps = 50.0
    for i in range(strat.lookback):                       # clear uptrend
        strat.on_price(10_000 + i * 200)
    assert strat.signal(0) is None


def test_mean_reversion_can_still_sell_on_a_wide_book(config):
    strat = build_strategies(config)["ELXR"]
    strat.orderbook = wide_book()
    strat.max_spread_bps = 50.0
    for _ in range(strat.lookback - 1):
        strat.on_price(10_000)
    strat.on_price(10_500)                                # well above the mean
    signal = strat.signal(200)
    assert signal is not None and signal.side == "sell"


def test_mean_reversion_cannot_buy_on_a_wide_book(config):
    strat = build_strategies(config)["ELXR"]
    strat.orderbook = wide_book()
    strat.max_spread_bps = 50.0
    for _ in range(strat.lookback - 1):
        strat.on_price(10_000)
    strat.on_price(9_500)                                 # well below the mean
    assert strat.signal(0) is None


def test_follower_can_still_sell_on_a_wide_book(config):
    strategies = build_strategies(config)
    axon, nova = strategies["AXON"], strategies["NOVA"]
    axon.orderbook = wide_book()
    axon.max_spread_bps = 50.0
    for _ in range(axon.breakout_samples - 1):            # leader breaks out
        nova.on_price(10_000)
        axon.on_price(20_000)
    nova.on_price(10_500)
    axon.on_price(20_000)
    for _ in range(axon.arm_samples + 1):                 # then rolls over
        nova.on_price(10_300)
        axon.on_price(20_000)
    signal = axon.signal(200)
    assert signal is not None and signal.side == "sell"


# --- the regime policy --------------------------------------------------

@pytest.mark.parametrize("regime", list(Regime.ALL))
def test_no_regime_can_block_a_sell(config, regime):
    from strategies.base import Signal

    policy = RegimePolicy({**config["regime"], "enabled": True})
    strategies = build_strategies(config)
    for symbol, strat in strategies.items():
        sell = Signal("sell", 100, 10_000)
        out = policy.apply(symbol, strat, sell, StaticRegimeClassifier(regime))
        assert out is sell, f"{symbol} sell blocked in {regime}"
        assert out.quantity == 100


# --- the correlated-group cap -------------------------------------------

def test_group_cap_cannot_block_the_sell_that_unwinds_a_breach(config):
    """A breach must be unwindable by the bot, not only by hand."""
    from exchange.results import Outcome, SubmitResult
    from orders.manager import OrderManager
    from risk.limits import RiskLimits

    client = Mock()
    client.get_orders.return_value = []
    client.get_portfolio.return_value = {
        "session_id": "w1", "cash": 10**9, "reserved_cash": 0,
        # Large enough to breach the configured group cap at these marks.
        "positions": [
            {"symbol": "NOVA", "quantity": 5_000, "reserved_qty": 0,
             "avg_cost": 20_000, "last_price": 20_000},
            {"symbol": "AXON", "quantity": 5_000, "reserved_qty": 0,
             "avg_cost": 20_000, "last_price": 20_000}]}
    client.submit_limit_order.side_effect = lambda s, side, q, p, k: SubmitResult(
        Outcome.ACKNOWLEDGED, k, order_id=k, status="OPEN", remaining_qty=q)

    risk = RiskLimits(
        config["risk"]["max_position_primary"],
        config["risk"]["max_position_secondary"], "NOVA", "ELXR",
        extra_limits=config["risk"]["max_position"],
        correlated_groups=config["risk"]["correlated_groups"])
    manager = OrderManager(client, risk, session_id="w1")
    manager.refresh_positions(force=True)

    cap = risk.max_group_notional(risk.group_for("NOVA"))
    assert manager.projected_notional(["NOVA", "AXON"]) > cap   # in breach
    assert manager.submit_intent("AXON", "sell", 500, 20_000) is not None
