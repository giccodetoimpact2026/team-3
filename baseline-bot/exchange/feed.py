"""SSE market-data feed.

Connects to `GET /events` with `X-API-Key` and dispatches events to registered
handlers. Market data is SSE-only — there is no candles endpoint, so price
history is accumulated from `price` events downstream.
"""
import json
import threading

import requests
import sseclient


class SSEFeed:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._handlers = {}          # event type -> callable(payload: dict)
        self._thread = None
        self._last_event_id = None   # tracked, but not yet used to reconnect

    def on(self, event_type: str, handler):
        """Register a handler for an event type (price/trade/book_update/session)."""
        self._handlers[event_type] = handler

    def start(self):
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _listen(self):
        try:
            headers = {"X-API-Key": self.api_key, "Accept": "text/event-stream"}
            resp = requests.get(f"{self.base_url}/events", headers=headers,
                                stream=True, timeout=(5, None))
            client = sseclient.SSEClient(resp)
            for event in client.events():
                self._last_event_id = event.id
                self._dispatch(event)
        except Exception:
            pass

    def _dispatch(self, event):
        try:
            payload = json.loads(event.data) if event.data else {}
        except json.JSONDecodeError:
            return

        etype = event.event or "message"
        if etype == "price":
            self._emit("price", payload)
        elif etype == "trade":
            self._emit("trade", payload)
        elif etype == "book_update":
            self._emit("book_update", payload)
        elif etype == "session":
            self._emit("session", payload)
        elif etype == "announcement":
            # TODO(S1): students wire LLM-based signal here.
            # `announcement` payload is { message, affected_symbol, timestamp, announcement_id } —
            # the S1 signal source. Derive a directional view across the
            # supply-chain web, gate it on confidence, and act via the strategies.
            pass

    def _emit(self, event_type: str, payload: dict):
        handler = self._handlers.get(event_type)
        if handler:
            handler(payload)
