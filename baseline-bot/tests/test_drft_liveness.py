"""DRFT must actually trade, and asymmetric mean-reversion bands.

The bug these guard against: a symmetric 0.015 band made DRFT completely inert —
0 buys, therefore no position, therefore 0 sells — and nothing in the suite
noticed, because every other test asserted that a *signal* was correct rather
than that an instrument was *reachable*.

The mechanism is worth stating because it generalises: on a long-only exchange,
mean-reversion's sell leg can only ever reduce a long the buy leg built. If the
buy band is unreachable for an instrument's price shape, the whole strategy is
dead on that instrument — not merely mistuned.
"""
import random
from pathlib import Path

import pytest
import yaml

from main import build_strategies
from strategies.mean_reversion import MeanReversionStrategy


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def spiky_path(base=8000, ticks=600, seed=7):
    """A thin, event-driven name: quiet drift punctuated by upward spikes.

    "It spikes on volume then drops" — the moves are up, and the reversion is
    back to the base rather than below it, which is why a symmetric band fails.
    """
    rng = random.Random(seed)
    path = []
    for i in range(ticks):
        if i % 60 == 20:
            path += [base + 300, base + 500, base + 250]
        path.append(base + rng.randint(-25, 25))
    return path


def trade(strategy, path, cap):
    """Walk a path honestly: flat to start, and only buys can raise the position."""
    position, buys, sells = 0, 0, 0
    for price in path:
        strategy.on_price(price)
        signal = strategy.signal(position)        # sellable == held
        if signal is None:
            continue
        if signal.side == "buy" and position + signal.quantity <= cap:
            position += signal.quantity
            buys += 1
        elif signal.side == "sell" and position > 0:
            position -= min(signal.quantity, position)
            sells += 1
    return buys, sells


# --- the regression ------------------------------------------------------

def test_drft_actually_trades_under_the_real_config(config):
    """The test that was missing. An instrument nobody can trade is a bug."""
    strat = build_strategies(config)["DRFT"]
    cap = config["risk"]["max_position"]["DRFT"]
    buys, sells = trade(strat, spiky_path(), cap)
    assert buys > 0, "DRFT never enters — the buy band is unreachable"
    assert sells > 0, "DRFT never exits — it only accumulates"


def test_a_symmetric_band_would_make_drft_inert(config):
    """Documents the failure, so nobody 'simplifies' the bands back."""
    cfg = dict(config["strategy"]["DRFT"])
    cfg.pop("buy_deviation_threshold")
    cfg.pop("sell_deviation_threshold")
    cfg["deviation_threshold"] = 0.015
    buys, sells = trade(MeanReversionStrategy("DRFT", cfg), spiky_path(), 100)
    assert (buys, sells) == (0, 0)


def test_drft_respects_its_position_cap(config):
    """Trading more is only safe because the cap and the clip stay tiny."""
    strat = build_strategies(config)["DRFT"]
    cap = config["risk"]["max_position"]["DRFT"]
    position = 0
    for price in spiky_path():
        strat.on_price(price)
        signal = strat.signal(position)
        if signal and signal.side == "buy":
            if position + signal.quantity > cap:
                continue
            position += signal.quantity
        elif signal and signal.side == "sell" and position > 0:
            position -= min(signal.quantity, position)
        assert position <= cap


# --- asymmetric bands ----------------------------------------------------

def test_buy_band_is_the_narrower_one(config):
    strat = build_strategies(config)["DRFT"]
    assert strat.buy_threshold < strat.sell_threshold


def test_bands_default_to_symmetric():
    """An instrument that needs no asymmetry needs no extra keys — ELXR."""
    strat = MeanReversionStrategy("ELXR", {
        "lookback_samples": 15, "deviation_threshold": 0.004,
        "trade_quantity": 100})
    assert strat.buy_threshold == strat.sell_threshold == 0.004


def test_elxr_bands_are_unchanged_by_the_drft_work(config):
    elxr = build_strategies(config)["ELXR"]
    assert elxr.buy_threshold == elxr.sell_threshold == 0.004


def test_buy_fires_on_the_narrow_band_only():
    strat = MeanReversionStrategy("DRFT", {
        "lookback_samples": 15, "deviation_threshold": 0.006,
        "buy_deviation_threshold": 0.006, "sell_deviation_threshold": 0.012,
        "trade_quantity": 10})
    for _ in range(14):
        strat.on_price(10_000)
    strat.on_price(9_920)                      # ~-0.85%: past buy, not sell
    signal = strat.signal(0)
    assert signal is not None and signal.side == "buy"


def test_sell_needs_the_wider_band():
    strat = MeanReversionStrategy("DRFT", {
        "lookback_samples": 15, "deviation_threshold": 0.006,
        "buy_deviation_threshold": 0.006, "sell_deviation_threshold": 0.012,
        "trade_quantity": 10})
    for _ in range(14):
        strat.on_price(10_000)
    strat.on_price(10_100)                     # ~+0.93%: past buy band, not sell
    assert strat.signal(50) is None
    for _ in range(14):
        strat.on_price(10_000)
    strat.on_price(10_200)                     # ~+1.86%: past the sell band
    signal = strat.signal(50)
    assert signal is not None and signal.side == "sell"


def test_nonpositive_band_is_rejected():
    with pytest.raises(ValueError, match="positive fractions"):
        MeanReversionStrategy("DRFT", {
            "lookback_samples": 15, "deviation_threshold": 0.006,
            "buy_deviation_threshold": 0, "trade_quantity": 10})


# --- the risk controls that make it safe to trade at all -----------------

def test_drft_is_the_smallest_book_and_has_the_tightest_controls(config):
    """"Size it small, your own orders move its price" (James p.4); "a fifth of
    what you'd run on NOVA" (Priya)."""
    caps = {"NOVA": config["risk"]["max_position_primary"],
            "ELXR": config["risk"]["max_position_secondary"],
            **config["risk"]["max_position"]}
    assert caps["DRFT"] == min(caps.values())
    assert caps["DRFT"] * 5 <= caps["NOVA"]

    quantities = {s: c["trade_quantity"] for s, c in config["strategy"].items()}
    assert quantities["DRFT"] == min(quantities.values())

    exits = config["risk"]["exits"]["DRFT"]
    assert exits["stop_loss_pct"] <= 0.02
    # Fixed target, not a trail: a trail sells into the drop on a thin book.
    assert "take_profit_pct" in exits
    assert "trailing_take_profit" not in exits
