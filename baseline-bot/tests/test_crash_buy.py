"""Unit tests for the crash-buy strategy. Prices are integer cents."""
import pytest

from strategies.crash_buy import CrashBuyStrategy
from strategies.regime_policy import FAMILY_BY_CLASS, DEFAULT_POLICY, Regime

CFG = {
    "lookback_samples": 5,
    "crash_drop_pct": 0.05,
    "tranche_step_pct": 0.05,
    "tranche_scale": 2.0,
    "max_tranches": 3,
    "confirm_samples": 0,
    "recovery_fraction": 0.5,
    "abandon_drop_pct": 0.30,
    "trade_quantity": 100,
    "limit_offset_bps": 5,
}


def make(**over):
    return CrashBuyStrategy("CRVX", {**CFG, **over})


def feed(s, *prices):
    for p in prices:
        s.on_price(p)


def flat(s, price=10_000, n=5):
    feed(s, *([price] * n))


# --- detection ------------------------------------------------------------

def test_no_signal_without_a_crash():
    s = make()
    feed(s, 10_000, 10_050, 9_980, 10_020, 9_900)   # -1% is noise
    assert s.signal(position=0) is None
    assert not s.in_episode


def test_crash_opens_episode_and_buys_first_tranche():
    s = make()
    flat(s)
    s.on_price(9_400)                                # -6% off the window high
    sig = s.signal(position=0)
    assert sig is not None and sig.side == "buy"
    assert sig.quantity == 100
    assert s.in_episode and s.reference == 10_000 and s.tranches == 1


def test_reference_excludes_the_crashing_print():
    """A crash beginning on this sample is measured against what came before."""
    s = make(lookback_samples=3)
    feed(s, 10_000, 10_000, 9_400)
    assert s.signal(position=0).side == "buy"


def test_waits_for_full_lookback():
    s = make()
    feed(s, 10_000, 10_000, 9_000)                   # only 3 of 5 samples
    assert s.signal(position=0) is None


# --- the ladder -----------------------------------------------------------

def test_ladder_escalates_with_depth():
    s = make()
    flat(s)
    s.on_price(9_400)                                # -6%  -> tranche 1
    assert s.signal(0).quantity == 100
    s.on_price(9_300)                                # -7%  -> not yet -10%
    assert s.signal(100) is None
    s.on_price(8_900)                                # -11% -> tranche 2, 2x
    assert s.signal(100).quantity == 200
    s.on_price(8_400)                                # -16% -> tranche 3, 4x
    assert s.signal(300).quantity == 400
    s.on_price(7_900)                                # -21% -> capped
    assert s.signal(700) is None
    assert s.tranches == 3


def test_confirmation_delays_the_buy_until_price_stops_making_lows():
    s = make(confirm_samples=2)
    flat(s)
    s.on_price(9_400)                                # new low, counter 0
    assert s.signal(0) is None
    s.on_price(9_300)                                # new low again, counter 0
    assert s.signal(0) is None
    s.on_price(9_350)                                # not a new low, counter 1
    assert s.signal(0) is None
    s.on_price(9_360)                                # counter 2 -> buy
    sig = s.signal(0)
    assert sig is not None and sig.side == "buy"


def test_abandons_a_repricing_and_places_no_more_tranches():
    s = make()
    flat(s)
    s.on_price(9_400)
    assert s.signal(0).side == "buy"
    s.on_price(6_500)                                # -35% > abandon 30%
    assert s.signal(100) is None
    assert s.abandoned
    s.on_price(6_400)
    assert s.signal(100) is None                     # still nothing


def test_spread_gate_blocks_entries_only():
    class WideBook:
        def spread_bps(self, symbol):
            return 500
        def best_ask(self, symbol):
            return None
        def best_bid(self, symbol):
            return None

    s = make(max_spread_bps=50)
    s.orderbook = WideBook()
    flat(s)
    s.on_price(9_400)
    assert s.signal(0) is None                       # entry gated
    # Force the exit leg: recovery level is 9_700, print above it while long.
    s.on_price(9_800)
    sig = s.signal(100)
    assert sig is not None and sig.side == "sell"    # exit never gated


