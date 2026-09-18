"""In-memory order book cache fed by SSE book_update events.

Thread-safe: the feed thread writes via update(), the trading thread reads
best_bid/best_ask/spread_bps. Staleness-aware — returns None when the data
is older than a configurable threshold so the caller falls back to blind
offset pricing.
"""
import threading
import time


class OrderBookCache:
    def __init__(self, stale_seconds: float = 5.0, clock=time.monotonic,
                 team_name: str | None = None):
        self._stale_seconds = stale_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._books: dict[str, dict] = {}
        # Our own team name, from GET /portfolio. Used to exclude our resting
        # orders from depth: we cannot trade against ourselves (409 SELF_MATCH),
        # so counting our own size as available liquidity overstates the book
        # precisely when we already have an order sitting in it.
        self.team_name = team_name

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

    def depth_at_or_better(self, symbol: str, side: str,
                           limit_price: int) -> int | None:
        """Shares we could fill at `limit_price` or better. None if unknown.

        Two things about the payload shape make this more than a lookup
        (exchange-api-reference.md §4):

        * entries are **one per resting order, not one per price** — "there is
          one entry per resting order" — so a price level can appear several
          times and has to be summed rather than read;
        * every entry names its owner, and that includes us. Our own resting
          order is not liquidity we can take: an order crossing it comes back
          409 SELF_MATCH. So `team_name` entries are excluded.

        `side` is the side WE would trade: "buy" walks the asks at or below our
        limit, "sell" walks the bids at or above it. Returns None when there is
        no fresh book, which the caller must treat as "no opinion" rather than
        as zero liquidity — a stale feed is not an empty market.
        """
        with self._lock:
            book = self._fresh(symbol)
            team = self.team_name
        if not book:
            return None
        levels = book["asks"] if side == "buy" else book["bids"]
        if not levels:
            return 0

        total = 0
        for entry in levels:
            price = int((entry or {}).get("price", 0))
            if price <= 0:
                continue
            if side == "buy" and price > limit_price:
                continue
            if side == "sell" and price < limit_price:
                continue
            if team is not None and (entry or {}).get("team_name") == team:
                continue
            # `quantity` is what is still resting, not the order's original
            # size, so it is already the tradeable figure.
            total += max(0, int(entry.get("quantity", 0)))
        return total

    def reset(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self._books.clear()
            else:
                self._books.pop(symbol, None)
