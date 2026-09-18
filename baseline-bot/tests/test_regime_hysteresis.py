"""Regime hysteresis (James Wee, page 3).

"hysteresis so it doesn't flip-flop on the boundary — once you switch regime,
require a few samples before switching back."

A label computed from a rolling window oscillates whenever the market sits near
a threshold, and each oscillation would resize or cancel entries. So a new
label must be confirmed on `confirm_samples` consecutive observations.

The debounce is ASYMMETRIC: a label that forbids entries in every family is
adopted immediately, because delaying a risk-reducing transition is the one
thing hysteresis must not do. "In every family" is enforced at startup, not
assumed — see test_immediate_label_sized_above_zero_is_rejected.
"""
from pathlib import Path

import pytest
import yaml

from main import _check_immediate_labels_are_risk_off, build_regime_classifier
from strategies.regime_adapter import DEFAULT_IMMEDIATE, AtrRegimeClassifier
from strategies.regime_policy import Regime, RegimePolicy


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def clf(confirm=3, immediate=None):
    return AtrRegimeClassifier(confirm_samples=confirm,
                              immediate_labels=immediate)


def publish(adapter, symbol, label):
    """Force a published label without going through the price classifier."""
    adapter._published[symbol] = label


# =========================================================================
# The debounce
# =========================================================================

def test_one_print_does_not_switch_regime():
    """The whole point: a single boundary oscillation is absorbed."""
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.RANGE)
    assert a.classify("NOVA") == Regime.TREND
    assert a.pending("NOVA") == (Regime.RANGE, 1)


def test_label_publishes_on_the_confirming_observation():
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    for expected in (1, 2):
        a._debounce("NOVA", Regime.RANGE)
        assert a.classify("NOVA") == Regime.TREND
        assert a.pending("NOVA") == (Regime.RANGE, expected)
    a._debounce("NOVA", Regime.RANGE)
    assert a.classify("NOVA") == Regime.RANGE
    assert a.pending("NOVA") is None


def test_flip_back_abandons_a_part_built_candidate():
    """RANGE, RANGE, TREND, RANGE must not accumulate three RANGE votes."""
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.RANGE)
    a._debounce("NOVA", Regime.RANGE)
    a._debounce("NOVA", Regime.TREND)          # back to published
    assert a.pending("NOVA") is None
    a._debounce("NOVA", Regime.RANGE)
    assert a.classify("NOVA") == Regime.TREND
    assert a.pending("NOVA") == (Regime.RANGE, 1)


def test_a_different_candidate_restarts_the_count():
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.RANGE)
    a._debounce("NOVA", Regime.RANGE)
    a._debounce("NOVA", Regime.COMPRESSION)    # new candidate
    assert a.pending("NOVA") == (Regime.COMPRESSION, 1)
    assert a.classify("NOVA") == Regime.TREND


def test_confirm_one_disables_the_debounce():
    a = clf(confirm=1)
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.RANGE)
    assert a.classify("NOVA") == Regime.RANGE


def test_first_label_needs_no_confirmation():
    """There is nothing to flip-flop against before the first publish."""
    a = clf(confirm=3)
    a._debounce("NOVA", Regime.COMPRESSION)
    assert a.classify("NOVA") == Regime.COMPRESSION


def test_symbols_debounce_independently():
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    publish(a, "ELXR", Regime.TREND)
    for _ in range(3):
        a._debounce("NOVA", Regime.RANGE)
    a._debounce("ELXR", Regime.RANGE)
    assert a.classify("NOVA") == Regime.RANGE
    assert a.classify("ELXR") == Regime.TREND


# =========================================================================
# The asymmetry
# =========================================================================

def test_an_immediate_label_skips_confirmation():
    """Delaying a risk-off transition is the one thing this must not do."""
    a = clf(confirm=3, immediate=[Regime.WARMUP])
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.WARMUP)
    assert a.classify("NOVA") == Regime.WARMUP


def test_shock_is_not_immediate_by_default():
    """crash_buy sizes SHOCK at 1.0, so one print must not grant full size."""
    assert Regime.SHOCK not in DEFAULT_IMMEDIATE
    a = clf(confirm=3)
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.SHOCK)
    assert a.classify("NOVA") == Regime.TREND


