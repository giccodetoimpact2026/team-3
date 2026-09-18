"""SSE market-data feed.

Connects to `GET /events` with `X-API-Key` and dispatches events to registered
handlers. Market data is SSE-only — there is no candles endpoint, so price
history is accumulated from `price` events downstream.
"""
import json
import logging
import random
import threading
import time

import requests
import sseclient


logger = logging.getLogger(__name__)


class SSEFeed:
    def __init__(self, base_url: str, api_key: str,
                 reconnect_base_seconds: float = 0.5,
                 reconnect_max_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        # Retained for constructor compatibility. /events is public and must not
        # receive the team's API key.
        self.api_key = api_key
        self._handlers = {}          # event type -> callable(payload: dict)
        self._thread = None
        self._last_event_id = None
        self._response = None
        self._stop = threading.Event()
        self._reconnect_base_seconds = reconnect_base_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        # Liveness for the heartbeat/watchdog: when the last *event* arrived
        # and when the stream last connected. sseclient swallows the
        # exchange's `: keep-alive` comments, so a quiet market looks the same
        # as a stalled socket here; the watchdog threshold allows for that.
        self._last_frame_at = None
        self._connected_at = None
        self.reconnects = 0

    def on(self, event_type: str, handler):
        """Register a handler for an event type (price/trade/book_update/session)."""
        self._handlers[event_type] = handler

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("SSE feed is already running")
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        response = self._response
        if response is not None:
            response.close()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def age(self, now=None) -> float | None:
        """Seconds since the last event from the exchange; None before the first."""
        if self._last_frame_at is None:
            return None
        return (now if now is not None else time.monotonic()) - self._last_frame_at

    def connected(self) -> bool:
        return self._response is not None

    def restart(self, join_timeout: float = 5.0) -> None:
        """Tear the stream down and reconnect from the saved cursor.

        Used by the feed-stall watchdog for a half-open connection: the socket
        is still up, so the reader would wait on it forever, but nothing has
        arrived for longer than the exchange's keep-alive interval.
        """
        self.stop()
        self.join(join_timeout)
        self.reconnects += 1
        self.start()

    def _connection_headers(self):
        headers = {"Accept": "text/event-stream"}
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id
        return headers

    def _listen(self):
        delay = self._reconnect_base_seconds
        while not self._stop.is_set():
            try:
                self._listen_once()
                if not self._stop.is_set():
                    raise ConnectionError("SSE stream ended")
            except Exception:
                if self._stop.is_set():
                    break
                logger.exception("SSE connection failed; reconnecting")
                jitter = random.uniform(0, min(0.25, delay * 0.25))
                self._stop.wait(delay + jitter)
                delay = min(delay * 2, self._reconnect_max_seconds)

    def _listen_once(self):
        response = requests.get(
            f"{self.base_url}/events",
            headers=self._connection_headers(),
            stream=True,
            timeout=(5, 30),
            verify=False,
        )
        self._response = response
        try:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type.lower():
                raise ValueError(f"unexpected SSE content type: {content_type!r}")

            self._connected_at = time.monotonic()
            self._last_frame_at = self._connected_at
            client = sseclient.SSEClient(response)
            for event in client.events():
                if self._stop.is_set():
                    break
                self._last_frame_at = time.monotonic()
                if event.id:
                    self._last_event_id = event.id
                self._dispatch(event)
        finally:
            response.close()
            self._response = None

    def _dispatch(self, event):
        try:
            payload = json.loads(event.data) if event.data else {}
        except json.JSONDecodeError as exc:
            logger.warning("discarding malformed SSE event: %s", exc)
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
            self._emit("announcement", payload)

    def _emit(self, event_type: str, payload: dict):
        handler = self._handlers.get(event_type)
        if handler:
            try:
                handler(payload)
            except Exception:
                # A strategy or REST failure must not kill the network reader.
                # The handler failure remains visible and the next event is read.
                logger.exception("SSE handler failed for event type %s", event_type)
