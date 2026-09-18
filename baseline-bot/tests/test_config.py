"""Regression tests for safe runtime exchange configuration."""

import pytest

import main


def test_load_config_requires_runtime_key(monkeypatch):
    # load_config() reads .env before validating, so deleting the variables is
    # not enough on a developer machine that has one — the loader puts them
    # straight back and this test passed on CI while failing locally. Stub the
    # loader out: the assertion here is about load_config's validation, not
    # about .env parsing, which test_load_env_file covers separately.
    monkeypatch.setattr(main, "load_env_file", lambda *a, **k: None)
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
