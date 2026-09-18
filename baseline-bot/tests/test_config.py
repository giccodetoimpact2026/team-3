"""Regression tests for safe runtime exchange configuration."""

import pytest

import main


def test_load_config_requires_runtime_key(monkeypatch):
    monkeypatch.delenv("EXCHANGE_API_KEY", raising=False)
    monkeypatch.delenv("EXCHANGE_BASE_URL", raising=False)

    with pytest.raises(ValueError, match="EXCHANGE_API_KEY"):
        main.load_config()


def test_load_config_uses_environment_overrides(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "test-key")
    monkeypatch.setenv("EXCHANGE_BASE_URL", "https://sandbox.example/")

    config = main.load_config()

    assert config["exchange"]["api_key"] == "test-key"
    assert config["exchange"]["base_url"] == "https://sandbox.example"


# --- startup validation ------------------------------------------------------

def _valid():
    return {
        "instruments": {"active": {"primary": "NOVA", "secondary": "ELXR"}},
        "strategy": {
            "NOVA": {"type": "momentum", "lookback_samples": 10, "momentum_threshold": 0.003,
                     "trade_quantity": 100, "limit_offset_bps": 5},
            "ELXR": {"type": "mean_reversion", "lookback_samples": 15,
                     "deviation_threshold": 0.004, "trade_quantity": 50, "limit_offset_bps": 5},
        },
        "risk": {"max_position_primary": 500, "max_position_secondary": 200,
                 "max_orders_per_minute": 20, "anomaly_pause_seconds": 60},
        "control": {"host": "127.0.0.1", "port": 8092},
        "heartbeat": {"interval_seconds": 5, "feed_degraded_after_seconds": 10,
                      "feed_stall_after_seconds": 20},
    }


def test_shipped_config_validates():
    import os
    import yaml
    path = os.path.join(os.path.dirname(main.__file__), "config.yaml")
    with open(path) as f:
        main.validate_config(yaml.safe_load(f))


def test_valid_config_passes():
    main.validate_config(_valid())


@pytest.mark.parametrize("mutate, match", [
    (lambda c: c["strategy"]["NOVA"].update(trade_quantity=0), "trade_quantity"),
    (lambda c: c["strategy"]["NOVA"].update(trade_quantity=-5), "trade_quantity"),
    (lambda c: c["strategy"]["ELXR"].update(lookback_samples=0), "lookback_samples"),
    (lambda c: c["strategy"]["NOVA"].update(momentum_threshold=0), "momentum_threshold"),
    (lambda c: c["strategy"]["NOVA"].update(momentum_threshold=1.5), "momentum_threshold"),
    (lambda c: c["strategy"]["ELXR"].update(deviation_threshold=-0.1), "deviation_threshold"),
    (lambda c: c["strategy"]["NOVA"].update(type="martingale"), "unknown strategy"),
    (lambda c: c["risk"].update(max_position_primary=-1), "max_position_primary"),
    (lambda c: c["risk"].update(max_orders_per_minute=0), "max_orders_per_minute"),
    (lambda c: c["control"].update(port=70000), "control.port"),
    (lambda c: c["heartbeat"].update(feed_stall_after_seconds=5), "heartbeat"),
    (lambda c: c["instruments"]["active"].update(secondary="NOVA"), "distinct"),
])
def test_invalid_config_is_rejected(mutate, match):
    cfg = _valid()
    mutate(cfg)
    with pytest.raises(ValueError, match=match):
        main.validate_config(cfg)


def test_crash_guard_config_covers_all_instruments(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "test-key")
    monkeypatch.setenv("EXCHANGE_BASE_URL", "http://test")
    from main import load_config
    cg = load_config()["crash_guard"]
    assert cg["enabled"] is True
    assert cg["drop_pct"] == 0.15
    assert cg["confirm_ticks"] >= 2
    assert 0 < cg["floor_pct"] < cg["drop_pct"]
    assert cg["cooldown_seconds"] > 0


# --- risk model section ------------------------------------------------------

