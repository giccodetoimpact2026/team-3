"""Regression tests for SSE feed supervision."""

from types import SimpleNamespace

from exchange.feed import SSEFeed


def test_handler_failure_does_not_escape_dispatch():
    feed = SSEFeed("https://exchange.example", "unused")
    feed.on("price", lambda _payload: (_ for _ in ()).throw(RuntimeError("boom")))

    feed._dispatch(SimpleNamespace(event="price", data="{}"))


def test_reconnect_header_uses_last_processed_event_id():
    feed = SSEFeed("https://exchange.example", "unused")
    feed._last_event_id = "event-42"

    assert feed._connection_headers() == {
        "Accept": "text/event-stream",
        "Last-Event-ID": "event-42",
    }


def test_initial_connection_is_public_and_has_no_api_key():
    feed = SSEFeed("https://exchange.example", "secret-must-not-leak")

    assert feed._connection_headers() == {"Accept": "text/event-stream"}