def test_warmup_is_the_default_immediate_label():
    assert DEFAULT_IMMEDIATE == (Regime.WARMUP,)


def test_immediate_adoption_clears_a_pending_candidate():
    a = clf(confirm=3, immediate=[Regime.WARMUP])
    publish(a, "NOVA", Regime.TREND)
    a._debounce("NOVA", Regime.RANGE)
    a._debounce("NOVA", Regime.WARMUP)
    assert a.classify("NOVA") == Regime.WARMUP
    assert a.pending("NOVA") is None


# =========================================================================
# The enforced invariant
# =========================================================================

def test_real_config_immediate_labels_are_risk_off_everywhere(config):
    """The asymmetry is only sound if these forbid entries in every family."""
    policy = RegimePolicy({**config["regime"], "enabled": True})
    for label in config["regime"]["hysteresis"]["immediate_labels"]:
        for family in policy.table():
            assert policy.multiplier("*", family, label) == 0.0, (family, label)


def test_immediate_label_sized_above_zero_is_rejected(config):
    """SHOCK is the live example: crash_buy sizes it at 1.0."""
    with pytest.raises(ValueError, match="adopted without confirmation"):
        _check_immediate_labels_are_risk_off(
            config["regime"], {"immediate_labels": ["SHOCK"]})


def test_no_immediate_labels_is_accepted(config):
    _check_immediate_labels_are_risk_off(config["regime"], {})


def test_unknown_immediate_label_is_rejected():
    with pytest.raises(ValueError, match="unknown regime"):
        clf(immediate=["SIDEWAYS"])


def test_zero_confirm_samples_is_rejected():
    with pytest.raises(ValueError, match="confirm_samples must be >= 1"):
        clf(confirm=0)


# =========================================================================
# Diagnostics and lifecycle
# =========================================================================

def test_raw_exposes_the_undebounced_label():
    """So a held label reads as a deliberate delay, not a lag bug."""
    a = clf(confirm=3)
    for index in range(19):
        a.observe("NOVA", 10_000 + 100 * index)
    assert a.classify("NOVA") == Regime.TREND
    a.observe("NOVA", 40_000)
    assert a.raw("NOVA") == Regime.SHOCK
    assert a.classify("NOVA") == Regime.TREND
    assert a.pending("NOVA") == (Regime.SHOCK, 1)


def test_reset_clears_debounce_state():
    """Per-window, like every other piece of state at a boundary."""
    a = clf(confirm=3)
    for index in range(19):
        a.observe("NOVA", 10_000 + 100 * index)
    a.observe("NOVA", 40_000)
    assert a.pending("NOVA") is not None
    a.reset()
    assert a.classify("NOVA") == Regime.WARMUP
    assert a.pending("NOVA") is None
    assert a.raw("NOVA") == Regime.WARMUP


def test_reset_can_target_one_symbol():
    a = clf(confirm=3)
    for index in range(19):
        a.observe("NOVA", 10_000 + 100 * index)
        a.observe("CRVX", 10_000 + 100 * index)
    a.reset("NOVA")
    assert a.classify("NOVA") == Regime.WARMUP
    assert a.classify("CRVX") == Regime.TREND


# =========================================================================
# End to end, through the configured classifier
# =========================================================================

def test_sustained_move_confirms_and_a_blip_does_not(config):
    adapter = build_regime_classifier(config["regime"])
    confirm = adapter.confirm_samples

    for index in range(16 + confirm):                   # sustained uptrend
        adapter.observe("NOVA", 10_000 + 100 * index)
    assert adapter.classify("NOVA") == Regime.TREND

    # A single divergent print must not move the published label.
    before = adapter.classify("NOVA")
    adapter.observe("NOVA", 10_000 + 100 * (15 + confirm))
    assert adapter.classify("NOVA") == before


def test_confirmed_transition_reaches_the_policy(config):
    """The debounce delays the label; it must not prevent the transition."""
    adapter = build_regime_classifier(config["regime"])
    policy = RegimePolicy({**config["regime"], "enabled": True})
    for index in range(16 + adapter.confirm_samples):
        adapter.observe("NOVA", 10_000 + 100 * index)
    assert policy.multiplier(
        "NOVA", "momentum", adapter.classify("NOVA")) == 1.0
