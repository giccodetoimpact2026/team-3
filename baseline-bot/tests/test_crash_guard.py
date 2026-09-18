"""Crash guard: a 15% drop from the window high dumps the whole position into
the resting bids with one sell order, then keeps the strategies quiet."""
from unittest.mock import Mock

import pytest

from exchange.results import Outcome, SubmitResult
from risk.crash_guard import CrashGuard, CrashResponder, sweep_price


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def guard(clock):
    return CrashGuard(["NOVA", "AXON"], drop_pct=0.15, confirm_ticks=3,
                      floor_pct=0.03, cooldown_seconds=300, clock=clock)


# --- trigger -------------------------------------------------------------

def test_no_trigger_while_price_stays_above_threshold(guard):
    for p in (10000, 10200, 9000, 8600):   # -14% at worst
        assert guard.on_price("NOVA", p) is False


def test_single_breaching_tick_does_not_trigger(guard):
    guard.on_price("NOVA", 10000)
    assert guard.on_price("NOVA", 8400) is False


def test_three_consecutive_breaching_ticks_trigger_once(guard):
    guard.on_price("NOVA", 10000)
    assert guard.on_price("NOVA", 8400) is False
    assert guard.on_price("NOVA", 8300) is False
    assert guard.on_price("NOVA", 8200) is True
    assert guard.on_price("NOVA", 8100) is False   # already fired: cooldown


def test_recovery_between_breaches_resets_the_count(guard):
    guard.on_price("NOVA", 10000)
    guard.on_price("NOVA", 8400)
    guard.on_price("NOVA", 8400)
    guard.on_price("NOVA", 9500)
    guard.on_price("NOVA", 8400)
    assert guard.on_price("NOVA", 8400) is False


def test_high_tracks_the_window_high_not_the_first_price(guard):
    guard.on_price("NOVA", 10000)
    guard.on_price("NOVA", 12000)
    for p in (10100, 10100):
        assert guard.on_price("NOVA", p) is False
    assert guard.on_price("NOVA", 10100) is True   # -15.8% from 12000


def test_instruments_are_independent(guard):
    guard.on_price("NOVA", 10000)
    guard.on_price("AXON", 10000)
    for _ in range(3):
        guard.on_price("NOVA", 8000)
    assert guard.in_cooldown("NOVA")
    assert not guard.in_cooldown("AXON")


def test_unknown_symbol_is_ignored(guard):
    assert guard.on_price("ZZZZ", 100) is False


def test_cooldown_expires_and_rearms_from_the_new_level(guard, clock):
    guard.on_price("NOVA", 10000)
    for _ in range(3):
        guard.on_price("NOVA", 8000)
    clock.advance(301)
    assert not guard.in_cooldown("NOVA")
    # High was reset to the dump level; a bounce to 8500 must not fire.
    for p in (8500, 8500, 8500):
        assert guard.on_price("NOVA", p) is False
    for p in (7200, 7200):
        guard.on_price("NOVA", p)
    assert guard.on_price("NOVA", 7200) is True    # -15.3% from 8500


def test_session_reset_clears_highs_and_cooldowns(guard):
    guard.on_price("NOVA", 10000)
    for _ in range(3):
        guard.on_price("NOVA", 8000)
    guard.reset_for_session()
    assert not guard.in_cooldown("NOVA")
    guard.on_price("NOVA", 8000)
    assert guard.on_price("NOVA", 7000) is False   # fresh count, one tick only


def test_status_reports_per_symbol_state(guard):
    guard.on_price("NOVA", 10000)
    s = guard.status()
    assert s["NOVA"]["high"] == 10000
    assert s["NOVA"]["cooldown"] is False
    assert s["drop_pct"] == 0.15


# --- sweep price ---------------------------------------------------------

def bids(*levels):
    return [{"price": p, "quantity": q, "team_name": "BOT"} for p, q in levels]


def test_sweep_price_is_the_level_that_absorbs_the_whole_quantity():
    book = bids((10000, 300), (9950, 400), (9900, 500))
    assert sweep_price(book, 700, floor=9000) == 9950


