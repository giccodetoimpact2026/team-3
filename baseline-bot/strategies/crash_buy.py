"""Crash-buy strategy: scale aggressively INTO a sharp sell-off, sell the rebound.

The observed sessions (see team-11-analysis.md, "The 13:42 event") show the
shape this is built for: an announcement knocks a liquid name ~15-20% in one
move and the price then V-recovers toward its pre-event level. Momentum sells
that dip (or never buys it) and 15-sample mean-reversion buys one clip and stops
— neither collects the rebound. This strategy does one thing: when a crash is
detected it ladders in with escalating clips, then unwinds once the price has
reclaimed a configured fraction of the drop.

Definitions (prices are integer cents throughout):

  reference  = highest price in the lookback window BEFORE the crash began
  drop       = (reference - last) / reference          (positive on a fall)
  crash      = drop >= crash_drop_pct                  (opens an "episode")
  recovery   = last >= reference - (1 - recovery_fraction) * (reference - low)
               i.e. price has clawed back `recovery_fraction` of the gap from
               the episode low back to the reference -> SELL to close

Buying is a LADDER, not a single clip. Tranche k (1-based) is placed when price
is at least `k * tranche_step_pct` below the reference, sized
`trade_quantity * tranche_scale ** (k-1)`, capped at `max_tranches` per episode.
The deeper it falls the more we buy — that is the "aggressive" part — and it is
also the dangerous part, so three brakes are built in and none of them is
optional to get wrong:

  1. `confirm_samples`: a tranche is only placed once the price has NOT made a
     new episode low for that many consecutive samples. Zero means "buy the
     first print of the crash". James Wee's page-2 warning about catching a
     falling knife applies with full force here; the default is 2.
  2. `max_tranches` caps how many times we add in one episode, and the risk
     layer's `max_position` still applies on top of it (OrderManager enforces
     it against projected exposure, this module cannot bypass it).
  3. `abandon_drop_pct`: if the drop exceeds this the episode is NOT a dip, it
     is a repricing (delisting-grade news, a fundamental break). No further
     tranches are placed and the position is left to the configured stop-loss
     in `risk.exits`. This strategy never places a stop itself — exits are the
     risk layer's job and run before the strategy on every price event.

Long-only: every sell reduces or closes a held long. The spread gate applies to
ENTRIES only — a wide book must never stop us reducing.

What this module deliberately does not do: it does not read announcements
(the news engine does — and note that a bearish news bias SUPPRESSES buys in
main.py, which will fight this strategy on exactly the announcement-driven
crashes it exists for; see NOTES in the module docstring's companion section of
config.yaml), it does not decide which instrument it belongs on, and it does
not claim the rebound will happen. On a thin book (DRFT) our own ladder would
BE the rebound — do not run it there.
"""
from .base import BaseStrategy, Signal


