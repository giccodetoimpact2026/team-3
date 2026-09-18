"""Instrument risk model: profiles, sizing caps, drawdown, instrument loss,
stop-loss and status. Prices and money are integer cents."""
from unittest.mock import Mock

import pytest

from risk.limits import RiskLimits
from risk.model import DEFENSIVE, NORMAL, RiskModel, StopTrigger
from risk.profiles import load_profiles
from risk.stats import HIGH, LOW, NEUTRAL, MarketStats


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


SYMBOLS = ["NOVA", "AXON", "CRVX", "DRFT", "ELXR"]


def cfg(**overrides):
    base = {
        "enabled": True,
        "size_mode": "cap",
        "bucket_seconds": 10,
        "atr_buckets": 1,
        "regime_confirm_buckets": 1,
        "correlation_buckets": 10,
        "correlation_min_samples": 3,
        "correlation_alert": 0.7,
        "risk_per_trade_pct": 0.005,
        "max_notional_per_symbol_pct": 0.35,
        "max_gross_exposure_pct": 0.90,
        "instrument_loss_limit_pct": 0.02,
        "drawdown": {"warn_pct": 0.10, "limit_pct": 0.15, "action": "block_buys"},
        "stop_loss": {"enabled": True},
        "regime_factors": {
            "momentum": {"LOW": 0.0, "NEUTRAL": 0.5, "HIGH": 1.0},
            "mean_reversion": {"LOW": 1.0, "NEUTRAL": 0.75, "HIGH": 0.5},
            "dca": {"LOW": 1.0, "NEUTRAL": 1.0, "HIGH": 0.5},
        },
        "groups": {"energy_freight": {"members": ["NOVA", "AXON"], "max_exposure_pct": 0.45}},
        "default_profile": {
            "personality": "unknown", "size_multiplier": 0.5, "liquidity": "liquid",
            "depth_fraction": 0.5, "max_qty_without_book": 10_000,
            "low_vol": 0.004, "high_vol": 0.010, "stop_atr_mult": 4.0,
            "min_stop_pct": 0.04, "max_stop_pct": 0.10, "confirm_ticks": 3,
            "cooldown_seconds": 120,
        },
        "profiles": {
            "NOVA": {"personality": "trending", "size_multiplier": 1.0},
            "AXON": {"personality": "follower", "size_multiplier": 0.75},
            "DRFT": {"personality": "thin_event", "size_multiplier": 0.2,
                     "liquidity": "thin", "depth_fraction": 0.2,
                     "max_qty_without_book": 20},
            "ELXR": {"personality": "range_bound", "size_multiplier": 1.0},
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def portfolio(cash, positions=(), session_id="w1"):
    total = cash + sum(p.get("quantity", 0) * p.get("last_price", 0) for p in positions)
    return {"session_id": session_id, "cash": cash, "reserved_cash": 0,
            "total_value": total, "positions": list(positions)}


def held(symbol, quantity, avg_cost, last_price=None, realized=0):
    return {"symbol": symbol, "quantity": quantity, "reserved_qty": 0,
            "avg_cost": avg_cost, "last_price": last_price or avg_cost,
            "realized_pnl": realized}


@pytest.fixture
def clock():
    return FakeClock()


def build(clock, config=None, symbols=SYMBOLS, halt=None):
    config = config or cfg()
    profiles = load_profiles(config, symbols)
    stats = MarketStats(symbols, bucket_seconds=config["bucket_seconds"],
                        atr_buckets=config["atr_buckets"],
                        regime_thresholds={s: (p.low_vol, p.high_vol)
                                           for s, p in profiles.items()},
                        regime_confirm_buckets=config["regime_confirm_buckets"],
                        correlation_buckets=config["correlation_buckets"],
                        correlation_min_samples=config["correlation_min_samples"],
                        clock=clock)
    limits = RiskLimits(1_000_000, 1_000_000, "NOVA", "ELXR", max_position_default=1_000_000)
    model = RiskModel(profiles, stats, limits, config, clock=clock, halt=halt)
    return model, stats


def set_regime(stats, clock, symbol, regime, price=10_000):
    """Close bars until the ATR-based regime lands where the test wants it.
    Thresholds are 0.4% / 1.0% of price; atr_buckets is 1 and confirm is 1."""
    step = {LOW: 10, NEUTRAL: 60, HIGH: 200}[regime]
    stats.on_price(symbol, price)
    clock.advance(10)
    stats.on_price(symbol, price + step)
    clock.advance(10)
    stats.on_price(symbol, price)     # closes the bar with TR = step
    assert stats.regime(symbol) == regime


# --- profiles ----------------------------------------------------------------

def test_profiles_merge_defaults_overrides_and_groups():
    p = load_profiles(cfg(), ["NOVA", "DRFT", "ZZZZ"])
    assert p["NOVA"].size_multiplier == 1.0 and p["NOVA"].group == "energy_freight"
    assert p["NOVA"].min_stop_pct == 0.04                     # inherited default
    assert p["DRFT"].liquidity == "thin" and p["DRFT"].depth_fraction == 0.2
    assert p["DRFT"].group is None
    assert p["ZZZZ"].personality == "unknown" and p["ZZZZ"].size_multiplier == 0.5


# --- sizing ------------------------------------------------------------------

def test_size_multiplier_scales_the_request(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("DRFT", "buy", 100, 10_000, 0, source="dca") == (
        20, "size_multiplier=0.2")


def test_full_size_request_passes_untouched(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0, source="momentum") == (100, None)


def test_momentum_is_suspended_in_a_low_regime(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    set_regime(stats, clock, "NOVA", LOW)
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0, source="momentum") == (
        0, "regime LOW: momentum suspended")


def test_neutral_regime_halves_momentum_size(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    set_regime(stats, clock, "NOVA", NEUTRAL)
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0, source="momentum") == (
        50, "regime NEUTRAL x0.5")


def test_high_regime_keeps_momentum_at_full_size(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    set_regime(stats, clock, "NOVA", HIGH)
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0, source="momentum") == (100, None)


def test_unknown_source_gets_no_regime_factor(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    set_regime(stats, clock, "NOVA", LOW)
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0) == (100, None)


def test_vol_cap_limits_the_risk_budget_at_the_widest_stop_without_atr(clock):
    """Equity 100M cents, 0.5% budget = 500,000; no ATR -> stop 10% of 10,000 =
    1,000 cents -> at most 500 shares."""
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("NOVA", "buy", 2_000, 10_000, 0, source="momentum") == (
        500, "vol_cap=500")


def test_vol_cap_uses_the_atr_stop_distance_when_available(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    # One closed bar with TR 150 -> ATR 150 -> 4 x 150 = 600 cents (between 4% and 10%).
    stats.on_price("NOVA", 10_000)
    clock.advance(10)
    stats.on_price("NOVA", 10_150)
    clock.advance(10)
    stats.on_price("NOVA", 10_000)
    qty, reason = model.adjust_intent("NOVA", "buy", 2_000, 10_000, 0)
    assert (qty, reason) == (833, "vol_cap=833")


def test_thin_name_is_capped_to_a_fraction_of_visible_ask_depth(clock):
    model, stats = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(100_000_000))
    stats.on_book("DRFT", bids=[], asks=[{"price": 10_010, "quantity": 60},
                                         {"price": 10_020, "quantity": 40}])
    assert model.adjust_intent("DRFT", "buy", 500, 10_000, 0, source="dca") == (
        20, "depth_cap=20")


def test_thin_name_without_a_book_uses_the_fixed_ceiling(clock):
    model, _ = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("DRFT", "buy", 500, 10_000, 0, source="dca") == (
        20, "depth_cap=20")


def test_notional_cap_counts_the_projected_position(clock):
    """35% of 100M = 35M cents = 3,500 shares at 10,000; 3,000 projected leaves 500."""
    model, _ = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("NOVA", "buy", 1_000, 10_000, 3_000) == (
        500, "notional_cap=500")


def test_group_cap_counts_the_other_members_held(clock):
    """Group 45% of 100M = 45M; AXON holds 30M; NOVA may add 15M = 1,500 shares."""
    model, _ = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(70_000_000, [held("AXON", 3_000, 10_000)]))
    assert model.adjust_intent("NOVA", "buy", 2_000, 10_000, 0) == (
        1_500, "group_cap=1500 (energy_freight)")


def test_gross_cap_counts_every_position(clock):
    """Gross 90% of 100M = 90M; ELXR holds 80M; NOVA may add 10M = 1,000 shares."""
    model, _ = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(20_000_000, [held("ELXR", 8_000, 10_000)]))
    assert model.adjust_intent("NOVA", "buy", 2_000, 10_000, 0) == (
        1_000, "gross_cap=1000")


def test_a_cap_below_one_share_refuses_the_buy(clock):
    model, _ = build(clock, cfg(risk_per_trade_pct=0.5))
    model.on_portfolio(portfolio(10_000_000, [held("ELXR", 9_000, 10_000)]))
    qty, reason = model.adjust_intent("NOVA", "buy", 100, 10_000, 0)
    assert qty == 0 and reason.startswith("gross_cap=0")


def test_sells_are_never_reduced(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(80_000_000))          # 20% drawdown -> DEFENSIVE
    assert model.mode == DEFENSIVE
    assert model.adjust_intent("NOVA", "sell", 100, 10_000, 0) == (100, None)


def test_target_size_mode_sizes_from_the_risk_budget(clock):
    model, _ = build(clock, cfg(size_mode="target"))
    model.on_portfolio(portfolio(100_000_000))
    assert model.adjust_intent("NOVA", "buy", 10, 10_000, 0) == (500, "vol_cap=500")


def test_limits_protocol_delegates_to_risk_limits(clock):
    model, _ = build(clock)
    assert model.max_position("NOVA") == 1_000_000
    assert model.allows_buy("NOVA", 0, 10)
    assert not model.allows_sell("NOVA", 5, 10)


def test_disabled_model_passes_everything_through(clock):
    model, _ = build(clock, cfg(enabled=False))
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(50_000_000))
    assert model.mode == NORMAL
    assert model.adjust_intent("DRFT", "buy", 500, 10_000, 0, source="dca") == (500, None)


