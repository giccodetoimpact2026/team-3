from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from main import Bot, load_config


@pytest.fixture
def config():
    from pathlib import Path

    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


@pytest.fixture
def client():
    c = Mock()
    c.get_session.return_value = {"id": "window-1", "status": "running", "mode": "sandbox"}
    c.get_orders.return_value = []
    c.get_positions.return_value = 0
    return c


def ready_bot(client, config, **kwargs):
    bot = Bot(client, config, **kwargs)
    bot.session_id = "window-1"
    s = bot.strategies["NOVA"]
    # Fill exactly the configured lookback with a clear uptrend, so these tests
    # keep exercising a live signal when the window is retuned. A fixed 10
    # samples stopped filling the buffer when NOVA widened to 25, and every
    # "assert not submitted" case below then passed vacuously.
    for i in range(s.lookback):
        s.on_price(10_000 + i * 100)
    return bot, s


def test_missing_position_is_zero():
    c = ExchangeClient("http://test", "test")
    c.get_portfolio = Mock(return_value={"positions": []})
    assert c.get_positions("ELXR") == 0


def test_config_lookback_and_environment(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "test-key")
    monkeypatch.setenv("EXCHANGE_BASE_URL", "http://test")
    c = load_config()
    assert c["strategy"]["ELXR"]["lookback_samples"] == 15
    assert c["exchange"] == {"base_url": "http://test", "api_key": "test-key"}


def test_observation_never_submits_or_cancels(client, config):
    bot, s = ready_bot(client, config)
    bot.evaluate(s)
    bot.shutdown()
    client.submit_limit_order.assert_not_called()
    client.cancel_order.assert_not_called()


def test_live_blocked_by_default(client, config):
    client.get_session.return_value["mode"] = "live"
    bot, s = ready_bot(client, config, trade=True)
    bot.evaluate(s)
    client.submit_limit_order.assert_not_called()


def test_resting_order_prevents_stacking(client, config):
    client.get_orders.return_value = [{"symbol": "NOVA", "status": "OPEN"}]
    bot, s = ready_bot(client, config, trade=True)
    bot.evaluate(s)
    client.submit_limit_order.assert_not_called()


def test_position_cap(client, config):
    client.get_positions.return_value = 500
    bot, s = ready_bot(client, config, trade=True)
    bot.evaluate(s)
    client.submit_limit_order.assert_not_called()


def test_unknown_submit_blocks_until_reconciled(client, config):
    bot, s = ready_bot(client, config, trade=True)
    client.submit_limit_order.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        bot.evaluate(s)
    key = bot.unknown_key
    assert key
    bot.evaluate(s)
    assert client.submit_limit_order.call_count == 1
    client.get_orders.return_value = [
        {"id": "o1", "idempotency_key": key, "symbol": "NOVA", "status": "OPEN"}
    ]
    bot.evaluate(s)
    assert bot.unknown_key is None
    assert client.submit_limit_order.call_count == 1


def test_new_window_resets_all_history(client, config):
    bot, s = ready_bot(client, config, trade=True)
    client.get_session.return_value["id"] = "window-2"
    bot.evaluate(s)
    assert all(not item.prices for item in bot.strategies.values())
    client.submit_limit_order.assert_not_called()


def test_shutdown_cancels_and_checks(client, config, monkeypatch):
    monkeypatch.setattr("main.time.sleep", lambda _: None)
    client.get_orders.side_effect = [
        [{"id": "o1", "status": "PARTIALLY_FILLED"}, {"id": "o2", "status": "FILLED"}],
        [],
    ]
    bot = Bot(client, config, trade=True)
    assert bot.shutdown()
    client.cancel_order.assert_called_once_with("o1")
    assert bot.stopping.is_set()


def test_each_instrument_gets_evaluated(client, config):
    bot = Bot(client, config)
    bot.evaluate = Mock()
    bot.on_price({"symbol": "NOVA", "last_price": 10000})
    bot.on_price({"symbol": "ELXR", "last_price": 10000})
    assert bot.evaluate.call_count == 2


def test_reconnect_preserves_cursor_on_snapshot(monkeypatch):
    feed = SSEFeed("http://test", "test-key")
    response = Mock()
    response.headers = {"Content-Type": "text/event-stream"}
    get = Mock(return_value=response)
    monkeypatch.setattr("exchange.feed.requests.get", get)

    def event(id):
        return SimpleNamespace(id=id, event="price", data='{"last_price":100}')

    streams = iter([[event("42")], [event("")], []])

    def stream(_):
        events = next(streams)
        if not events:
            feed._stop.set()
        return SimpleNamespace(events=lambda: iter(events))

    monkeypatch.setattr("exchange.feed.sseclient.SSEClient", stream)
    monkeypatch.setattr(feed._stop, "wait", lambda _: None)
    feed._listen()
    assert get.call_args_list[1].kwargs["headers"]["Last-Event-ID"] == "42"
    assert get.call_args_list[2].kwargs["headers"]["Last-Event-ID"] == "42"
    assert "X-API-Key" not in get.call_args.kwargs["headers"]
