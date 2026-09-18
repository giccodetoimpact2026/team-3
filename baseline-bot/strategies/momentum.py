"""Momentum strategy (NOVA, primary).

Buys into upward momentum; when momentum turns down and a long is held, sells to
close (long-only — never net short). Momentum is the return over the lookback
window; the magnitude is a unit-free ratio. Prices are always integer cents.
"""
from .base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.threshold = float(config["momentum_threshold"])

    def signal(self, position: int):
        if not self.ready():
            return None
        if not self.spread_ok():
            return None
        first, last = self.prices[0], self.prices[-1]
        if first <= 0:
            return None
        change = (last - first) / first

        if change >= self.threshold:
            # Upward momentum: open or add to the long.
            return Signal("buy", self.trade_quantity, self._limit_price(last, "buy"))
        if change <= -self.threshold and position > 0:
            # Momentum turned down and we hold: reduce/close.
            qty = min(self.trade_quantity, position)
            return Signal("sell", qty, self._limit_price(last, "sell"))
        return None
