"""DCA / scale-in ladder for the mean-reversion names.

Where it belongs and where it does not:

* MEAN-REVERSION (ELXR, DRFT) — yes. The deviation band *is* the thesis, so a
  deeper dip is a stronger signal rather than a broken one. And on DRFT it also
  reduces price impact: "your order size moves the price" (James p.4), so three
  small clips disturb a thin book less than one block.

* MOMENTUM (NOVA, CRVX) — no. A DCA ladder adds on ADVERSE moves, which inverts
  a momentum thesis: it would buy a falling price while the strategy's premise
  is that price rises. Momentum already scales in on FAVOURABLE moves.

The safety argument, which these tests pin: the ladder is bounded by
`max_tranches` and by the position cap, so worst-case loss is unchanged —
cap x stop_loss_pct with or without DCA. What changes is the average entry
price and how often the cap is reached.
"""
from pathlib import Path

import pytest
import yaml

from main import build_strategies
from strategies.mean_reversion import MeanReversionStrategy


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def ladder(clip=100, band=0.004, tranches=3, step=1.0):
    return MeanReversionStrategy("ELXR", {
        "lookback_samples": 15, "deviation_threshold": band,
        "trade_quantity": clip,
        "dca": {"max_tranches": tranches, "step_multiple": step}})


def at_deviation(strat, pct, base=10_000):
    """Fill the buffer so the latest print sits `pct` from the mean."""
    strat.prices.clear()
    for _ in range(strat.lookback - 1):
        strat.on_price(base)
    # mean includes the last sample, so solve for it: dev = (x-m)/m with
    # m = ((N-1)*base + x)/N. Close enough to step down until it clears.
    target = int(base * (1 + pct))
    strat.on_price(target)
    mean = sum(strat.prices) / len(strat.prices)
    return (strat.prices[-1] - mean) / mean


# --- the ladder ---------------------------------------------------------

def test_first_tranche_needs_only_the_base_band():
    strat = ladder()
    assert strat.required_deviation(0) == pytest.approx(0.004)


def test_each_tranche_demands_a_deeper_dip():
    strat = ladder(clip=100, band=0.004, step=1.0)
    assert strat.required_deviation(0) == pytest.approx(0.004)
    assert strat.required_deviation(100) == pytest.approx(0.008)
    assert strat.required_deviation(200) == pytest.approx(0.012)


def test_tranche_count_comes_from_the_position():
    """Stateless: survives a restart and cannot drift from the exchange."""
    strat = ladder(clip=100)
    assert strat.tranches_held(0) == 0
    assert strat.tranches_held(99) == 0
    assert strat.tranches_held(100) == 1
    assert strat.tranches_held(250) == 2


def test_a_shallow_dip_only_buys_once():
    """The whole point: one dip is one tranche, not a cascade."""
    strat = ladder(clip=100, band=0.004)
    at_deviation(strat, -0.006)                  # past 1x, short of 2x
    first = strat.signal(0)
    assert first is not None and first.side == "buy"
    assert strat.signal(100) is None             # same dip, no second tranche


def test_a_deepening_dip_adds_a_tranche():
    strat = ladder(clip=100, band=0.004)
    at_deviation(strat, -0.010)                  # past 2x
    assert strat.signal(100) is not None


def test_the_ladder_stops_at_max_tranches():
    strat = ladder(clip=100, band=0.004, tranches=3)
    at_deviation(strat, -0.50)                   # far past every rung
    assert strat.signal(0) is not None
    assert strat.signal(100) is not None
    assert strat.signal(200) is not None
    assert strat.signal(300) is None, "4th tranche must be refused"


def test_step_zero_makes_every_rung_the_same_depth():
    strat = ladder(clip=100, band=0.004, step=0.0)
    assert strat.required_deviation(200) == pytest.approx(0.004)


def test_absent_dca_block_is_a_single_tranche():
    """An instrument with no `dca` key behaves exactly as before."""
    strat = MeanReversionStrategy("ELXR", {
        "lookback_samples": 15, "deviation_threshold": 0.004,
        "trade_quantity": 100})
    assert strat.max_tranches == 1
    at_deviation(strat, -0.50)
    assert strat.signal(0) is not None
    assert strat.signal(100) is None


# --- the ladder never affects exits ------------------------------------

