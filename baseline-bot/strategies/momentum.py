"""Momentum strategy (NOVA, primary).

Buys into upward momentum; when momentum turns down and a long is held, sells to
close (long-only — never net short). Prices are always integer cents.

TWO MEASURES OF "MOMENTUM TURNED DOWN", because one is not enough:

* WINDOW RETURN, `(last - first) / first`. What the strategy enters on. As an
  exit it has a structural blind spot: `first` slides forward with the window,
  so after a rise stops, `first` catches up into the new level and the return
  decays toward 0% — away from the negative trigger, not toward it. A position
  opened during a rise that then flattens is never released by this measure.

* GIVE-BACK FROM THE WINDOW HIGH, `(last - max(window)) / max(window)`. Priced
  off a reference that only ratchets up, so a roll-over registers immediately
  instead of waiting for the whole move to be handed back. This is what exits a
  faded trend. Optional (`exit_giveback_pct`); absent, behaviour is unchanged.

The two are OR-ed and checked BEFORE the entry branch, so a give-back exit wins
over an entry signal that the still-positive window return would otherwise
produce. Both reduce by one clip rather than closing outright — the full close
belongs to `risk.exits`, which is the layer that knows the cost basis.

SCALE-IN IS BOUNDED (`max_tranches`). Without a bound the entry branch re-fires
on EVERY tick while the window return holds above the threshold, so a single
modest rise walks the position to its cap in as many orders as it takes and
spends the rest of the move being refused by the risk cap. That is not just
churn against the rate limiter: averaging in across the whole ascent blends the
cost basis UP toward the current price, and the trailing take-profit in
`risk.exits` is armed as a percentage OVER that basis. The more it accumulates,
the further its own take-profit recedes. Bounding the tranches keeps the basis
low enough for the trail to arm.
"""
from .base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.threshold = float(config["momentum_threshold"])

        giveback = config.get("exit_giveback_pct")
        self.exit_giveback = None if giveback is None else float(giveback)
        if self.exit_giveback is not None and self.exit_giveback <= 0:
            raise ValueError(
                f"{symbol}: exit_giveback_pct must be > 0 "
                f"(omit the key to disable); got {self.exit_giveback}")

        tranches = config.get("max_tranches")
        self.max_tranches = None if tranches is None else int(tranches)
        if self.max_tranches is not None and self.max_tranches < 1:
            raise ValueError(
                f"{symbol}: max_tranches must be >= 1 "
                f"(omit the key for an unbounded scale-in); "
                f"got {self.max_tranches}")

    # --- scale-in bound ---------------------------------------------------

    def tranches_held(self, position: int) -> int:
        """Clips already held, derived from the position rather than counted.

        Stateless on purpose: it survives a restart and cannot drift out of
        step with the exchange, the same reason the mean-reversion ladder
        derives its rung this way.
        """
        return max(0, position) // self.trade_quantity

    def can_add(self, position: int) -> bool:
        if self.max_tranches is None:
            return True
        return self.tranches_held(position) < self.max_tranches

    # --- exit measures ----------------------------------------------------

    def giveback_from_high(self):
        """Signed return from the window high; <= 0. None if not computable."""
        if not self.prices:
            return None
        high = max(self.prices)
        if high <= 0:
            return None
        return (self.prices[-1] - high) / high

    def momentum_faded(self, change: float) -> bool:
        """True when either exit measure says the trend is over."""
        if change <= -self.threshold:
            return True
        if self.exit_giveback is None:
            return False
        giveback = self.giveback_from_high()
        return giveback is not None and giveback <= -self.exit_giveback

    # --- signal -----------------------------------------------------------

    def signal(self, position: int):
        if not self.ready():
            return None
        first, last = self.prices[0], self.prices[-1]
        if first <= 0:
            return None
        change = (last - first) / first

        # EXIT FIRST. A held long must be able to leave while the window return
        # still reads as momentum — that overlap is exactly the case the
        # give-back measure exists to catch, and an entry checked first would
        # add to the position instead of reducing it. No spread or liquidity
        # gate here: a thin book must never stop us reducing.
        if position > 0 and self.momentum_faded(change):
            qty = min(self.trade_quantity, position)
            return Signal("sell", qty, self._limit_price(last, "sell"))

        if change >= self.threshold:
            # Upward momentum: open or add to the long, while tranches remain.
            if not self.can_add(position):
                return None
            if not self.spread_ok():
                return None
            limit = self._limit_price(last, "buy")
            quantity = self.entry_size(limit)
            if quantity <= 0:
                return None
            return Signal("buy", quantity, limit)
        return None