# --- drawdown ----------------------------------------------------------------

def test_baseline_is_the_first_non_zero_equity(clock):
    model, _ = build(clock)
    model.on_portfolio({"session_id": "", "cash": 0, "total_value": 0, "positions": []})
    assert model.baseline_equity is None
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(120_000_000))
    assert model.baseline_equity == 100_000_000
    assert model.peak_equity == 120_000_000


def test_drawdown_warning_alerts_once_and_keeps_trading(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(89_000_000))
    model.on_portfolio(portfolio(88_000_000))
    kinds = [a["kind"] for a in model.alerts()]
    assert kinds == ["drawdown_warn"]
    assert model.mode == NORMAL
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0) == (100, None)


def test_drawdown_limit_blocks_buys_for_the_rest_of_the_window(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(84_000_000))
    assert model.mode == DEFENSIVE
    assert [a["kind"] for a in model.alerts()] == ["drawdown_warn", "drawdown_limit"]
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0) == (
        0, "drawdown limit: buys blocked")
    model.on_portfolio(portfolio(99_000_000))         # recovery does not re-arm
    assert model.mode == DEFENSIVE


def test_drawdown_is_marked_live_from_feed_prices(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(0, [held("NOVA", 10_000, 10_000)]))   # equity 100M
    stats.on_price("NOVA", 8_400)
    model.on_price("NOVA", 8_400)
    assert model.equity() == 84_000_000
    assert model.mode == DEFENSIVE


