"""Leader-follower strategy (AXON off NOVA).

Energy costs drive freight margins, so the energy name leads the freight name:
when NOVA breaks a new high, AXON tends to follow (James Wee's notes, page 4;
Derek Tan: "NOVA breaks a new high, go long AXON").

**This is not a pairs trade.** A pairs trade is long one leg and short the
other; this exchange is long-only, so there is no short leg. What this expresses
is a single *correlated long* triggered by another instrument's breakout, which
has a consequence worth stating plainly: the follower's exposure stacks on top
of the leader's rather than hedging it. Clarissa Ng's warning applies directly —
"NOVA and AXON move together; if we're long both we've doubled a position" — so
the follower is sized at the leader's cap, and a portfolio-level cap across the
pair is still outstanding (BUG-008).

Entry and exit are deliberately symmetric around the leader's window high:
 - the leader printing a new high arms the follower for a few of its own ticks;
 - the leader falling `exit_drawdown` back off that high closes the follower.

Prices are integer cents throughout.
"""
from .base import BaseStrategy, Signal


class PairsFollowStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict, leader):
        super().__init__(symbol, config)
        self.leader = leader
        self.breakout_samples = int(config["leader_breakout_samples"])
        self.arm_samples = int(config["breakout_arm_samples"])
        self.exit_drawdown = float(config["exit_drawdown"])

        if self.breakout_samples < 2:
            raise ValueError(
                f"{symbol}: leader_breakout_samples must be >= 2 to have a prior "
                f"window to compare against")
        if self.breakout_samples > leader.lookback:
            # The leader's buffer is capped at its own lookback, so a wider
            # breakout window could never fill and the follower would silently
            # never trade. Fail at startup instead.
            raise ValueError(
                f"{symbol}: leader_breakout_samples ({self.breakout_samples}) "
                f"exceeds {leader.symbol} lookback_samples ({leader.lookback}); "
                f"widen the leader's lookback or narrow the breakout window")

        # Ticks of *our own* price series since the leader last broke out.
        # None means "never armed".
        self._ticks_since_breakout = None

    # --- readiness --------------------------------------------------------

    def ready(self) -> bool:
        """Overrides the base: this strategy reads two price series.

        Our own buffer only supplies the reference price for the limit, so one
        sample is enough there; the signal itself lives in the leader's window,
        which must be full before a "new high" means anything.
        """
        return bool(self.prices) and len(self.leader.prices) >= self.breakout_samples

    # --- leader state -----------------------------------------------------

    def _leader_window(self):
        return list(self.leader.prices)[-self.breakout_samples:]

    def _leader_new_high(self) -> bool:
        """True when the leader's latest print is above its whole prior window.

        Strictly greater, so a flat series never counts as a breakout.
        """
        window = self._leader_window()
        return window[-1] > max(window[:-1])

    def _leader_rolled_over(self) -> bool:
        """True when the leader has given back `exit_drawdown` from its high."""
        window = self._leader_window()
        high = max(window)
        if high <= 0:
            return False
        return (window[-1] - high) / high <= -self.exit_drawdown

    # --- price ingestion --------------------------------------------------

    def on_price(self, last_price: int):
        """Record our own price and age the breakout arm.

        The arm is measured in follower ticks rather than wall-clock: the two
        instruments publish independently, and what matters is how much of the
        follower's own move has already happened since the leader's signal.
        """
        super().on_price(last_price)
        if len(self.leader.prices) < self.breakout_samples:
            return
        if self._leader_new_high():
            self._ticks_since_breakout = 0
        elif self._ticks_since_breakout is not None:
            self._ticks_since_breakout += 1

    @property
    def armed(self) -> bool:
        return (self._ticks_since_breakout is not None
                and self._ticks_since_breakout < self.arm_samples)

    # --- signal -----------------------------------------------------------

    def signal(self, sellable: int):
        """Buy while the leader's breakout is fresh; sell when it rolls over.

        `sellable` is what we may actually sell — held shares net of those
        already reserved by our own resting sells — so the exit never oversells
        into a 400 INSUFFICIENT_POSITION.
        """
        if not self.ready():
            return None
        last = self.prices[-1]
        if last <= 0:
            return None

        if self.armed:
            # The spread gate applies to ENTRIES only — a wide book must never
            # stop us reducing, or the illiquidity that made it wide traps the
            # position.
            if not self.spread_ok():
                return None
            return Signal("buy", self.trade_quantity, self._limit_price(last, "buy"))

        if sellable > 0 and self._leader_rolled_over():
            # The leading signal is gone and we still hold: reduce/close.
            self._ticks_since_breakout = None
            qty = min(self.trade_quantity, sellable)
            return Signal("sell", qty, self._limit_price(last, "sell"))

        return None

    def reset(self):
        super().reset()
        self._ticks_since_breakout = None