# --- the exit -------------------------------------------------------------

def test_sells_into_the_rebound_and_closes_the_episode():
    s = make()
    flat(s)
    s.on_price(9_000)                                # -10%, low = 9_000
    assert s.signal(0).side == "buy"
    # recovery_fraction 0.5: level = 10_000 - 0.5 * 1_000 = 9_500
    s.on_price(9_400)
    assert s.signal(100) is None
    s.on_price(9_550)
    sig = s.signal(100)
    assert sig is not None and sig.side == "sell" and sig.quantity == 100
    assert s.in_episode                              # still holding
    s.on_price(9_600)
    assert s.signal(0) is None                       # flat and recovered
    assert not s.in_episode                          # episode closed


def test_sell_clip_is_capped_at_position_and_uses_sell_quantity():
    s = make(sell_quantity=250)
    flat(s)
    s.on_price(9_000)
    s.signal(0)
    s.on_price(9_600)
    assert s.signal(700).quantity == 250
    assert s.signal(80).quantity == 80


def test_never_sells_when_flat():
    s = make()
    flat(s)
    s.on_price(9_000)
    s.signal(0)
    s.on_price(9_900)
    assert s.signal(0) is None


def test_recovery_tracks_the_episode_low_not_the_first_print():
    s = make()
    flat(s)
    s.on_price(9_400)                                # low 9_400 -> level 9_700
    s.signal(0)
    s.on_price(8_000)                                # low 8_000 -> level 9_000
    s.signal(100)
    s.on_price(9_100)                                # above the new level
    assert s.signal(300).side == "sell"


def test_stop_loss_closing_the_position_abandons_the_episode():
    """A held position that vanishes below the recovery level was stopped out;
    the ladder must not re-enter the same crash."""
    s = make()
    flat(s)
    s.on_price(9_400)
    s.signal(0)                                      # tranche 1
    s.on_price(9_300)
    s.signal(100)                                    # held
    s.on_price(9_200)
    assert s.signal(0) is None                       # gone -> abandoned
    assert s.abandoned
    s.on_price(8_800)                                # -12% would be tranche 2
    assert s.signal(0) is None


def test_reset_drops_the_episode():
    s = make()
    flat(s)
    s.on_price(9_000)
    s.signal(0)
    s.reset()
    assert not s.in_episode and s.tranches == 0 and len(s.prices) == 0


# --- config validation ----------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"crash_drop_pct": 0},
    {"crash_drop_pct": 1.5},
    {"tranche_scale": 0.5},
    {"max_tranches": 0},
    {"confirm_samples": -1},
    {"recovery_fraction": 0},
    {"recovery_fraction": 1.2},
    {"abandon_drop_pct": 0.05},          # equal to crash_drop_pct
    {"sell_quantity": 0},
])
def test_rejects_bad_config(bad):
    with pytest.raises(ValueError):
        make(**bad)


def test_optional_keys_default():
    s = CrashBuyStrategy("CRVX", {"lookback_samples": 5, "crash_drop_pct": 0.05,
                                  "trade_quantity": 100})
    assert s.tranche_step_pct == 0.05 and s.tranche_scale == 1.5
    assert s.max_tranches == 3 and s.confirm_samples == 2
    assert s.recovery_fraction == 0.6 and s.abandon_drop_pct == 0.35
    assert s.sell_clip == 100


# --- regime wiring --------------------------------------------------------

def test_has_a_regime_family_that_allows_shock():
    assert FAMILY_BY_CLASS["CrashBuyStrategy"] == "crash_buy"
    assert DEFAULT_POLICY["crash_buy"][Regime.SHOCK] == 1.0
    assert DEFAULT_POLICY["crash_buy"][Regime.WARMUP] == 0.0


def test_main_builds_it_from_config():
    from main import build_strategy
    s = build_strategy("CRVX", {"type": "crash_buy", **CFG})
    assert isinstance(s, CrashBuyStrategy)
