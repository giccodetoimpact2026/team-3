"""Stop-loss and take-profit tests. Prices are integer cents.

The invariant: an exit overrides the strategy. If it can be vetoed by a strategy
signal it is not an exit.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from main import RUNNING, build_strategies, make_price_handler
from risk.exits import ExitPolicy, ExitSignal


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


# =========================================================================
# Stop-loss — every instrument
# =========================================================================

@pytest.fixture
def stop_only():
    return ExitPolicy({"NOVA": {"stop_loss_pct": 0.03}}, limit_offset_bps=25)


def test_stop_fires_below_cost(stop_only):
    exit_ = stop_only.check("NOVA", avg_cost=10_000, mark=9_650, sellable=100)
    assert exit_ is not None
    assert exit_.reason == ExitSignal.STOP_LOSS and exit_.side == "sell"
    assert exit_.limit_price < 9_650          # concedes the spread to get out


def test_stop_does_not_fire_inside_the_band(stop_only):
    assert stop_only.check("NOVA", 10_000, 9_750, 100) is None


def test_stop_fires_exactly_at_the_level(stop_only):
    assert stop_only.check("NOVA", 10_000, 9_700, 100) is not None


def test_stop_closes_the_whole_unreserved_position(stop_only):
    """An exit that unwinds a clip at a time leaves the rest exposed."""
    assert stop_only.check("NOVA", 10_000, 9_000, 500).quantity == 500


def test_limit_price_never_goes_nonpositive():
    """A collapse to 1 cent must still produce a submittable price."""
    policy = ExitPolicy({"NOVA": {"stop_loss_pct": 0.03}}, limit_offset_bps=10_000)
    assert policy.check("NOVA", 10_000, 1, 100).limit_price >= 1


# --- long-only and flat-position guards ---------------------------------

def test_nothing_to_exit_when_flat(stop_only):
    assert stop_only.check("NOVA", 10_000, 9_000, sellable=0) is None


def test_nothing_to_exit_without_a_cost_basis(stop_only):
    """avg_cost 0 is the exchange's flat marker — never guess a basis."""
    assert stop_only.check("NOVA", avg_cost=0, mark=9_000, sellable=100) is None


def test_sell_never_exceeds_sellable(stop_only):
    """Reserved shares are already committed to a resting sell."""
    assert stop_only.check("NOVA", 10_000, 9_000, sellable=30).quantity == 30


def test_symbol_without_levels_has_no_exits(stop_only):
    assert stop_only.check("DRFT", 10_000, 1_000, 100) is None


# =========================================================================
# Trailing take-profit — the momentum names
# =========================================================================

@pytest.fixture
def trailing():
    return ExitPolicy({"NOVA": {
        "stop_loss_pct": 0.03,
        "trailing_take_profit": {"arm_pct": 0.04, "trail_pct": 0.015},
    }})


def test_trail_does_not_arm_before_the_arm_level(trailing):
    """Up 2%, then gives it all back: below arm_pct, so no trailing exit."""
    assert trailing.check("NOVA", 10_000, 10_200, 100) is None
    assert trailing.check("NOVA", 10_000, 10_100, 100) is None


def test_winner_is_allowed_to_run(trailing):
    """The whole point: a fixed target would have closed this at +4%."""
    for mark in (10_400, 10_800, 11_500, 12_000, 13_000):
        assert trailing.check("NOVA", 10_000, mark, 100) is None
    assert trailing.peak("NOVA") == 13_000


def test_trail_fires_on_give_back_from_the_peak(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)          # arm and set the peak
    exit_ = trailing.check("NOVA", 10_000, 12_700, 100)  # -2.3% from 13_000
    assert exit_ is not None
    assert exit_.reason == ExitSignal.TRAILING_TAKE_PROFIT
    assert exit_.peak == 13_000
    assert exit_.move_pct > 0                            # still a winner


def test_shallow_pullback_from_the_peak_holds(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)
    assert trailing.check("NOVA", 10_000, 12_900, 100) is None   # -0.8%


def test_peak_is_a_high_water_mark(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)
    trailing.check("NOVA", 10_000, 12_900, 100)
    assert trailing.peak("NOVA") == 13_000     # a lower print does not lower it


def test_peak_clears_when_the_position_goes_flat(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)
    trailing.check("NOVA", avg_cost=0, mark=13_000, sellable=0)
    assert trailing.peak("NOVA") is None


def test_peak_clears_after_an_exit_fires(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)
    assert trailing.check("NOVA", 10_000, 12_700, 100) is not None
    assert trailing.peak("NOVA") is None       # next position starts clean


def test_stop_beats_the_trail_when_both_could_fire(trailing):
    """A collapse from a peak straight through the stop is a stop, not a trail."""
    trailing.check("NOVA", 10_000, 13_000, 100)
    assert trailing.check("NOVA", 10_000, 9_000, 100).reason == ExitSignal.STOP_LOSS


def test_reset_clears_all_peaks(trailing):
    trailing.check("NOVA", 10_000, 13_000, 100)
    trailing.reset()
    assert trailing.peak("NOVA") is None


# =========================================================================
# Fixed take-profit — the spike-and-revert name
# =========================================================================

@pytest.fixture
def fixed():
    return ExitPolicy({"DRFT": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04}})


def test_fixed_target_fires_at_the_level(fixed):
    exit_ = fixed.check("DRFT", 10_000, 10_400, 100)
    assert exit_ is not None and exit_.reason == ExitSignal.TAKE_PROFIT


def test_fixed_target_does_not_fire_below_the_level(fixed):
    assert fixed.check("DRFT", 10_000, 10_300, 100) is None


