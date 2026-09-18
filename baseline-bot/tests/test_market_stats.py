"""Per-instrument market statistics for the risk model: bucketed ATR, regime with
hysteresis, book depth and rolling correlation. Prices are integer cents."""
import math

import pytest

from risk.stats import HIGH, LOW, NEUTRAL, MarketStats


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


def feed_bars(stats, symbol, closes, clock, bucket=10.0):
    """One tick per bar at the given close prices, then one tick that opens the
    next bar so the last close is committed."""
    for c in closes:
        stats.on_price(symbol, c)
        clock.advance(bucket)
    stats.on_price(symbol, closes[-1])


# --- ATR ------------------------------------------------------------------

def test_atr_is_unavailable_until_enough_bars_have_closed(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=3, clock=clock)
    feed_bars(s, "NOVA", [10_000, 10_100, 10_050], clock)   # only 2 true ranges
    assert s.atr("NOVA") is None
    assert s.atr_pct("NOVA") is None


def test_atr_is_the_mean_true_range_over_closed_bars(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=3, clock=clock)
    # closes 10000, 10100, 10050, 10200 -> TR = 100, 50, 150 (one tick per bar)
    feed_bars(s, "NOVA", [10_000, 10_100, 10_050, 10_200], clock)
    assert s.atr("NOVA") == pytest.approx(100.0)
    assert s.atr_pct("NOVA") == pytest.approx(100.0 / 10_200)


def test_true_range_uses_the_bar_high_low_and_the_previous_close(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=1, clock=clock)
    s.on_price("NOVA", 10_000)
    clock.advance(10)
    # Bar 2 gaps up to 10300, trades down to 10200, closes 10250:
    # TR = max(10300, 10000) - min(10200, 10000) = 300
    for p in (10_300, 10_200, 10_250):
        s.on_price("NOVA", p)
    clock.advance(10)
    s.on_price("NOVA", 10_250)
    assert s.atr("NOVA") == pytest.approx(300.0)


def test_an_empty_bar_carries_the_close_with_zero_range(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=2, clock=clock)
    s.on_price("NOVA", 10_000)
    clock.advance(10)
    s.on_price("NOVA", 10_100)          # TR 100
    clock.advance(30)                   # two silent bars, then a tick
    s.on_price("NOVA", 10_100)
    assert s.atr("NOVA") == pytest.approx(0.0)


def test_symbols_are_independent(clock):
    s = MarketStats(["NOVA", "ELXR"], bucket_seconds=10, atr_buckets=1, clock=clock)
    feed_bars(s, "NOVA", [10_000, 10_500], clock)
    assert s.atr("NOVA") == pytest.approx(500.0)
    assert s.atr("ELXR") is None


def test_unknown_symbol_is_ignored(clock):
    s = MarketStats(["NOVA"], clock=clock)
    s.on_price("ZZZZ", 100)
    assert s.last_price("ZZZZ") is None


# --- marks and session open ------------------------------------------------

def test_last_price_and_session_open_price(clock):
    s = MarketStats(["NOVA"], clock=clock)
    s.on_price("NOVA", 10_000)
    s.on_price("NOVA", 10_200)
    assert s.last_price("NOVA") == 10_200
    assert s.session_open_price("NOVA") == 10_000


def test_session_reset_clears_history_and_marks(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=1, clock=clock)
    feed_bars(s, "NOVA", [10_000, 10_500], clock)
    s.reset_for_session()
    assert s.atr("NOVA") is None
    assert s.last_price("NOVA") is None
    assert s.session_open_price("NOVA") is None
    assert s.regime("NOVA") is None


# --- regime with hysteresis ------------------------------------------------

def regime_stats(clock, confirm=2):
    return MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=2,
                       regime_thresholds={"NOVA": (0.002, 0.006)},
                       regime_confirm_buckets=confirm, clock=clock)


def test_regime_is_unknown_before_atr_is_ready(clock):
    s = regime_stats(clock)
    s.on_price("NOVA", 10_000)
    assert s.regime("NOVA") is None


def test_first_classification_is_adopted_after_confirmation(clock):
    s = regime_stats(clock, confirm=2)
    # TR 10, 10 -> ATR 10 -> 0.1% < 0.2% = LOW, twice.
    feed_bars(s, "NOVA", [10_000, 10_010, 10_020, 10_030], clock)
    assert s.regime("NOVA") == LOW


def test_regime_switches_only_after_confirm_bars(clock):
    s = regime_stats(clock, confirm=2)
    feed_bars(s, "NOVA", [10_000, 10_010, 10_020, 10_030], clock)
    assert s.regime("NOVA") == LOW
    # One volatile bar: ATR = (10 + 200)/2 = 105 -> 1.03% > 0.6% = HIGH candidate.
    clock.advance(0)
    s.on_price("NOVA", 10_230)
    clock.advance(10)
    s.on_price("NOVA", 10_230)
    assert s.regime("NOVA") == LOW          # not yet confirmed
    s.on_price("NOVA", 10_430)
    clock.advance(10)
    s.on_price("NOVA", 10_430)
    assert s.regime("NOVA") == HIGH


def test_neutral_between_the_thresholds(clock):
    s = regime_stats(clock, confirm=1)
    # TR 40, 40 -> ATR 40 -> ~0.4%: between 0.2% and 0.6%.
    feed_bars(s, "NOVA", [10_000, 10_040, 10_080], clock)
    assert s.regime("NOVA") == NEUTRAL


