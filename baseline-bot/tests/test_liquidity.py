"""Liquidity filter: how DEEP the book is, as distinct from how wide.

Adeline Loh's complaint is the specification: "If we're getting lots of partial
fills on NOVA, our sizing is too big for the book." So a thin book shrinks the
entry clip rather than cancelling the trade — refusing gives up the trade, while
sizing to the book takes the part the market can actually fill.

Two payload details from exchange-api-reference.md §4 drive the arithmetic:
entries are one per resting ORDER rather than one per price, and every entry
names its owner — including us.
"""
from pathlib import Path

import pytest
import yaml

from exchange.orderbook import OrderBookCache
from main import build_strategies
from strategies.momentum import MomentumStrategy


@pytest.fixture
def config():
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def entry(price, quantity, team="BOT"):
    return {"price": price, "quantity": quantity, "team_name": team}


def book(asks=None, bids=None, team_name=None, stale_seconds=5.0):
    cache = OrderBookCache(stale_seconds=stale_seconds, team_name=team_name)
    cache.update("NOVA", bids or [], asks or [])
    return cache


def momentum(qty=50, floor=None):
    cfg = {"lookback_samples": 3, "momentum_threshold": 0.01,
           "trade_quantity": qty, "limit_offset_bps": 5}
    if floor is not None:
        cfg["min_depth_shares"] = floor
    return MomentumStrategy("NOVA", cfg)


# =========================================================================
# depth_at_or_better
# =========================================================================

def test_sums_several_orders_at_the_same_price():
    """One entry per resting ORDER, not per price — a level must be summed."""
    cache = book(asks=[entry(10_000, 60), entry(10_000, 40)])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 100


def test_buy_walks_asks_up_to_the_limit():
    cache = book(asks=[entry(10_000, 30), entry(10_100, 40), entry(10_500, 90)])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 30
    assert cache.depth_at_or_better("NOVA", "buy", 10_100) == 70
    assert cache.depth_at_or_better("NOVA", "buy", 10_500) == 160


def test_sell_walks_bids_down_to_the_limit():
    cache = book(bids=[entry(10_000, 30), entry(9_900, 40), entry(9_500, 90)])
    assert cache.depth_at_or_better("NOVA", "sell", 10_000) == 30
    assert cache.depth_at_or_better("NOVA", "sell", 9_900) == 70
    assert cache.depth_at_or_better("NOVA", "sell", 9_500) == 160


def test_our_own_resting_orders_are_not_liquidity():
    """An order crossing our own comes back 409 SELF_MATCH."""
    cache = book(asks=[entry(10_000, 60, team="TEAM DELTA"),
                       entry(10_000, 40, team="BOT")],
                 team_name="TEAM DELTA")
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 40


def test_every_house_bot_counts_as_liquidity():
    """All house bots report as "BOT"; only OUR name is excluded."""
    cache = book(asks=[entry(10_000, 60, team="BOT"),
                       entry(10_000, 40, team="TEAM ECHO")],
                 team_name="TEAM DELTA")
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 100


def test_unknown_team_name_counts_everything():
    """Without our name we cannot exclude — and overstating is the safe error
    only because the clip is then capped by trade_quantity anyway."""
    cache = book(asks=[entry(10_000, 60, team="TEAM DELTA")])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 60


def test_empty_side_is_zero_depth_not_unknown():
    cache = book(asks=[])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 0


def test_no_book_is_unknown_not_zero():
    """A missing book is "no opinion"; zero would mean "an empty market"."""
    assert OrderBookCache().depth_at_or_better("NOVA", "buy", 10_000) is None


def test_stale_book_is_unknown():
    """Same rule as the dashboard's stale values: do not present old data."""
    clock = [1000.0]
    cache = OrderBookCache(stale_seconds=5.0, clock=lambda: clock[0])
    cache.update("NOVA", [], [entry(10_000, 100)])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 100
    clock[0] += 6
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) is None


def test_malformed_entries_are_skipped():
    cache = book(asks=[entry(0, 100), {"quantity": 50}, entry(10_000, 40), None])
    assert cache.depth_at_or_better("NOVA", "buy", 10_000) == 40


# =========================================================================
# entry_size: shrink to the book
# =========================================================================

def test_deep_book_gets_the_full_clip():
    strat = momentum(qty=50)
    strat.orderbook = book(asks=[entry(10_100, 500)])
    assert strat.entry_size(10_100) == 50


