"""Tests for the deterministic news engine."""
import time
from unittest.mock import patch as mock_patch

import pytest

from news.engine import NewsEngine, NewsBias, _classify


# -- keyword classifier -----------------------------------------------------

class TestClassify:
    def test_bullish(self):
        assert _classify("Equity indices rally broadly as risk appetite returns") == "buy"

    def test_bearish(self):
        assert _classify("Major sanctions imposed on defense contractors") == "sell"

    def test_ambiguous_both(self):
        assert _classify("Rally expected despite crash in energy sector") is None

    def test_no_keywords(self):
        assert _classify("The committee met to discuss quarterly results") is None

    def test_case_insensitive(self):
        assert _classify("MASSIVE SURGE in semiconductor demand") == "buy"


# -- helpers ----------------------------------------------------------------

def _make_config(**overrides):
    cfg = {
        "enabled": True,
        "cooldown_seconds": 60,
        "directive_ttl_seconds": 120,
        "default_size_multiplier": 0.5,
        "size_multiplier": {"NOVA": 0.6, "DRFT": 0.25},
    }
    cfg.update(overrides)
    return cfg


def _make_announcement(ann_id=1, symbol="NOVA",
                       message="Oil discovery causes energy surge"):
    return {
        "announcement_id": ann_id,
        "affected_symbol": symbol,
        "message": message,
        "timestamp": "2026-09-18T10:00:00Z",
    }


# -- NewsEngine --------------------------------------------------------------

class TestNewsEngine:
    def test_bullish_creates_buy_bias(self):
        engine = NewsEngine(_make_config())
        bias = engine.on_announcement(_make_announcement())
        assert bias is not None
        assert bias.action == "buy"
        assert bias.symbol == "NOVA"
        assert bias.size_multiplier == 0.6

    def test_bearish_creates_sell_bias(self):
        engine = NewsEngine(_make_config())
        bias = engine.on_announcement(
            _make_announcement(message="Major sanctions on energy firms"))
        assert bias is not None
        assert bias.action == "sell"

    def test_unclassified_returns_none(self):
        engine = NewsEngine(_make_config())
        bias = engine.on_announcement(
            _make_announcement(message="Committee discusses quarterly results"))
        assert bias is None

    def test_dedup_same_id(self):
        engine = NewsEngine(_make_config())
        b1 = engine.on_announcement(_make_announcement(ann_id=42))
        b2 = engine.on_announcement(_make_announcement(ann_id=42))
        assert b1 is not None
        assert b2 is None

    def test_cooldown_blocks_second_announcement(self):
        engine = NewsEngine(_make_config(cooldown_seconds=60))
        engine.on_announcement(_make_announcement(ann_id=1, symbol="NOVA"))
        bias = engine.on_announcement(
            _make_announcement(ann_id=2, symbol="NOVA",
                               message="Another energy surge reported"))
        assert bias is None

    def test_cooldown_expires(self):
        engine = NewsEngine(_make_config(cooldown_seconds=1))
        engine.on_announcement(_make_announcement(ann_id=1, symbol="NOVA"))

        now = time.monotonic()
        with mock_patch("news.engine.time") as mock_time:
            mock_time.monotonic.return_value = now + 2
            bias = engine.on_announcement(
                _make_announcement(ann_id=2, symbol="NOVA",
                                   message="Another energy surge"))
        assert bias is not None

    def test_bias_expires(self):
        engine = NewsEngine(_make_config(directive_ttl_seconds=1))
        engine.on_announcement(_make_announcement(ann_id=1))

        now = time.monotonic()
        with mock_patch("news.engine.time") as mock_time:
            mock_time.monotonic.return_value = now + 2
            assert engine.active_bias("NOVA") is None

    def test_active_bias_returns_live(self):
        engine = NewsEngine(_make_config(directive_ttl_seconds=9999))
        engine.on_announcement(_make_announcement())
        bias = engine.active_bias("NOVA")
        assert bias is not None
        assert bias.action == "buy"

    def test_active_bias_none_for_unknown_symbol(self):
        engine = NewsEngine(_make_config())
        assert engine.active_bias("UNKNOWN") is None

    def test_reset_clears_everything(self):
        engine = NewsEngine(_make_config())
        engine.on_announcement(_make_announcement())
        engine.reset()
        assert engine.active_bias("NOVA") is None
        bias = engine.on_announcement(_make_announcement(ann_id=1))
        assert bias is not None

    def test_disabled_ignores_announcements(self):
        engine = NewsEngine(_make_config(enabled=False))
        bias = engine.on_announcement(_make_announcement())
        assert bias is None

    def test_malformed_payload(self):
        engine = NewsEngine(_make_config())
        assert engine.on_announcement({}) is None
        assert engine.on_announcement({"message": "surge"}) is None
        assert engine.on_announcement({"affected_symbol": "X"}) is None

    def test_default_multiplier_for_unknown_symbol(self):
        engine = NewsEngine(_make_config())
        bias = engine.on_announcement(
            _make_announcement(symbol="CRVX", message="Semiconductor surge"))
        assert bias is not None
        assert bias.size_multiplier == 0.5  # default

    def test_per_symbol_multiplier(self):
        engine = NewsEngine(_make_config())
        bias = engine.on_announcement(
            _make_announcement(symbol="DRFT",
                               message="Defense contractor shutdown"))
        assert bias is not None
        assert bias.size_multiplier == 0.25


# -- config read/write ------------------------------------------------------

class TestConfigReadWrite:
    def test_get_config(self):
        engine = NewsEngine(_make_config())
        cfg = engine.get_config()
        assert cfg["enabled"] is True
        assert cfg["cooldown_seconds"] == 60
        assert cfg["size_multiplier"]["NOVA"] == 0.6

    def test_update_config(self):
        engine = NewsEngine(_make_config())
        engine.update_config({"cooldown_seconds": 30, "enabled": False})
        cfg = engine.get_config()
        assert cfg["cooldown_seconds"] == 30
        assert cfg["enabled"] is False

    def test_update_multiplier(self):
        engine = NewsEngine(_make_config())
        engine.update_config({"size_multiplier": {"NOVA": 0.9}})
        cfg = engine.get_config()
        assert cfg["size_multiplier"]["NOVA"] == 0.9
        assert cfg["size_multiplier"]["DRFT"] == 0.25

    def test_multiplier_clamped(self):
        engine = NewsEngine(_make_config())
        engine.update_config({"default_size_multiplier": 5.0})
        assert engine.get_config()["default_size_multiplier"] == 2.0
        engine.update_config({"default_size_multiplier": -1.0})
        assert engine.get_config()["default_size_multiplier"] == 0.0
