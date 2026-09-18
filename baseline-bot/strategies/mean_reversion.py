"""Mean-reversion strategy (ELXR and DRFT).

Buys when price dips below its rolling mean (expecting reversion up); when price
runs above the mean and a long is held, sells to close (long-only). The lookback
window size is the key parameter — see James Wee's strategy notes, page 2:
"Lookback = 15 samples. This is important ... Use 15."

**The two bands can differ, and for one instrument they must.** A single
`deviation_threshold` assumes the instrument dips below its mean about as often
as it runs above it. That holds for a range-bound name like ELXR, and it fails
completely for a name that spikes upward and reverts, which is DRFT: "it spikes
on volume then drops."

The failure is not a tuning inconvenience, it is structural, because the
exchange is long-only:

  * the sell leg fires often — a spike puts price well above its own mean;
  * the buy leg almost never fires — price rarely sits far *below* the mean,
    because the moves are upward;
  * and a sell can only ever reduce a long we do not have.

So a symmetric band on DRFT produces no buys, therefore no position, therefore
no sells either: measured at 0.015 it placed nothing at all. Set
`buy_deviation_threshold` below `sell_deviation_threshold` to buy the small
frequent dips and sell into the large rare spikes. Both default to
`deviation_threshold`, so a symmetric instrument needs no extra keys.
"""
from .base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        symmetric = float(config["deviation_threshold"])
        self.threshold = symmetric          # retained for introspection
        self.buy_threshold = float(
            config.get("buy_deviation_threshold", symmetric))
        self.sell_threshold = float(
            config.get("sell_deviation_threshold", symmetric))
        if self.buy_threshold <= 0 or self.sell_threshold <= 0:
            raise ValueError(
                f"{symbol}: deviation thresholds must be positive fractions")

        dca = config.get("dca") or {}
        # 1 tranche is "no ladder" — exactly the previous behaviour, so an
        # instrument without a `dca` block is unaffected.
        self.max_tranches = int(dca.get("max_tranches", 1))
        self.dca_step = float(dca.get("step_multiple", 1.0))
        if self.max_tranches < 1:
            raise ValueError(f"{symbol}: dca.max_tranches must be >= 1")
        if self.dca_step < 0:
            raise ValueError(f"{symbol}: dca.step_multiple cannot be negative")

    # --- scale-in ladder --------------------------------------------------

    def tranches_held(self, position: int) -> int:
        """How many clips of the ladder are already on.

        Derived from the position rather than counted in state, so it survives a
        restart, needs no reset at a session boundary, and cannot drift from
        what the exchange thinks we hold.
        """
        if self.trade_quantity <= 0:
            return 0
        return max(0, position) // self.trade_quantity

    def required_deviation(self, position: int) -> float:
        """The deviation the NEXT tranche demands.

        Each successive tranche needs a deeper dip: tranche n requires
        `buy_threshold * (1 + n * step_multiple)`. With the defaults that is
        1x, 2x, 3x the band. So a single shallow dip only ever buys once, and
        the ladder only extends if the price genuinely keeps moving away.
        """
        return self.buy_threshold * (
            1 + self.tranches_held(position) * self.dca_step)

    def signal(self, position: int):
        if not self.ready():
            return None
        mean = sum(self.prices) / len(self.prices)
        last = self.prices[-1]
        if mean <= 0:
            return None
        deviation = (last - mean) / mean

        if deviation <= -self.required_deviation(position):
            # Below the mean: expect reversion up -> buy. Successive tranches
            # demand successively deeper dips, and the ladder stops at
            # max_tranches.
            if self.tranches_held(position) >= self.max_tranches:
                return None
            # The spread and liquidity gates apply to ENTRIES only — a thin
            # book must never stop us reducing, or the illiquidity that
            # triggered them traps the position.
            if not self.spread_ok():
                return None
            limit = self._limit_price(last, "buy")
            quantity = self.entry_size(limit)
            if quantity <= 0:
                return None
            return Signal("buy", quantity, limit)
        if deviation >= self.sell_threshold and position > 0:
            # Above the mean and holding: take the reversion -> sell to close.
            qty = min(self.trade_quantity, position)
            return Signal("sell", qty, self._limit_price(last, "sell"))
        return None