def test_thin_book_shrinks_the_clip():
    """Trade smaller rather than eat a partial fill."""
    strat = momentum(qty=50)
    strat.orderbook = book(asks=[entry(10_100, 18)])
    assert strat.entry_size(10_100) == 18


def test_depth_below_the_floor_places_nothing():
    """A 2-share fill moves the price and tells us nothing."""
    strat = momentum(qty=50, floor=10)
    strat.orderbook = book(asks=[entry(10_100, 2)])
    assert strat.entry_size(10_100) == 0


def test_no_floor_means_any_depth_is_tradeable():
    strat = momentum(qty=50)
    strat.orderbook = book(asks=[entry(10_100, 2)])
    assert strat.entry_size(10_100) == 2


def test_unknown_depth_falls_back_to_the_full_clip():
    """A stale feed is not an empty market, and pricing already has a fallback."""
    strat = momentum(qty=50, floor=10)
    strat.orderbook = OrderBookCache()
    assert strat.entry_size(10_100) == 50


def test_no_book_at_all_falls_back_to_the_full_clip():
    strat = momentum(qty=50, floor=10)
    assert strat.orderbook is None
    assert strat.entry_size(10_100) == 50


# =========================================================================
# Through the strategies
# =========================================================================

def uptrend(strat):
    for i in range(strat.lookback):
        strat.on_price(10_000 + i * 200)


def test_momentum_buy_is_sized_to_available_depth():
    strat = momentum(qty=50)
    uptrend(strat)
    strat.orderbook = book(asks=[entry(99_999, 12)])   # limit will be ~10_405
    signal = strat.signal(0)
    assert signal.side == "buy" and signal.quantity == 12


def test_momentum_buy_is_refused_below_the_floor():
    strat = momentum(qty=50, floor=10)
    uptrend(strat)
    strat.orderbook = book(asks=[entry(99_999, 3)])
    assert strat.signal(0) is None


def test_liquidity_never_gates_an_exit(config):
    """A thin book is exactly when you most want out."""
    strat = build_strategies(config)["NOVA"]
    strat.min_depth_shares = 10_000                    # impossible floor
    strat.orderbook = book(asks=[entry(10_000, 1)], bids=[entry(9_000, 1)])
    for i in range(strat.lookback):                    # clear downtrend
        strat.on_price(15_000 - i * 200)
    signal = strat.signal(300)
    assert signal is not None and signal.side == "sell"
    # Sized by the strategy's own rule — min(clip, held) — and emphatically not
    # by the 1 share of depth resting on the bid.
    assert signal.quantity == min(strat.trade_quantity, 300)


def test_mean_reversion_entry_is_sized_to_depth(config):
    strat = build_strategies(config)["ELXR"]
    strat.trend_guard = 0  # Isolate depth sizing from the declining-trend gate.
    for _ in range(strat.lookback - 1):
        strat.on_price(10_000)
    strat.on_price(9_500)
    strat.orderbook = book(asks=[entry(99_999, 30)])
    strat.symbol = "NOVA"                              # book fixture keys NOVA
    signal = strat.signal(0)
    assert signal.side == "buy" and signal.quantity == 30


def test_follower_entry_is_sized_to_depth(config):
    strategies = build_strategies(config)
    axon, nova = strategies["AXON"], strategies["NOVA"]
    for _ in range(axon.breakout_samples - 1):
        nova.on_price(10_000)
        axon.on_price(20_000)
    nova.on_price(10_500)                              # breakout
    axon.on_price(20_000)
    axon.orderbook = book(asks=[entry(99_999, 22)])
    axon.symbol = "NOVA"
    signal = axon.signal(0)
    assert signal.side == "buy" and signal.quantity == 22


# =========================================================================
# The real config
# =========================================================================

def test_every_instrument_has_a_depth_floor(config):
    floors = config["orderbook"]["min_depth_shares"]
    for symbol in build_strategies(config):
        assert symbol in floors, f"{symbol} has no min_depth_shares"


def test_floors_are_below_their_clips(config):
    """A floor at or above the clip would refuse every partially-filled book,
    which is the behaviour this filter exists to replace."""
    floors = config["orderbook"]["min_depth_shares"]
    for symbol, strat_cfg in config["strategy"].items():
        assert floors[symbol] < strat_cfg["trade_quantity"], symbol


def test_drft_has_the_lowest_bar(config):
    """Thin by nature: a high floor would make it inert again."""
    floors = config["orderbook"]["min_depth_shares"]
    assert floors["DRFT"] == min(floors.values())
