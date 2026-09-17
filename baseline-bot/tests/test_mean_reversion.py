"""Starter unit tests for the mean-reversion strategy. Prices are integer cents."""
from strategies.mean_reversion import MeanReversionStrategy

CFG = {"lookback_samples": 4, "deviation_threshold": 0.01,
       "trade_quantity": 50, "limit_offset_bps": 5}


def test_buys_when_price_dips_below_mean():
    s = MeanReversionStrategy("ELXR", CFG)
    for p in (10_000, 10_000, 10_000, 9_800):  # last ~1.5% below mean
        s.on_price(p)
    sig = s.signal(position=0)
    assert sig is not None and sig.side == "buy"


def test_sells_to_close_when_price_runs_above_mean_and_long():
    s = MeanReversionStrategy("ELXR", CFG)
    for p in (10_000, 10_000, 10_000, 10_200):
        s.on_price(p)
    sig = s.signal(position=50)
    assert sig is not None and sig.side == "sell"


def test_no_signal_inside_band():
    s = MeanReversionStrategy("ELXR", CFG)
    for p in (10_000, 10_000, 10_000, 10_000):
        s.on_price(p)
    assert s.signal(position=0) is None
