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
        spread_cap = config.get("max_spread_bps")
        self.max_spread_bps = None if spread_cap is None else float(spread_cap)
        # Liquidity filter: the fewest shares the book must be able to absorb
        # before we will place an entry at all. None disables the filter.
        depth_floor = config.get("min_depth_shares")
        self.min_depth_shares = None if depth_floor is None else int(depth_floor)
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

    def entry_size(self, limit_price: int) -> int:
        """How many shares to enter with, given what the book can absorb.

        Adeline Loh's complaint is the specification: "If we're getting lots of
        partial fills on NOVA, our sizing is too big for the book." The answer
        to a thin book is therefore to trade SMALLER, not to refuse — refusing
        gives up the trade, while sizing to the book takes the part of it the
        market can actually fill. Derek and Aaron said the same about DRFT from
        the other direction: "your order size moves the price — plan
        accordingly."

        Returns the clip to use, or 0 to place nothing:

        * no book, or a stale one -> the full clip. A stale feed is not an empty
          market, and `_limit_price` already falls back to a blind offset.
        * depth at or better than our limit >= clip -> the full clip.
        * less than that -> shrink to the available depth, unless it falls below
          `min_depth_shares`, which is the floor where a fill is too small to be
          worth the fees and the price impact of asking.

        ENTRIES ONLY. Never call this on an exit: a thin book is exactly when
        you most want out, and sizing an exit to available depth would leave the
        remainder stranded.
        """
        if self.orderbook is None or limit_price <= 0:
            return self.trade_quantity
        depth = self.orderbook.depth_at_or_better(self.symbol, "buy", limit_price)
        if depth is None:
            return self.trade_quantity
        if depth >= self.trade_quantity:
            return self.trade_quantity
        floor = self.min_depth_shares
        if floor is not None and depth < floor:
            return 0
        return depth

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