class CrashBuyStrategy(BaseStrategy):
    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.crash_drop_pct = float(config["crash_drop_pct"])
        self.tranche_step_pct = float(
            config.get("tranche_step_pct", self.crash_drop_pct))
        self.tranche_scale = float(config.get("tranche_scale", 1.5))
        self.max_tranches = int(config.get("max_tranches", 3))
        self.confirm_samples = int(config.get("confirm_samples", 2))
        self.recovery_fraction = float(config.get("recovery_fraction", 0.6))
        self.abandon_drop_pct = float(config.get("abandon_drop_pct", 0.35))
        self.sell_clip = int(config.get("sell_quantity", self.trade_quantity))

        if not 0 < self.crash_drop_pct < 1:
            raise ValueError(f"{symbol}: crash_drop_pct must be a fraction in (0, 1)")
        if not 0 < self.tranche_step_pct < 1:
            raise ValueError(f"{symbol}: tranche_step_pct must be a fraction in (0, 1)")
        if self.tranche_scale < 1:
            raise ValueError(f"{symbol}: tranche_scale must be >= 1 (ladder never shrinks)")
        if self.max_tranches < 1:
            raise ValueError(f"{symbol}: max_tranches must be >= 1")
        if self.confirm_samples < 0:
            raise ValueError(f"{symbol}: confirm_samples cannot be negative")
        if not 0 < self.recovery_fraction <= 1:
            raise ValueError(f"{symbol}: recovery_fraction must be in (0, 1]")
        if self.abandon_drop_pct <= self.crash_drop_pct:
            raise ValueError(
                f"{symbol}: abandon_drop_pct must exceed crash_drop_pct, or every "
                f"crash is abandoned the moment it is detected")
        if self.sell_clip < 1:
            raise ValueError(f"{symbol}: sell_quantity must be >= 1")

        self._reset_episode()

    # --- episode state ----------------------------------------------------

    def _reset_episode(self):
        self.reference = None        # pre-crash high, cents
        self.low = None              # lowest print seen in the episode
        self.tranches = 0            # tranches placed this episode
        self.samples_since_low = 0   # confirmation counter
        self.abandoned = False       # drop blew through abandon_drop_pct
        self.held = False            # a tranche filled at some point

    @property
    def in_episode(self) -> bool:
        return self.reference is not None

    def reset(self):
        super().reset()
        self._reset_episode()

    # --- helpers ----------------------------------------------------------

    def _drop(self, price: int) -> float:
        return (self.reference - price) / self.reference

    def _recovery_level(self) -> int:
        gap = self.reference - self.low
        return int(self.reference - (1.0 - self.recovery_fraction) * gap)

    def tranche_quantity(self, k: int) -> int:
        """Size of the k-th (1-based) tranche."""
        return max(1, int(self.trade_quantity * self.tranche_scale ** (k - 1)))

    def _observe_episode(self, last: int):
        """Update episode bookkeeping for the newest print."""
        if last < self.low:
            self.low = last
            self.samples_since_low = 0
        else:
            self.samples_since_low += 1
        if self._drop(last) >= self.abandon_drop_pct:
            self.abandoned = True

    # --- signal -----------------------------------------------------------

    def signal(self, position: int):
        if not self.ready():
            return None
        last = self.prices[-1]

        if not self.in_episode:
            # The reference is the high of the window EXCLUDING the newest
            # print, so a crash that begins on this very sample is measured
            # against what came before it.
            reference = max(list(self.prices)[:-1])
            if reference <= 0:
                return None
            if (reference - last) / reference < self.crash_drop_pct:
                return None
            self.reference = reference
            self.low = last
            self.samples_since_low = 0
            if self._drop(last) >= self.abandon_drop_pct:
                self.abandoned = True
        else:
            self._observe_episode(last)

        # A position that was held and is now gone while price is still below
        # the recovery level was closed by something else — the risk layer's
        # stop-loss. Re-laddering into the same crash after a stop is exactly
        # the loop a stop exists to break, so the episode is abandoned.
        if position > 0:
            self.held = True
        elif self.held and last < self._recovery_level():
            self.abandoned = True

        # --- exit leg: the rebound ---------------------------------------
        if last >= self._recovery_level():
            if position > 0:
                qty = min(self.sell_clip, position)
                return Signal("sell", qty, self._limit_price(last, "sell"))
            # Recovered and flat: the episode is over. Also the escape hatch
            # for an episode that never got a fill.
            self._reset_episode()
            return None

        # --- entry leg: the ladder ---------------------------------------
        if self.abandoned or self.tranches >= self.max_tranches:
            return None
        if self.samples_since_low < self.confirm_samples:
            return None
        next_k = self.tranches + 1
        if self._drop(last) < next_k * self.tranche_step_pct:
            return None
        if not self.spread_ok():
            return None
        self.tranches = next_k
        return Signal("buy", self.tranche_quantity(next_k),
                      self._limit_price(last, "buy"))
