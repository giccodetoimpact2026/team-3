"""Regression tests for exchange-client portfolio handling."""

from exchange.client import ExchangeClient


def _client_with_portfolio(portfolio):
    client = object.__new__(ExchangeClient)
    client.get_portfolio = lambda: portfolio
    return client


def test_missing_position_defaults_to_zero():
    client = _client_with_portfolio({"positions": []})

    assert client.get_positions("NOVA") == 0


def test_existing_position_is_returned():
    client = _client_with_portfolio(
        {"positions": [{"symbol": "NOVA", "quantity": 125}]}
    )

    assert client.get_positions("NOVA") == 125
