"""Momentum's give-back exit and its bounded scale-in.

THE DEFECT THESE PIN. Momentum enters on the window return,
`(last - first) / first`. Reused as an exit that measure has a structural blind
spot: `first` slides forward with the window, so once a rise flattens, `first`
catches up into the new level and the return decays toward 0% — AWAY from the
negative trigger. Measured before the fix, on a +3% ramp into a plateau: 10
buys to the 4000-share cap, 0 sells, then held for the rest of the window. None
of the three configured exits could release it either — the stop cannot fire on
a profitable position, and the trailing take-profit was not armed.

The fix prices a second exit measure off the window HIGH, which only ratchets
up, so a roll-over registers immediately rather than after the whole move has
been handed back. Both measures are OR-ed and both are checked BEFORE the entry
branch, because the case worth catching is precisely the one where the window
return still reads as momentum while the price is already rolling over.

`max_tranches` bounds the scale-in. It is a concentration dial rather than a
fix, and is deliberately unset for NOVA — see test_config_leaves_nova_unbounded.
"""
from pathlib import Path

import pytest
import yaml

from main import build_strategies
from strategies.momentum import MomentumStrategy


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def momentum(threshold=0.012, giveback=None, tranches=None, clip=400,
             lookback=25):
    cfg = {"lookback_samples": lookback, "momentum_threshold": threshold,
           "trade_quantity": clip, "limit_offset_bps": 5}
    if giveback is not None:
        cfg["exit_giveback_pct"] = giveback
    if tranches is not None:
        cfg["max_tranches"] = tranches
    return MomentumStrategy("NOVA", cfg)


def feed(strat, prices):
    for price in prices:
        strat.on_price(price)
    return strat


def ramp_then(strat, peak_pct, tail, base=10_000, rise=None):
    """A rise of `peak_pct`, then the `tail` prices.

    The rise is at least one full lookback long, so the buffer is guaranteed
    ready — deriving it rather than hard-coding a sample count, because a
    fixed count silently stopped filling the buffer the last time NOVA's
    lookback was widened and quietly defanged four assertions.
    """
    rise = strat.lookback if rise is None else rise
    top = base * (1 + peak_pct)
    path = [int(base + (top - base) * i / rise) for i in range(rise)]
    return feed(strat, path + list(tail))


# =========================================================================
# The give-back measure
# =========================================================================

def test_giveback_is_zero_at_a_new_high():
    strat = feed(momentum(giveback=0.012), range(10_000, 10_025))
    assert strat.giveback_from_high() == pytest.approx(0.0)