def test_sweep_price_uses_the_lowest_bid_when_the_book_is_too_thin():
    book = bids((10000, 300), (9950, 100))
    assert sweep_price(book, 1000, floor=9000) == 9950


def test_sweep_price_never_goes_below_the_floor():
    book = bids((10000, 10), (5000, 5000))
    assert sweep_price(book, 1000, floor=9700) == 9700


def test_sweep_price_with_an_empty_book_rests_at_the_floor():
    assert sweep_price([], 1000, floor=9700) == 9700


def test_sweep_price_ignores_malformed_levels():
    book = [{"price": None, "quantity": 5}, {"price": 9990, "quantity": 2000}]
    assert sweep_price(book, 1000, floor=9000) == 9990


# --- responder: ties trigger, book and OrderManager.flatten together ------

@pytest.fixture
def client():
    c = Mock()
    c.get_orderbook.return_value = {
        "symbol": "NOVA", "bids": bids((8200, 500), (8150, 600)), "asks": []}
    return c


@pytest.fixture
def orders():
    o = Mock()
    o.flatten.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="d1", status="FILLED",
        remaining_qty=0, filled_qty=1000)
    return o


def test_responder_dumps_at_the_sweep_price_when_the_guard_fires(guard, client, orders):
    r = CrashResponder(guard, client, orders)
    guard.on_price("NOVA", 10000)
    for p in (8200, 8200):
        r.on_price("NOVA", p)
    assert not orders.flatten.called
    r.on_price("NOVA", 8200)
    orders.flatten.assert_called_once()
    symbol, pricer = orders.flatten.call_args.args
    assert symbol == "NOVA"
    # 1000 shares need both levels; floor is 3% under 8200 = 7954.
    assert pricer(1000) == 8150
    assert pricer(400) == 8200


def test_responder_floor_is_relative_to_the_trigger_price(guard, client, orders):
    client.get_orderbook.return_value = {"bids": bids((8200, 10), (100, 5000)), "asks": []}
    r = CrashResponder(guard, client, orders)
    guard.on_price("NOVA", 10000)
    for p in (8000, 8000, 8000):
        r.on_price("NOVA", p)
    _, pricer = orders.flatten.call_args.args
    assert pricer(1000) == 7760   # 8000 - 3%


def test_responder_still_dumps_when_the_book_read_fails(guard, client, orders):
    client.get_orderbook.side_effect = RuntimeError("boom")
    r = CrashResponder(guard, client, orders)
    guard.on_price("NOVA", 10000)
    for p in (8000, 8000, 8000):
        r.on_price("NOVA", p)
    _, pricer = orders.flatten.call_args.args
    assert pricer(1000) == 7760


def test_responder_suppresses_strategies_during_cooldown(guard, client, orders):
    r = CrashResponder(guard, client, orders)
    assert r.allows_strategy("NOVA")
    guard.on_price("NOVA", 10000)
    for p in (8000, 8000, 8000):
        r.on_price("NOVA", p)
    assert not r.allows_strategy("NOVA")
    assert r.allows_strategy("AXON")


def test_responder_reprices_once_if_the_dump_is_still_resting(guard, client, orders, clock):
    orders.flatten.return_value = SubmitResult(
        Outcome.ACKNOWLEDGED, "k", order_id="d1", status="OPEN", remaining_qty=1000)
    orders.has_active.return_value = True
    r = CrashResponder(guard, client, orders, reprice_after=5.0)
    guard.on_price("NOVA", 10000)
    for p in (8000, 8000, 8000):
        r.on_price("NOVA", p)
    r.on_price("NOVA", 7900)
    assert orders.flatten.call_count == 1
    clock.advance(6)
    r.on_price("NOVA", 7900)
    assert orders.flatten.call_count == 2
    clock.advance(6)
    r.on_price("NOVA", 7800)
    assert orders.flatten.call_count == 2      # only one chase
