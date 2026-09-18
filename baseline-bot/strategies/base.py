"""Strategy base class and the price-history buffer.

Prices are integer cents. Each strategy keeps its own rolling buffer of the last
`lookback_samples` prices (there is no candles endpoint — history is built from
SSE `price` events). The exchange is LONG-ONLY, so a strategy may only SELL to
reduce or close a held long; it never goes net short.
"""
from collections import deque


class Signal:
    def __init__(self, side: str, quantity: int, limit_price: int):
        self.side = side              # "buy" | "sell"
        self.quantity = quantity      # shares
        self.limit_price = limit_price  # integer cents

    def __repr__(self):
        return f"Signal({self.side} {self.quantity} @ {self.limit_price})"


class BaseStrategy:
    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.lookback = int(config["lookback_samples"])
        self.trade_quantity = int(config["trade_quantity"])
        self.limit_offset_bps = int(config.get("limit_offset_bps", 5))
        self.max_spread_bps = float(config["max_spread_bps"]) if "max_spread_bps" in config else None
        self.orderbook = None
        self.prices = deque(maxlen=self.lookback)

    def on_price(self, last_price: int):
        self.prices.append(int(last_price))

    def ready(self) -> bool:
        return len(self.prices) >= self.lookback

    def reset(self):
        """Drop per-window state at a session boundary.

        Prices belong to the window they were observed in, so carrying them
        across a boundary trades the previous window's series. Subclasses that
        hold extra state must extend this, not just clear `prices`.
        """
        self.prices.clear()

    def spread_ok(self) -> bool:
        """False when the book spread exceeds the configured max, signalling
        that liquidity is too thin to trade safely."""
        if self.max_spread_bps is None or self.orderbook is None:
            return True
        spread = self.orderbook.spread_bps(self.symbol)
        if spread is None:
            return True
        return spread <= self.max_spread_bps

    def signal(self, position: int):
        """Return a Signal or None given the current held position (shares)."""
        raise NotImplementedError

    def _limit_price(self, reference: int, side: str) -> int:
        """Price off the order book when available, else fall back to a blind
        offset from the last trade. `reference` and the result are integer cents."""
        if self.orderbook is not None:
            if side == "buy":
                ask = self.orderbook.best_ask(self.symbol)
                if ask is not None:
                    return ask
            else:
                bid = self.orderbook.best_bid(self.symbol)
                if bid is not None:
                    return bid
        offset = reference * self.limit_offset_bps // 10_000
        return reference + offset if side == "buy" else reference - offset
