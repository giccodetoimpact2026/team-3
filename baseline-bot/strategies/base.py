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
        self.prices = deque(maxlen=self.lookback)

    def on_price(self, last_price: int):
        self.prices.append(int(last_price))

    def ready(self) -> bool:
        return len(self.prices) >= self.lookback

    def signal(self, position: int):
        """Return a Signal or None given the current held position (shares)."""
        raise NotImplementedError

    def _limit_price(self, reference: int, side: str) -> int:
        """Cross the spread slightly to improve fill odds: pay up on a buy,
        concede on a sell. `reference` and the result are integer cents."""
        offset = reference * self.limit_offset_bps // 10_000
        return reference + offset if side == "buy" else reference - offset