# =========================================================================
# Config validation
# =========================================================================

@pytest.mark.parametrize("bad", [0, 1, 1.5])
def test_rejects_nonsense_stop(bad):
    with pytest.raises(ValueError, match="stop_loss_pct"):
        ExitPolicy({"NOVA": {"stop_loss_pct": bad}})


def test_rejects_half_configured_trail():
    with pytest.raises(ValueError, match="both arm_pct and trail_pct"):
        ExitPolicy({"NOVA": {"trailing_take_profit": {"arm_pct": 0.04}}})


def test_rejects_fixed_and_trailing_together():
    """A fixed target always fires before a trail can arm and run."""
    with pytest.raises(ValueError, match="not both"):
        ExitPolicy({"NOVA": {
            "take_profit_pct": 0.05,
            "trailing_take_profit": {"arm_pct": 0.04, "trail_pct": 0.015}}})


def test_sign_is_normalised():
    """A stop written as -0.03 means the same 3% as 0.03."""
    assert ExitPolicy({"NOVA": {"stop_loss_pct": -0.03}}).levels("NOVA")[
        "stop_loss_pct"] == 0.03


def test_symbol_with_no_legs_is_not_registered():
    assert ExitPolicy({"NOVA": {}}).levels() == {}


# =========================================================================
# Visibility (Priya: levels must be readable, not buried)
# =========================================================================

def test_levels_are_introspectable(trailing):
    assert trailing.levels("NOVA")["stop_loss_pct"] == 0.03
    assert "NOVA" in trailing.levels()


def test_trigger_prices_are_absolute_cents(fixed):
    prices = fixed.trigger_prices("DRFT", 10_000)
    assert prices["stop_loss"] == 9_800
    assert prices["take_profit"] == 10_400


def test_trailing_trigger_price_appears_once_armed(trailing):
    assert trailing.trigger_prices("NOVA", 10_000)["trailing_stop"] is None
    trailing.check("NOVA", 10_000, 13_000, 100)
    assert trailing.trigger_prices("NOVA", 10_000)["trailing_stop"] == 12_805


def test_trigger_prices_undefined_when_flat(trailing):
    assert trailing.trigger_prices("NOVA", 0) == {
        "stop_loss": None, "take_profit": None, "trailing_stop": None}


# =========================================================================
# Integration: exits override the strategy
# =========================================================================

def wired(config, sellable, avg_cost):
    orders = Mock()
    orders.available_to_sell.return_value = sellable
    orders.avg_cost.return_value = avg_cost
    strategies = build_strategies(config)
    exits = ExitPolicy(config["risk"]["exits"])
    on_price = make_price_handler(
        strategies, orders, {"session_open": True, "session_id": "w1"},
        {"value": RUNNING}, exits)
    return on_price, orders, strategies, exits


def test_stop_overrides_a_momentum_buy(config):
    """The case that matters: momentum wants to add, the stop says get out."""
    on_price, orders, strategies, _ = wired(config, sellable=300, avg_cost=10_000)
    nova = strategies["NOVA"]
    # Rising series -> momentum buy, but every print is far below cost basis.
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 5_000 + i * 100})
    assert {c.args[1] for c in orders.submit_intent.call_args_list} == {"sell"}


def test_exit_sizes_off_sellable_through_the_handler(config):
    on_price, orders, _, _ = wired(config, sellable=40, avg_cost=10_000)
    on_price({"symbol": "NOVA", "last_price": 9_000})
    _, side, quantity, _ = orders.submit_intent.call_args.args
    assert side == "sell" and quantity == 40


def test_no_exit_leaves_the_strategy_in_charge(config):
    on_price, orders, strategies, _ = wired(config, sellable=0, avg_cost=0)
    nova = strategies["NOVA"]
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 10_000 + i * 100})
    _, side, _, _ = orders.submit_intent.call_args.args
    assert side == "buy"


def test_trailing_exit_reaches_the_order_manager(config):
    on_price, orders, _, _ = wired(config, sellable=200, avg_cost=10_000)
    on_price({"symbol": "NOVA", "last_price": 13_000})   # arm, set the peak
    orders.submit_intent.reset_mock()
    on_price({"symbol": "NOVA", "last_price": 12_700})   # give back 2.3%
    symbol, side, quantity, _ = orders.submit_intent.call_args.args
    assert (symbol, side, quantity) == ("NOVA", "sell", 200)


# =========================================================================
# The real config
# =========================================================================

def test_every_active_instrument_has_a_stop(config):
    """Priya's ask was per-instrument stop-losses, so none may be missing."""
    exits = ExitPolicy(config["risk"]["exits"])
    for symbol in build_strategies(config):
        assert exits.levels(symbol).get("stop_loss_pct"), f"{symbol} has no stop"


def test_momentum_names_trail_and_mean_reversion_does_not(config):
    """Take-profit shape follows the strategy, not a blanket rule."""
    exits = ExitPolicy(config["risk"]["exits"])
    strategies = build_strategies(config)
    for symbol in ("NOVA", "CRVX", "AXON"):
        assert exits.levels(symbol)["trail_pct"] is not None, symbol
        assert exits.levels(symbol)["take_profit_pct"] is None, symbol
    # ELXR sells when price runs above its mean — that IS its take-profit.
    assert exits.levels("ELXR")["trail_pct"] is None
    assert exits.levels("ELXR")["take_profit_pct"] is None
    assert type(strategies["ELXR"]).__name__ == "MeanReversionStrategy"
    # DRFT spikes then drops, so it takes a fixed target rather than a trail.
    assert exits.levels("DRFT")["take_profit_pct"] is not None
    assert exits.levels("DRFT")["trail_pct"] is None
