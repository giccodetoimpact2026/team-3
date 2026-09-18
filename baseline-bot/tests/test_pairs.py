"""Tests for the AXON-off-NOVA leader-follower strategy. Prices are integer cents."""
from pathlib import Path

import pytest
import yaml

from main import build_strategies
from strategies.momentum import MomentumStrategy
from strategies.pairs import PairsFollowStrategy

LEADER_CFG = {"type": "momentum", "lookback_samples": 25,
              "momentum_threshold": 0.02, "trade_quantity": 50,
              "limit_offset_bps": 5}

FOLLOWER_CFG = {"lookback_samples": 5, "leader_breakout_samples": 10,
                "breakout_arm_samples": 5, "exit_drawdown": 0.004,
                "trade_quantity": 50, "limit_offset_bps": 5}


def build(follower_overrides=None, leader_overrides=None):
    leader = MomentumStrategy("NOVA", {**LEADER_CFG, **(leader_overrides or {})})
    follower = PairsFollowStrategy(
        "AXON", {**FOLLOWER_CFG, **(follower_overrides or {})}, leader)
    return leader, follower


def feed(leader, follower, leader_prices, follower_price=20_000):
    """Push leader prices, ticking the follower once per leader sample."""
    for price in leader_prices:
        leader.on_price(price)
        follower.on_price(follower_price)


# --- readiness ----------------------------------------------------------

def test_no_signal_before_leader_window_fills():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9)
    assert not follower.ready()
    assert follower.signal(0) is None


def test_no_signal_without_own_price():
    leader, follower = build()
    for price in range(10_000, 11_000, 100):
        leader.on_price(price)
    assert not follower.ready()          # leader ready, follower has no price
    assert follower.signal(0) is None


# --- entry --------------------------------------------------------------

def test_buys_when_leader_breaks_new_high():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    sig = follower.signal(0)
    assert sig is not None
    assert sig.side == "buy" and sig.quantity == 50
    assert sig.limit_price >= 20_000      # priced off AXON, not NOVA


def test_flat_leader_is_not_a_breakout():
    leader, follower = build()
    feed(leader, follower, [10_000] * 10)
    assert follower.signal(0) is None


def test_leader_below_its_high_is_not_a_breakout():
    leader, follower = build()
    feed(leader, follower, [10_000] * 8 + [10_500, 10_400])
    assert follower.signal(0) is None


# --- arm decay ----------------------------------------------------------

def test_arm_expires_after_configured_ticks():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    assert follower.armed
    feed(leader, follower, [10_480] * 4)   # just under the high: no new signal
    assert follower.armed and follower.signal(0) is not None
    feed(leader, follower, [10_480])
    assert not follower.armed
    assert follower.signal(0) is None


def test_fresh_breakout_rearms():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    feed(leader, follower, [10_480] * 6)
    assert not follower.armed
    feed(leader, follower, [10_900])
    assert follower.armed


# --- exit ---------------------------------------------------------------

def test_sells_when_leader_rolls_over_and_we_hold():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    feed(leader, follower, [10_400] * 6)   # -0.95% off the 10_500 high
    sig = follower.signal(100)
    assert sig is not None and sig.side == "sell"


def test_no_sell_when_flat():
    """Long-only: never sell what we do not hold."""
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    feed(leader, follower, [10_400] * 6)
    assert follower.signal(0) is None


def test_sell_is_capped_by_sellable_quantity():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    feed(leader, follower, [10_400] * 6)
    assert follower.signal(20).quantity == 20


def test_shallow_pullback_does_not_close():
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    feed(leader, follower, [10_480] * 6)   # -0.19%, inside exit_drawdown
    assert follower.signal(100) is None


# --- session boundary ---------------------------------------------------

def test_reset_clears_the_breakout_arm():
    """A stale arm would buy into the next window off the last one's breakout."""
    leader, follower = build()
    feed(leader, follower, [10_000] * 9 + [10_500])
    assert follower.armed
    follower.reset()
    assert not follower.armed and not follower.prices


# --- construction guards ------------------------------------------------

def test_breakout_window_wider_than_leader_lookback_is_rejected():
    """Would never fill, so the follower would silently never trade."""
    with pytest.raises(ValueError, match="exceeds NOVA lookback_samples"):
        build(follower_overrides={"leader_breakout_samples": 30})


def test_breakout_window_needs_a_prior_sample():
    with pytest.raises(ValueError, match="must be >= 2"):
        build(follower_overrides={"leader_breakout_samples": 1})


# --- wiring -------------------------------------------------------------

def test_build_strategies_resolves_leader_declared_after_follower():
    config = {
        "instruments": {"active": {"primary": "ELXR", "secondary": "AXON",
                                   "extra": ["NOVA"]}},
        "strategy": {
            "ELXR": {"type": "mean_reversion", "lookback_samples": 15,
                     "deviation_threshold": 0.004, "trade_quantity": 100},
            "AXON": {"type": "pairs_follow", "leader": "NOVA", **FOLLOWER_CFG},
            "NOVA": LEADER_CFG,
        },
    }
    strategies = build_strategies(config)
    assert strategies["AXON"].leader is strategies["NOVA"]


def test_build_strategies_rejects_inactive_leader():
    config = {
        "instruments": {"active": {"primary": "AXON", "secondary": "ELXR"}},
        "strategy": {
            "AXON": {"type": "pairs_follow", "leader": "NOVA", **FOLLOWER_CFG},
            "ELXR": {"type": "mean_reversion", "lookback_samples": 15,
                     "deviation_threshold": 0.004, "trade_quantity": 100},
        },
    }
    with pytest.raises(ValueError, match="is not an active strategy"):
        build_strategies(config)


def test_real_config_wires_axon_to_nova():
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    strategies = build_strategies(config)
    assert isinstance(strategies["AXON"], PairsFollowStrategy)
    assert strategies["AXON"].leader is strategies["NOVA"]
    # The breakout window has to fit inside the leader's buffer.
    assert strategies["AXON"].breakout_samples <= strategies["NOVA"].lookback