def test_default_thresholds_apply_to_symbols_without_their_own(clock):
    s = MarketStats(["AXON"], bucket_seconds=10, atr_buckets=1,
                    default_thresholds=(0.002, 0.006), regime_confirm_buckets=1,
                    clock=clock)
    feed_bars(s, "AXON", [10_000, 10_010], clock)
    assert s.regime("AXON") == LOW


# --- book depth ------------------------------------------------------------

def test_depth_is_none_before_any_book_update(clock):
    s = MarketStats(["DRFT"], clock=clock)
    assert s.depth("DRFT") is None


def test_depth_sums_the_resting_quantities_per_side(clock):
    s = MarketStats(["DRFT"], clock=clock)
    s.on_book("DRFT",
              bids=[{"price": 6000, "quantity": 50}, {"price": 5990, "quantity": 70}],
              asks=[{"price": 6010, "quantity": 30}, {"price": 6020, "quantity": 100},
                    {"price": None, "quantity": 5}])
    d = s.depth("DRFT")
    assert d == {"best_bid": 6000, "best_ask": 6010, "bid_depth": 120, "ask_depth": 130}


def test_empty_side_reports_zero_depth_and_no_best(clock):
    s = MarketStats(["DRFT"], clock=clock)
    s.on_book("DRFT", bids=[], asks=[{"price": 6010, "quantity": 30}])
    assert s.depth("DRFT") == {"best_bid": None, "best_ask": 6010,
                               "bid_depth": 0, "ask_depth": 30}


# --- correlation -----------------------------------------------------------

def test_correlation_is_unavailable_below_min_samples(clock):
    s = MarketStats(["NOVA", "AXON"], bucket_seconds=10, correlation_buckets=10,
                    correlation_min_samples=4, clock=clock)
    for a, b in [(100, 200), (101, 202), (102, 204)]:
        s.on_price("NOVA", a)
        s.on_price("AXON", b)
        clock.advance(10)
    s.on_price("NOVA", 102)
    s.on_price("AXON", 204)
    assert s.correlation("NOVA", "AXON") is None


def test_perfectly_aligned_moves_correlate_to_one(clock):
    s = MarketStats(["NOVA", "AXON"], bucket_seconds=10, correlation_buckets=10,
                    correlation_min_samples=4, clock=clock)
    nova = [10_000, 10_100, 10_050, 10_200, 10_150, 10_300]
    axon = [p * 2 for p in nova]
    for a, b in zip(nova, axon):
        s.on_price("NOVA", a)
        s.on_price("AXON", b)
        clock.advance(10)
    s.on_price("NOVA", nova[-1])
    s.on_price("AXON", axon[-1])
    assert s.correlation("NOVA", "AXON") == pytest.approx(1.0)
    assert s.correlation("AXON", "NOVA") == pytest.approx(1.0)


def test_opposite_moves_correlate_to_minus_one(clock):
    s = MarketStats(["NOVA", "ELXR"], bucket_seconds=10, correlation_buckets=10,
                    correlation_min_samples=4, clock=clock)
    nova = [10_000, 10_100, 10_050, 10_200, 10_150, 10_300]
    for a in nova:
        s.on_price("NOVA", a)
        s.on_price("ELXR", int(round(10_000 * 10_000 / a)))   # exact inverse
        clock.advance(10)
    s.on_price("NOVA", nova[-1])
    s.on_price("ELXR", int(round(10_000 * 10_000 / nova[-1])))
    assert s.correlation("NOVA", "ELXR") == pytest.approx(-1.0, abs=1e-3)


def test_flat_series_has_no_correlation(clock):
    s = MarketStats(["NOVA", "AXON"], bucket_seconds=10, correlation_buckets=10,
                    correlation_min_samples=3, clock=clock)
    for a in (10_000, 10_100, 10_050, 10_200, 10_150):
        s.on_price("NOVA", a)
        s.on_price("AXON", 5_000)
        clock.advance(10)
    s.on_price("NOVA", 10_150)
    s.on_price("AXON", 5_000)
    assert s.correlation("NOVA", "AXON") is None


def test_correlation_matrix_lists_every_pair_once(clock):
    s = MarketStats(["NOVA", "AXON", "ELXR"], clock=clock)
    m = s.correlation_matrix()
    assert set(m) == {"NOVA/AXON", "NOVA/ELXR", "AXON/ELXR"}
    assert all(v is None for v in m.values())


# --- status ----------------------------------------------------------------

def test_status_reports_per_symbol_figures(clock):
    s = MarketStats(["NOVA"], bucket_seconds=10, atr_buckets=1,
                    regime_confirm_buckets=1, default_thresholds=(0.002, 0.006),
                    clock=clock)
    feed_bars(s, "NOVA", [10_000, 10_500], clock)
    st = s.status()
    assert st["bucket_seconds"] == 10
    nova = st["instruments"]["NOVA"]
    assert nova["last_price"] == 10_500
    assert nova["atr"] == pytest.approx(500.0)
    assert nova["atr_pct"] == pytest.approx(500 / 10_500)
    assert nova["regime"] == HIGH
    assert nova["bars"] == 1
    assert math.isclose(nova["session_open_price"], 10_000)
