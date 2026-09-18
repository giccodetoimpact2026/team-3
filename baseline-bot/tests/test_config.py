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
