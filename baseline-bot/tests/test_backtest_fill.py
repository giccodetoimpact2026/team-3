"""Unit tests for the backtest fill model. Prices are integer cents."""
from backtest.fill import limit_from_reference, try_fill


def test_limit_matches_strategy_base_formula():
    # Mirrors strategies.base.BaseStrategy._limit_price exactly.
    assert limit_from_reference(10_000, "buy", 5) == 10_005
    assert limit_from_reference(10_000, "sell", 5) == 9_995
    assert limit_from_reference(20_000, "buy", 5) == 20_010


def test_buy_fills_at_next_price_when_within_limit():
    # limit 10_005; next tick 10_000 <= limit -> fills at the better price.
    assert try_fill("buy", 10_005, 10_000) == 10_000
    # next tick exactly at the limit -> fills at the limit.
    assert try_fill("buy", 10_005, 10_005) == 10_005


def test_buy_does_not_fill_when_market_runs_up_past_limit():
    assert try_fill("buy", 10_005, 10_006) is None


def test_sell_fills_at_next_price_when_within_limit():
    # limit 9_995; next tick 10_000 >= limit -> fills at the better price.
    assert try_fill("sell", 9_995, 10_000) == 10_000
    assert try_fill("sell", 9_995, 9_995) == 9_995


def test_sell_does_not_fill_when_market_runs_down_past_limit():
    assert try_fill("sell", 9_995, 9_994) is None


def test_slippage_moves_the_fill_against_you():
    # 100 bps = 1%. Buy pays more, sell gets less.
    assert try_fill("buy", 10_005, 10_000, slippage_bps=100) == 10_000 + 100
    assert try_fill("sell", 9_995, 10_000, slippage_bps=100) == 10_000 - 100
