"""In-memory order book cache fed by SSE book_update events.

Thread-safe: the feed thread writes via update(), the trading thread reads
best_bid/best_ask/spread_bps. Staleness-aware — returns None when the data
is older than a configurable threshold so the caller falls back to blind
offset pricing.
"""
import threading
import time


class OrderBookCache:
    def __init__(self, stale_seconds: float = 5.0, clock=time.monotonic):
        self._stale_seconds = stale_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._books: dict[str, dict] = {}

    def update(self, symbol: str, bids: list, asks: list) -> None:
        with self._lock:
            self._books[symbol] = {
                "bids": bids or [],
                "asks": asks or [],
                "at": self._clock(),
            }

    def _fresh(self, symbol: str):
        book = self._books.get(symbol)
        if book is None:
            return None
        if self._clock() - book["at"] > self._stale_seconds:
            return None
        return book

    def best_bid(self, symbol: str) -> int | None:
        with self._lock:
            book = self._fresh(symbol)
        if not book or not book["bids"]:
            return None
        return int(book["bids"][0].get("price", 0)) or None

    def best_ask(self, symbol: str) -> int | None:
        with self._lock:
            book = self._fresh(symbol)
        if not book or not book["asks"]:
            return None
        return int(book["asks"][0].get("price", 0)) or None

    def spread_bps(self, symbol: str) -> float | None:
        with self._lock:
            book = self._fresh(symbol)
        if not book or not book["bids"] or not book["asks"]:
            return None
        bid = int(book["bids"][0].get("price", 0))
        ask = int(book["asks"][0].get("price", 0))
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 10_000

    def midpoint(self, symbol: str) -> int | None:
        with self._lock:
            book = self._fresh(symbol)
        if not book or not book["bids"] or not book["asks"]:
            return None
        bid = int(book["bids"][0].get("price", 0))
        ask = int(book["asks"][0].get("price", 0))
        if bid <= 0 or ask <= 0:
            return None
        return (bid + ask) // 2

    def reset(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self._books.clear()
            else:
                self._books.pop(symbol, None)