def test_drawdown_action_halt_calls_the_kill_switch_once(clock):
    halt = Mock(return_value=0)
    model, _ = build(clock, cfg(drawdown={"action": "halt"}), halt=halt)
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(84_000_000))
    model.on_portfolio(portfolio(83_000_000))
    halt.assert_called_once()


def test_drawdown_action_alert_only_keeps_buying(clock):
    model, _ = build(clock, cfg(drawdown={"action": "alert_only"}))
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(84_000_000))
    assert [a["kind"] for a in model.alerts()] == ["drawdown_warn", "drawdown_limit"]
    assert model.mode == NORMAL
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0) == (100, None)


# --- instrument loss limit -----------------------------------------------------

def test_instrument_session_loss_pauses_its_buys_only(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("ELXR", 1_000, 10_000)]))   # baseline 100M
    stats.on_price("ELXR", 7_900)                    # -2.1M on ELXR > 2% of 100M
    model.on_price("ELXR", 7_900)
    assert model.paused("ELXR")
    assert model.adjust_intent("ELXR", "buy", 100, 7_900, 0)[0] == 0
    assert model.adjust_intent("ELXR", "sell", 100, 7_900, 0) == (100, None)
    assert model.adjust_intent("NOVA", "buy", 100, 10_000, 0) == (100, None)
    assert [a["kind"] for a in model.alerts()].count("instrument_loss") == 1


