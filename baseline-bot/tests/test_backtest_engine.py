"""Deterministic engine replay with a hand-computed P&L.

The scenario uses momentum (lookback 3, threshold 0.03, qty 100, offset 5 bps,
cap 1000) over a 6-tick NOVA series crafted to trigger exactly one buy that
fills, then one sell that closes it at a loss:

    idx price   event
    0   10000   warming up
    1   10200   warming up
    2   10400   change +4% >= 3% -> BUY (limit 10405), pending for idx 3
    3   10400   settle: 10400 <= 10405 -> BUY fills @ 10400 (pos 100, avg 10400)
                 on_price -> change +1.96% < 3% -> no new signal
    4   10000   settle: none. change -3.85% <= -3%, hold 100 -> SELL (limit 9995)
    5   10000   settle: 10000 >= 9995 -> SELL fills @ 10000 -> pnl (10000-10400)*100

Expected: realized -40000c, unrealized 0, total -40000, cash -40000,
trade_count 2, sell_count 1, win_rate 0.0, final_position 0,
equity_curve [0,0,0,0,-40000,-40000], max_drawdown -40000.
"""
from backtest.engine import replay

CFG = {"type": "momentum", "lookback_samples": 3, "momentum_threshold": 0.03,
       "trade_quantity": 100, "limit_offset_bps": 5}
SERIES = [(i, None, p) for i, p in enumerate(
    [10_000, 10_200, 10_400, 10_400, 10_000, 10_000])]


def test_hand_computed_pnl():
    res = replay("NOVA", CFG, {}, SERIES, max_position=1000)
    m = res["metrics"]
    assert m["realized_pnl_cents"] == -40_000
    assert m["unrealized_pnl_cents"] == 0
    assert m["total_pnl_cents"] == -40_000
    assert m["trade_count"] == 2
    assert m["sell_count"] == 1
    assert m["win_rate"] == 0.0
    assert m["final_position"] == 0
    assert m["max_drawdown_cents"] == -40_000
    assert [c["equity_cents"] for c in res["equity_curve"]] == [0, 0, 0, 0, -40_000, -40_000]


def test_signal_on_the_last_tick_never_fills():
    # A rising 3-tick series would signal a buy on the final tick, but there is
    # no next tick to fill against -> the anti-look-ahead guarantee.
    series = [(i, None, p) for i, p in enumerate([10_000, 10_200, 10_400])]
    res = replay("NOVA", CFG, {}, series, max_position=1000)
    assert res["metrics"]["trade_count"] == 0
    assert res["metrics"]["final_position"] == 0


def test_buy_is_clamped_to_the_position_cap():
    # Strong uptrend keeps buying; the cap (150) bounds the long.
    series = [(i, None, 10_000 + i * 400) for i in range(8)]
    res = replay("NOVA", CFG, {}, series, max_position=150)
    assert res["metrics"]["final_position"] <= 150


def test_win_rate_is_none_without_a_closing_sell():
    # Pure uptrend: momentum only ever buys, never realizes -> win_rate N/A.
    series = [(i, None, 10_000 + i * 400) for i in range(8)]
    res = replay("NOVA", CFG, {}, series, max_position=1000)
    assert res["metrics"]["sell_count"] == 0
    assert res["metrics"]["win_rate"] is None
