"""Starter unit tests for the momentum strategy. Prices are integer cents."""
from strategies.momentum import MomentumStrategy

CFG = {"lookback_samples": 3, "momentum_threshold": 0.01,
       "trade_quantity": 100, "limit_offset_bps": 5}


def test_no_signal_before_buffer_is_full():
    s = MomentumStrategy("NOVA", CFG)
    s.on_price(10_000)
    assert s.signal(position=0) is None


def test_buys_on_upward_momentum():
    s = MomentumStrategy("NOVA", CFG)
    for p in (10_000, 10_100, 10_300):  # +3% over the window
        s.on_price(p)
    sig = s.signal(position=0)
    assert sig is not None
    assert sig.side == "buy"
    assert sig.quantity == 100
    assert sig.limit_price >= 10_300  # pays up to get filled


def test_sells_to_close_on_downward_momentum_when_long():
    s = MomentumStrategy("NOVA", CFG)
    for p in (10_300, 10_100, 9_900):
        s.on_price(p)
    sig = s.signal(position=100)
    assert sig is not None and sig.side == "sell"


def test_long_only_no_sell_when_flat():
    s = MomentumStrategy("NOVA", CFG)
    for p in (10_300, 10_100, 9_900):
        s.on_price(p)
    assert s.signal(position=0) is None