def test_giveback_measures_from_the_high_not_the_first_sample():
    strat = momentum(giveback=0.012, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    # window return is +6.7% and still positive; give-back off 11_000 is -3%.
    assert strat.giveback_from_high() == pytest.approx(-0.03)


def test_giveback_exit_fires_while_the_window_return_is_still_positive():
    """The blind spot, stated directly: momentum says 'trend', price says
    'rolling over', and the position must be allowed to leave."""
    strat = momentum(giveback=0.012, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    first, last = strat.prices[0], strat.prices[-1]
    assert (last - first) / first > strat.threshold, "window return still bullish"

    signal = strat.signal(position=800)
    assert signal is not None and signal.side == "sell"


def test_exit_beats_entry_when_both_would_fire():
    """Checked before the entry branch, or the roll-over would ADD instead."""
    strat = momentum(giveback=0.012, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    assert strat.signal(position=800).side == "sell"
    # With no position there is nothing to exit, so the entry stands.
    assert strat.signal(position=0).side == "buy"


def test_giveback_below_the_threshold_does_not_exit():
    strat = momentum(giveback=0.012, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_950, 10_945])   # -0.5% off the high
    # The window return is still +9.5%, so the entry branch is what stands
    # here. The assertion that matters is that a shallow dip is not an exit.
    signal = strat.signal(position=800)
    assert signal is None or signal.side == "buy"


def test_giveback_exit_reduces_by_one_clip():
    """The full close belongs to risk.exits, which knows the cost basis."""
    strat = momentum(giveback=0.012, clip=400, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    assert strat.signal(position=4000).quantity == 400


def test_giveback_exit_never_oversells_the_position():
    strat = momentum(giveback=0.012, clip=400, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    assert strat.signal(position=150).quantity == 150


def test_long_only_no_giveback_exit_when_flat():
    strat = momentum(giveback=0.012, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    assert strat.signal(position=0) is not None      # an entry, not an exit
    assert strat.signal(position=0).side == "buy"


def test_window_return_exit_still_works_alongside_giveback():
    """The original measure is OR-ed in, not replaced."""
    strat = momentum(giveback=0.05, lookback=3, threshold=0.01)
    feed(strat, [10_300, 10_100, 9_900])             # -3.9% window return
    assert strat.signal(position=100).side == "sell"


# =========================================================================
# The regression: a rise into a plateau must not pin the position
# =========================================================================

def test_rollover_after_a_rise_now_releases_the_position():
    strat = momentum(giveback=0.012)
    ramp_then(strat, 0.03, [10_300] * 3 + [10_250, 10_180, 10_100])
    assert strat.signal(position=4000).side == "sell"


def test_before_the_fix_the_same_rollover_was_pinned():
    """Same path, give-back disabled: the window return has decayed to roughly
    zero, so nothing sells. This is the defect, kept executable."""
    strat = momentum(giveback=None)
    ramp_then(strat, 0.03, [10_300] * 3 + [10_250, 10_180, 10_100])
    first, last = strat.prices[0], strat.prices[-1]
    change = (last - first) / first
    assert -strat.threshold < change, "window return never reaches the trigger"
    assert strat.signal(position=4000) is None


def test_a_perfectly_flat_plateau_is_a_known_limitation():
    """Honest boundary of the fix: a price pinned EXACTLY at its own window
    high has a give-back of 0 by construction, so neither measure fires. Real
    feeds oscillate, and a position held at its high with an armed trailing
    stop is not the pathology; the roll-over above is.
    """
    strat = momentum(giveback=0.012)
    ramp_then(strat, 0.03, [10_300] * 30)
    assert strat.giveback_from_high() == pytest.approx(0.0)
    assert strat.signal(position=4000) is None


# =========================================================================
# The bounded scale-in
# =========================================================================

def test_tranche_count_comes_from_the_position():
    """Stateless, so it survives a restart and cannot drift from the
    exchange — the same reason the mean-reversion ladder works this way."""
    strat = momentum(tranches=3, clip=400)
    assert strat.tranches_held(0) == 0
    assert strat.tranches_held(399) == 0
    assert strat.tranches_held(400) == 1
    assert strat.tranches_held(1000) == 2


def test_scale_in_stops_at_max_tranches():
    strat = momentum(tranches=3, clip=400)
    ramp_then(strat, 0.05, [])
    for held in (0, 400, 800):
        assert strat.signal(position=held).side == "buy", held
    assert strat.signal(position=1200) is None, "4th tranche must be refused"


def test_unset_max_tranches_leaves_the_scale_in_unbounded():
    """The position cap in risk/ is then the only bound, which is the shipped
    configuration for NOVA."""
    strat = momentum(tranches=None, clip=400)
    ramp_then(strat, 0.05, [])
    assert strat.max_tranches is None
    assert strat.can_add(40_000) is True
    assert strat.signal(position=40_000).side == "buy"


def test_the_tranche_bound_never_blocks_an_exit():
    """Every gate in this bot reduces risk only. A bound that could refuse a
    sell would trap exactly the position it was meant to limit."""
    strat = momentum(giveback=0.012, tranches=1, clip=400, lookback=5)
    feed(strat, [10_000, 10_500, 11_000, 10_800, 10_670])
    assert strat.can_add(4000) is False
    signal = strat.signal(position=4000)
    assert signal is not None and signal.side == "sell"


# =========================================================================
# Validation
# =========================================================================

def test_zero_tranches_is_rejected():
    with pytest.raises(ValueError, match="max_tranches must be >= 1"):
        momentum(tranches=0)


def test_non_positive_giveback_is_rejected():
    with pytest.raises(ValueError, match="exit_giveback_pct must be > 0"):
        momentum(giveback=0.0)


# =========================================================================
# The real config
# =========================================================================

def test_nova_has_a_giveback_exit(config):
    assert config["strategy"]["NOVA"]["exit_giveback_pct"] == 0.012


def test_crvx_deliberately_has_no_giveback(config):
    """At 15 samples / 0.75% its window return turns negative in time on its
    own; measured, a give-back only churned and clipped its uptrend."""
    assert "exit_giveback_pct" not in config["strategy"]["CRVX"]


def test_config_leaves_nova_unbounded(config):
    """A concentration dial, not part of the fix — set it deliberately."""
    assert "max_tranches" not in config["strategy"]["NOVA"]


def test_giveback_scales_out_before_the_trail_closes(config):
    """The two exits are meant to ladder, and the order is what makes them
    complementary rather than redundant.

    The give-back reduces ONE CLIP off the window high; the trail in
    risk.exits closes EVERYTHING once armed at +4% over cost. So the
    give-back must be the tighter of the two: a roll-over then trims first
    and only closes out if the drop deepens. Were it the looser, the trail
    would always close the whole position first and the give-back would be
    dead config.
    """
    giveback = config["strategy"]["NOVA"]["exit_giveback_pct"]
    trail = config["risk"]["exits"]["NOVA"]["trailing_take_profit"]["trail_pct"]
    assert giveback <= trail, (
        f"give-back {giveback} is looser than the {trail} trail; the trail "
        f"would close the position first and this measure would never act")


def test_built_nova_carries_the_giveback(config):
    strat = build_strategies(config)["NOVA"]
    assert strat.exit_giveback == 0.012
    assert strat.max_tranches is None