def test_realized_losses_count_toward_the_instrument_limit(clock):
    model, _ = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    model.on_portfolio(portfolio(97_500_000, [held("CRVX", 0, 0, realized=-2_500_000)]))
    assert model.paused("CRVX")


def test_manual_resume_lifts_the_pause(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("ELXR", 1_000, 10_000)]))
    stats.on_price("ELXR", 7_900)
    model.on_price("ELXR", 7_900)
    model.resume("ELXR")
    assert not model.paused("ELXR")


# --- stop-loss -----------------------------------------------------------------

def test_stop_triggers_after_confirm_ticks_below_the_level(clock):
    """No ATR -> stop is max_stop_pct 10% of cost 10,000 -> level 9,000."""
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    for price in (9_000, 9_000):
        stats.on_price("NOVA", price)
        assert model.on_price("NOVA", price) is None
    stats.on_price("NOVA", 8_990)
    trigger = model.on_price("NOVA", 8_990)
    assert trigger == StopTrigger("NOVA", 8_990, 9_000, 10_000, 0.10)


def test_a_bounce_above_the_level_resets_the_count(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    for price in (9_000, 9_000, 9_100, 9_000, 9_000):
        stats.on_price("NOVA", price)
        assert model.on_price("NOVA", price) is None


def test_stop_distance_follows_atr_inside_its_bounds(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    # ATR 150 -> 4 x 150 = 600 -> level 9,400 (between 4% = 400 and 10% = 1,000).
    stats.on_price("NOVA", 10_000)
    clock.advance(10)
    stats.on_price("NOVA", 10_150)
    clock.advance(10)
    stats.on_price("NOVA", 10_000)
    assert model.stop_level("NOVA") == 9_400
    for price in (9_400, 9_400):
        stats.on_price("NOVA", price)
        model.on_price("NOVA", price)
    stats.on_price("NOVA", 9_400)
    assert model.on_price("NOVA", 9_400) == StopTrigger("NOVA", 9_400, 9_400, 10_000, 0.06)


def test_stop_reference_falls_back_to_the_session_open_price(clock):
    model, stats = build(clock)
    stats.on_price("NOVA", 10_000)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 0, last_price=10_000)]))
    assert model.stop_level("NOVA") == 9_000


def test_no_stop_when_flat_or_disabled(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(100_000_000))
    for price in (5_000, 5_000, 5_000):
        stats.on_price("NOVA", price)
        assert model.on_price("NOVA", price) is None
    assert model.stop_level("NOVA") is None

    model, stats = build(clock, cfg(stop_loss={"enabled": False}))
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    for price in (8_000, 8_000, 8_000):
        stats.on_price("NOVA", price)
        assert model.on_price("NOVA", price) is None


