"""Mean-reversion strategy (ELXR, secondary).

Buys when price dips below its rolling mean (expecting reversion up); when price
runs above the mean and a long is held, sells to close (long-only). The lookback
window size is the key parameter — see James Wee's strategy notes.
"""
from .base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.threshold = float(config["deviation_threshold"])

    def signal(self, position: int):
        if not self.ready():
            return None
        mean = sum(self.prices) / len(self.prices)
        last = self.prices[-1]
        if mean <= 0:
            return None
        deviation = (last - mean) / mean

        if deviation <= -self.threshold:
            # Below the mean: expect reversion up -> buy. The spread gate
            # applies to ENTRIES only — a wide book must never stop us
            # reducing, or the illiquidity that made it wide traps the position.
            if not self.spread_ok():
                return None
            return Signal("buy", self.trade_quantity, self._limit_price(last, "buy"))
        if deviation >= self.threshold and position > 0:
            # Above the mean and holding: take the reversion -> sell to close.
            qty = min(self.trade_quantity, position)
            return Signal("sell", qty, self._limit_price(last, "sell"))
        return None
