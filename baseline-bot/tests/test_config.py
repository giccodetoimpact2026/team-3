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