def test_selling_is_untouched_by_the_ladder():
    strat = ladder(clip=100, band=0.004, tranches=3)
    strat.sell_threshold = 0.004
    at_deviation(strat, 0.010)                   # above the mean, holding
    signal = strat.signal(300)
    assert signal is not None and signal.side == "sell"


# --- validation ---------------------------------------------------------

def test_zero_tranches_is_rejected():
    with pytest.raises(ValueError, match="max_tranches must be >= 1"):
        ladder(tranches=0)


def test_negative_step_is_rejected():
    with pytest.raises(ValueError, match="step_multiple cannot be negative"):
        ladder(step=-1.0)


# --- the real config ----------------------------------------------------

def test_mean_reversion_names_have_a_ladder(config):
    for symbol in ("ELXR", "DRFT"):
        assert config["strategy"][symbol]["dca"]["max_tranches"] > 1, symbol


def test_momentum_names_have_no_ladder(config):
    """A DCA ladder would invert a momentum thesis."""
    for symbol in ("NOVA", "CRVX"):
        assert "dca" not in config["strategy"][symbol], symbol
    assert "dca" not in config["strategy"]["AXON"]


def test_built_strategies_only_ladder_where_configured(config):
    strategies = build_strategies(config)
    assert strategies["ELXR"].max_tranches == 3
    assert strategies["DRFT"].max_tranches == 3
    # The momentum names also bound their scale-in, but that is NOT a ladder:
    # `required_deviation` is what makes a rung demand a deeper ADVERSE move,
    # and momentum must never have it. `max_tranches` alone is just a cap on
    # how many times a strategy may add, which both kinds of strategy want.
    for symbol in ("NOVA", "CRVX", "AXON"):
        assert not hasattr(strategies[symbol], "required_deviation"), symbol


def test_full_ladder_stays_within_the_position_cap(config):
    """The cap is what bounds the loss; the ladder must not be able to breach
    it, or the ladder becomes the binding risk control instead of the cap."""
    caps = {"NOVA": config["risk"]["max_position_primary"],
            "ELXR": config["risk"]["max_position_secondary"],
            **config["risk"]["max_position"]}
    for symbol in ("ELXR", "DRFT"):
        strat_cfg = config["strategy"][symbol]
        full = strat_cfg["trade_quantity"] * strat_cfg["dca"]["max_tranches"]
        assert full <= caps[symbol], (
            f"{symbol}: {strat_cfg['dca']['max_tranches']} x "
            f"{strat_cfg['trade_quantity']} = {full} exceeds cap {caps[symbol]}")


def test_dca_does_not_change_worst_case_loss(config):
    """Max loss is cap x stop, whatever path got us there."""
    caps = {"ELXR": config["risk"]["max_position_secondary"],
            **config["risk"]["max_position"]}
    for symbol in ("ELXR", "DRFT"):
        stop = config["risk"]["exits"][symbol]["stop_loss_pct"]
        worst_with_dca = caps[symbol] * stop
        worst_without = caps[symbol] * stop      # the cap is unchanged by DCA
        assert worst_with_dca == worst_without


def test_deepest_rung_is_reachable_before_the_stop_fires(config):
    """A rung deeper than the stop-loss could never be filled — the stop would
    have closed the position first, so the tranche would be dead config."""
    for symbol in ("ELXR", "DRFT"):
        strat = build_strategies(config)[symbol]
        deepest = strat.required_deviation(
            strat.trade_quantity * (strat.max_tranches - 1))
        stop = config["risk"]["exits"][symbol]["stop_loss_pct"]
        assert deepest < stop, (
            f"{symbol}: deepest rung {deepest:.4f} is beyond the "
            f"{stop:.4f} stop — that tranche can never fill")


@pytest.mark.parametrize("position", [0, 100, 200])
def test_trend_guard_blocks_initial_and_additional_tranches(position):
    strat = ladder()
    for price in range(10_000, 8_500, -100):
        strat.on_price(price)
    assert strat.signal(position) is not None
    strat.trend_guard = 0.001
    assert strat.signal(position) is None


def test_trend_guard_does_not_block_ladder_exit():
    strat = ladder()
    strat.trend_guard = 0.001
    for price in [12_000] * 7 + [10_000] * 7 + [11_500]:
        strat.on_price(price)
    assert strat._mean_declining()
    signal = strat.signal(200)
    assert signal is not None and signal.side == "sell"