def test_stop_cooldown_blocks_entry_then_expires(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    for price in (8_900, 8_900, 8_900):
        stats.on_price("NOVA", price)
        model.on_price("NOVA", price)
    assert not model.allows_entry("NOVA")
    assert model.allows_entry("ELXR")
    assert model.adjust_intent("NOVA", "buy", 100, 8_900, 0) == (0, "stop-loss cooldown")
    clock.advance(121)
    assert model.allows_entry("NOVA")
    assert [a["kind"] for a in model.alerts()] == ["stop_loss"]


def test_stop_fires_once_per_cooldown(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    fired = 0
    for price in (8_900, 8_900, 8_900, 8_800, 8_800, 8_800):
        stats.on_price("NOVA", price)
        fired += model.on_price("NOVA", price) is not None
    assert fired == 1


# --- correlation alert ---------------------------------------------------------

def test_measured_correlation_between_held_names_outside_a_group_alerts(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(0, [held("NOVA", 1_000, 10_000), held("ELXR", 1_000, 10_000)]))
    for price in (10_000, 10_100, 10_050, 10_200, 10_150):
        stats.on_price("NOVA", price)
        stats.on_price("ELXR", price)
        clock.advance(10)
    stats.on_price("NOVA", 10_150)
    stats.on_price("ELXR", 10_150)
    model.on_price("NOVA", 10_150)
    model.on_price("NOVA", 10_150)
    alerts = [a for a in model.alerts() if a["kind"] == "correlation"]
    assert len(alerts) == 1 and "NOVA/ELXR" in alerts[0]["message"]


# --- session reset and status -----------------------------------------------------

def test_session_reset_clears_baseline_mode_pauses_and_cooldowns(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(90_000_000, [held("NOVA", 1_000, 10_000)]))
    model.on_portfolio(portfolio(70_000_000, [held("NOVA", 1_000, 10_000)]))   # DEFENSIVE
    for price in (8_900, 8_900, 8_900):
        stats.on_price("NOVA", price)
        model.on_price("NOVA", price)
    model.reset_for_session()
    assert model.mode == NORMAL and model.baseline_equity is None
    assert model.allows_entry("NOVA") and not model.paused("NOVA")
    assert model.alerts() == []


def test_status_snapshot(clock):
    model, stats = build(clock)
    model.on_portfolio(portfolio(70_000_000, [held("NOVA", 1_000, 10_000),
                                              held("AXON", 2_000, 10_000)]))
    stats.on_price("NOVA", 9_500)
    model.on_price("NOVA", 9_500)
    model.adjust_intent("DRFT", "buy", 100, 10_000, 0, source="dca")
    s = model.status()
    assert s["enabled"] is True and s["mode"] == NORMAL
    assert s["equity"] == 99_500_000 and s["baseline_equity"] == 100_000_000
    assert s["drawdown_start_pct"] == pytest.approx(0.005)
    assert s["drawdown_peak_pct"] == pytest.approx(0.005)
    assert s["gross_exposure_pct"] == pytest.approx(29_500_000 / 99_500_000)
    assert s["groups"]["energy_freight"]["exposure_pct"] == pytest.approx(29_500_000 / 99_500_000)
    assert s["groups"]["energy_freight"]["cap_pct"] == 0.45
    # Risk at stops: NOVA 1,000 x 1,000 (10% of cost) + AXON 2,000 x 1,000 = 3M.
    assert s["risk_at_stops_pct"] == pytest.approx(3_000_000 / 99_500_000)
    nova = s["instruments"]["NOVA"]
    assert nova["personality"] == "trending"
    assert nova["size_multiplier"] == 1.0
    assert nova["stop_level"] == 9_000 and nova["stop_pct"] == 0.10
    assert nova["session_pnl"] == -500_000
    assert nova["loss_pct"] == pytest.approx(0.05)
    assert nova["paused"] is False and nova["cooldown_remaining_s"] == 0
    assert s["instruments"]["DRFT"]["last_decision"] == {
        "side": "buy", "source": "dca", "requested": 100, "quantity": 20,
        "reason": "size_multiplier=0.2"}
    assert s["drawdown_warn_pct"] == 0.10 and s["drawdown_limit_pct"] == 0.15
    assert s["alerts"] == []
    # The share caps ride along so the dashboard's Risk view has one source.
    assert s["max_position"] == {sym: 1_000_000 for sym in SYMBOLS}


# --- factory ----------------------------------------------------------------------

def test_build_risk_model_wires_stats_thresholds_from_the_profiles(clock):
    from risk.model import build_risk_model
    limits = RiskLimits(1500, 1500, "NOVA", "ELXR")
    model = build_risk_model(cfg(), SYMBOLS, limits, clock=clock)
    assert model.enabled and model.max_position("NOVA") == 1500
    assert model.stats.thresholds("DRFT") == (0.004, 0.010)      # default in the test cfg
    assert model.stats.bucket_seconds == 10 and model.stats.atr_buckets == 1
    assert model.profiles["DRFT"].size_multiplier == 0.2
    model.stats.on_price("NOVA", 10_000)
    assert model.stats.last_price("NOVA") == 10_000
