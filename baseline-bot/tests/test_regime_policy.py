"""Regime policy tests (James Wee, page 3). Prices are integer cents.

Two invariants:
  * regime can only REDUCE risk — never enlarge a signal, never touch a sell;
  * an unknown or unclassified regime places nothing.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from main import RUNNING, build_regime_classifier, build_strategies, make_price_handler
from strategies.base import Signal
from strategies.momentum import MomentumStrategy
from strategies.regime_policy import (
    DEFAULT_POLICY,
    Regime,
    RegimePolicy,
    StaticRegimeClassifier,
)


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


@pytest.fixture
def policy():
    return RegimePolicy({"enabled": True})


def momentum(symbol="NOVA", qty=100):
    return MomentumStrategy(symbol, {"lookback_samples": 25,
                                     "momentum_threshold": 0.02,
                                     "trade_quantity": qty,
                                     "limit_offset_bps": 5})


def buy(qty=100):
    return Signal("buy", qty, 10_000)


# =========================================================================
# The six regimes, applied to a buy
# =========================================================================

@pytest.mark.parametrize("regime,expected", [
    (Regime.TREND, 100),        # momentum active
    (Regime.RANGE, None),       # p.3: "suspend momentum (it will bleed)"
    (Regime.COMPRESSION, 25),   # no trend to ride
    (Regime.SHOCK, None),       # whipsaw risk
    (Regime.NEUTRAL, 50),       # p.3: "hold current posture, reduce size"
    (Regime.WARMUP, None),      # never trade on an unclassified regime
])
def test_momentum_sizing_per_regime(policy, regime, expected):
    signal = policy.apply("NOVA", momentum(), buy(100),
                          StaticRegimeClassifier(regime))
    assert (signal.quantity if signal else None) == expected


def test_mean_reversion_is_suspended_in_a_trend(policy, config):
    """The falling-knife case: reverting into a trend buys every 'dip'."""
    elxr = build_strategies(config)["ELXR"]
    assert policy.apply("ELXR", elxr, buy(100),
                        StaticRegimeClassifier(Regime.TREND)) is None


def test_mean_reversion_runs_full_size_in_a_range(policy, config):
    elxr = build_strategies(config)["ELXR"]
    signal = policy.apply("ELXR", elxr, buy(100),
                          StaticRegimeClassifier(Regime.RANGE))
    assert signal.quantity == 100


def test_momentum_and_mean_reversion_disagree_by_design(policy, config):
    """The whole point of page 3: the two families want opposite regimes."""
    strategies = build_strategies(config)
    for regime, momentum_ok, reversion_ok in [(Regime.TREND, True, False),
                                              (Regime.RANGE, False, True)]:
        clf = StaticRegimeClassifier(regime)
        assert bool(policy.apply("NOVA", strategies["NOVA"], buy(), clf)) is momentum_ok
        assert bool(policy.apply("ELXR", strategies["ELXR"], buy(), clf)) is reversion_ok


# =========================================================================
# Risk can only go down
# =========================================================================

def test_a_sell_is_never_gated(policy):
    """A market-state label must not be able to trap us in a position."""
    for regime in Regime.ALL:
        sell = Signal("sell", 100, 10_000)
        out = policy.apply("NOVA", momentum(), sell, StaticRegimeClassifier(regime))
        assert out is sell and out.quantity == 100


def test_multiplier_above_one_is_rejected():
    """Regime must not be able to increase size."""
    with pytest.raises(ValueError, match=r"must be in \[0, 1\]"):
        RegimePolicy({"enabled": True,
                      "policy": {"momentum": {Regime.TREND: 1.5}}})


def test_disabled_policy_is_a_passthrough(config):
    off = RegimePolicy({"enabled": False})
    signal = buy(100)
    assert off.apply("NOVA", momentum(), signal,
                     StaticRegimeClassifier(Regime.SHOCK)) is signal


def test_unknown_label_is_treated_as_warmup(policy):
    """Fail safe: a label we cannot interpret places nothing."""
    assert policy.apply("NOVA", momentum(), buy(),
                        StaticRegimeClassifier("LUNCHTIME")) is None


def test_rounding_to_zero_places_nothing(policy):
    """A 0-share order is a 400 ORDER_QUANTITY_INVALID."""
    # 2 shares x COMPRESSION 0.25 = 0.5 -> 0
    assert policy.apply("NOVA", momentum(qty=2), buy(2),
                        StaticRegimeClassifier(Regime.COMPRESSION)) is None


# =========================================================================
# Follower reads its leader's regime
# =========================================================================

def test_follower_uses_the_leaders_regime(policy, config):
    """AXON's entry is NOVA breaking out, so NOVA's regime is what matters."""
    axon = build_strategies(config)["AXON"]
    clf = StaticRegimeClassifier(per_symbol={"NOVA": Regime.TREND,
                                             "AXON": Regime.SHOCK})
    assert policy.regime_for(axon, clf) == Regime.TREND
    assert policy.apply("AXON", axon, buy(100), clf).quantity == 100


def test_non_follower_uses_its_own_regime(policy):
    clf = StaticRegimeClassifier(per_symbol={"NOVA": Regime.RANGE})
    assert policy.regime_for(momentum(), clf) == Regime.RANGE


# =========================================================================
# Config
# =========================================================================

def test_per_symbol_override_beats_the_family_table():
    policy = RegimePolicy({"enabled": True,
                           "per_symbol": {"DRFT": {Regime.NEUTRAL: 0.0}}})
    assert policy.multiplier("DRFT", "mean_reversion", Regime.NEUTRAL) == 0.0
    assert policy.multiplier("ELXR", "mean_reversion", Regime.NEUTRAL) == 0.5


def test_unknown_family_is_rejected():
    with pytest.raises(ValueError, match="unknown strategy family"):
        RegimePolicy({"enabled": True, "policy": {"scalping": {}}})


def test_unknown_regime_key_is_rejected():
    with pytest.raises(ValueError, match="unknown regime"):
        RegimePolicy({"enabled": True,
                      "policy": {"momentum": {"SIDEWAYS": 0.5}}})


def test_every_family_covers_every_regime():
    """A missing cell would fall through to a KeyError on the trading path."""
    for family, table in DEFAULT_POLICY.items():
        assert set(table) == set(Regime.ALL), family


def test_real_config_matches_the_documented_defaults(config):
    """The config restates DEFAULT_POLICY for visibility — keep them in step."""
    policy = RegimePolicy({**config["regime"], "enabled": True})
    for family, table in DEFAULT_POLICY.items():
        assert policy.table(family) == table, family


def test_real_config_ships_disabled(config):
    """Regime sizing remains opt-in after classifier integration."""
    assert config["regime"]["enabled"] is False


# =========================================================================
# Classifier resolution
# =========================================================================

def test_enabled_without_a_classifier_raises():
    """Silently reading WARMUP everywhere would stop trading without saying so."""
    with pytest.raises(ValueError, match="regime.classifier is not set"):
        build_regime_classifier({"enabled": True})


def test_static_regime_escape_hatch():
    clf = build_regime_classifier({"enabled": True, "static_regime": "TREND"})
    assert clf.classify("NOVA") == Regime.TREND


def test_dotted_path_is_resolved():
    clf = build_regime_classifier(
        {"classifier": "strategies.regime_policy.StaticRegimeClassifier"})
    assert clf.classify("NOVA") == Regime.NEUTRAL


def test_object_missing_the_contract_is_rejected():
    with pytest.raises(ValueError, match="has no observe"):
        build_regime_classifier({"classifier": "strategies.regime_policy.Regime"})


def test_missing_attribute_is_reported():
    with pytest.raises(ValueError, match="not found"):
        build_regime_classifier({"classifier": "strategies.regime_policy.NoSuchThing"})


# =========================================================================
# Integration through the price handler
# =========================================================================

def wired(config, regime, sellable=0):
    orders = Mock()
    orders.available_to_sell.return_value = sellable
    orders.avg_cost.return_value = 0
    strategies = build_strategies(config)
    policy = RegimePolicy({**config["regime"], "enabled": True})
    clf = StaticRegimeClassifier(regime)
    on_price = make_price_handler(
        strategies, orders, {"session_open": True, "session_id": "w1"},
        {"value": RUNNING}, regime_policy=policy, classifier=clf)
    return on_price, orders, strategies


def test_range_blocks_a_momentum_buy_through_the_handler(config):
    on_price, orders, strategies = wired(config, Regime.RANGE)
    nova = strategies["NOVA"]
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 10_000 + i * 100})
    orders.submit_intent.assert_not_called()


def test_trend_lets_a_momentum_buy_through_at_full_size(config):
    on_price, orders, strategies = wired(config, Regime.TREND)
    nova = strategies["NOVA"]
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 10_000 + i * 100})
    _, side, quantity, _ = orders.submit_intent.call_args.args
    assert side == "buy" and quantity == nova.trade_quantity


def test_neutral_halves_the_clip_through_the_handler(config):
    on_price, orders, strategies = wired(config, Regime.NEUTRAL)
    nova = strategies["NOVA"]
    for i in range(nova.lookback):
        on_price({"symbol": "NOVA", "last_price": 10_000 + i * 100})
    _, _, quantity, _ = orders.submit_intent.call_args.args
    assert quantity == nova.trade_quantity // 2


def test_classifier_observes_every_print(config):
    clf = Mock()
    clf.classify.return_value = Regime.TREND
    strategies = build_strategies(config)
    orders = Mock()
    orders.available_to_sell.return_value = 0
    orders.avg_cost.return_value = 0
    on_price = make_price_handler(
        strategies, orders, {"session_open": False, "session_id": "w1"},
        {"value": RUNNING}, regime_policy=RegimePolicy({"enabled": True}),
        classifier=clf)
    # Closed session: still observed, so the classifier is warm at the open.
    on_price({"symbol": "NOVA", "last_price": 10_000})
    clf.observe.assert_called_once_with("NOVA", 10_000)


@pytest.mark.parametrize("direction", [1, -1])
def test_atr_adapter_maps_trends_and_shocks(config, policy, direction):
    clf = build_regime_classifier(config["regime"])
    assert clf.classify("NOVA") == Regime.WARMUP
    for index in range(16):
        clf.observe("NOVA", 10_000 + direction * 100 * index)
    assert clf.classify("NOVA") == Regime.TREND
    assert policy.apply("NOVA", momentum(), buy(), clf).quantity == 100
    assert clf.classify("ELXR") == Regime.WARMUP
    clf.observe("NOVA", 10_000 + direction * 2500)
    assert clf.classify("NOVA") == Regime.SHOCK
    assert policy.apply("NOVA", momentum(), buy(), clf) is None
    sell = Signal("sell", 100, 10_000)
    assert policy.apply("NOVA", momentum(), sell, clf) is sell


def test_atr_adapter_through_price_handler(config):
    clf = build_regime_classifier(config["regime"])
    orders = Mock()
    orders.available_to_sell.return_value = 0
    orders.avg_cost.return_value = 0
    strategies = build_strategies(config)
    handler = make_price_handler(
        strategies, orders, {"session_open": True, "session_id": "w1"},
        {"value": RUNNING}, regime_policy=RegimePolicy({"enabled": True}),
        classifier=clf)
    for index in range(strategies["NOVA"].lookback):
        handler({"symbol": "NOVA", "last_price": 10_000 + index * 100})
    assert orders.submit_intent.call_args.args[1:3] == (
        "buy", strategies["NOVA"].trade_quantity)