def _risk_model():
    return {
        "enabled": True, "size_mode": "cap", "bucket_seconds": 10, "atr_buckets": 14,
        "regime_confirm_buckets": 3, "correlation_buckets": 30,
        "correlation_min_samples": 20, "correlation_alert": 0.7,
        "risk_per_trade_pct": 0.005, "max_notional_per_symbol_pct": 0.35,
        "max_gross_exposure_pct": 0.9, "instrument_loss_limit_pct": 0.02,
        "drawdown": {"warn_pct": 0.10, "limit_pct": 0.15, "action": "block_buys"},
        "stop_loss": {"enabled": True},
        "exit": {"floor_pct": 0.03, "reprice_after_seconds": 5},
        "regime_factors": {"momentum": {"LOW": 0.0, "NEUTRAL": 0.5, "HIGH": 1.0}},
        "groups": {"energy_freight": {"members": ["NOVA", "AXON"], "max_exposure_pct": 0.45}},
        "default_profile": {"size_multiplier": 0.5, "depth_fraction": 0.5,
                            "max_qty_without_book": 1000, "low_vol": 0.004,
                            "high_vol": 0.010, "stop_atr_mult": 4.0,
                            "min_stop_pct": 0.04, "max_stop_pct": 0.10,
                            "confirm_ticks": 3, "cooldown_seconds": 120},
        "profiles": {"DRFT": {"size_multiplier": 0.2, "low_vol": 0.006, "high_vol": 0.015}},
    }


def test_config_without_a_risk_model_section_is_accepted():
    main.validate_config(_valid())


def test_valid_risk_model_section_passes():
    cfg = _valid()
    cfg["risk_model"] = _risk_model()
    main.validate_config(cfg)


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.update(size_mode="guess"), "size_mode"),
    (lambda r: r.update(bucket_seconds=0), "bucket_seconds"),
    (lambda r: r.update(atr_buckets=0), "atr_buckets"),
    (lambda r: r.update(risk_per_trade_pct=1.5), "risk_per_trade_pct"),
    (lambda r: r.update(max_gross_exposure_pct=0), "max_gross_exposure_pct"),
    (lambda r: r["drawdown"].update(action="ignore"), "drawdown.action"),
    (lambda r: r["drawdown"].update(warn_pct=0.2), "drawdown"),
    (lambda r: r["default_profile"].update(low_vol=0.02), "low_vol"),
    (lambda r: r["profiles"]["DRFT"].update(size_multiplier=-1), "size_multiplier"),
    (lambda r: r["default_profile"].update(min_stop_pct=0.2), "min_stop_pct"),
    (lambda r: r["default_profile"].update(depth_fraction=1.5), "depth_fraction"),
    (lambda r: r["groups"]["energy_freight"].update(members=[]), "members"),
    (lambda r: r["groups"]["energy_freight"].update(max_exposure_pct=2), "max_exposure_pct"),
    (lambda r: r["regime_factors"]["momentum"].update(LOW=-1), "regime_factors"),
])
def test_invalid_risk_model_section_is_rejected(mutate, match):
    cfg = _valid()
    cfg["risk_model"] = _risk_model()
    mutate(cfg["risk_model"])
    with pytest.raises(ValueError, match=match):
        main.validate_config(cfg)


def test_shipped_risk_model_config_covers_the_five_instruments(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "test-key")
    monkeypatch.setenv("EXCHANGE_BASE_URL", "http://test")
    rm = main.load_config()["risk_model"]
    assert rm["enabled"] is True
    assert set(rm["profiles"]) == {"NOVA", "AXON", "CRVX", "DRFT", "ELXR"}
    nova, drft = rm["profiles"]["NOVA"], rm["profiles"]["DRFT"]
    assert drft["size_multiplier"] <= 0.2 * nova["size_multiplier"]
    assert rm["drawdown"]["limit_pct"] == 0.15
    assert rm["groups"]["energy_freight"]["members"] == ["NOVA", "AXON"]
