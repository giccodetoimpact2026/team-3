"""Crash guard: dump a position into the resting bids when a price collapses.

The exchange is limit-only, so "sell at whatever the buyers are already
bidding" means one sell order priced at the bid level that absorbs the whole
position. Matching is price-time priority, so that single order fills against
every bid at or above its price, best first. No need to submit one order per
resting buyer.

Three guards against the naive version:

  - `confirm_ticks` consecutive breaching prices, so one bad tick cannot dump.
  - A floor (`floor_pct` under the trigger price) so a thin book cannot pull
    the sweep price to nothing; the remainder rests at the floor.
  - A cooldown after firing, during which the strategies stay quiet so
    momentum cannot buy straight back.

Prices are integer cents throughout.
"""
import threading
import time

from risk.flatten import Flattener, sweep_price  # noqa: F401 (re-export)
from utils.logger import log


class CrashGuard:
    """Per-instrument trigger: `confirm_ticks` prices in a row at least
    `drop_pct` below the window high. Fires once, then cools down."""

    def __init__(self, symbols, drop_pct=0.15, confirm_ticks=3, floor_pct=0.03,
                 cooldown_seconds=300.0, clock=time.monotonic):
        self.drop_pct = float(drop_pct)
        self.confirm_ticks = max(1, int(confirm_ticks))
        self.floor_pct = float(floor_pct)
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._symbols = list(symbols)
        self._state = {}
        self.reset_for_session()

    def reset_for_session(self) -> None:
        with self._lock:
            self._state = {
                s: {"high": None, "breaches": 0, "cooldown_until": 0.0}
                for s in self._symbols
            }

    def in_cooldown(self, symbol: str) -> bool:
        with self._lock:
            st = self._state.get(symbol)
            return bool(st) and self._clock() < st["cooldown_until"]

    def on_price(self, symbol: str, price: int) -> bool:
        """Feed one price. True exactly when the guard fires for `symbol`."""
        with self._lock:
            st = self._state.get(symbol)
            if st is None or price is None or int(price) <= 0:
                return False
            price = int(price)
            now = self._clock()

            if now < st["cooldown_until"]:
                # Track the level we dumped at so the guard rearms from there.
                st["high"] = max(st["high"] or price, price)
                return False

            if st["high"] is None or price > st["high"]:
                st["high"] = price
                st["breaches"] = 0
                return False

            threshold = st["high"] * (1.0 - self.drop_pct)
            if price <= threshold:
                st["breaches"] += 1
            else:
                st["breaches"] = 0
                return False

            if st["breaches"] < self.confirm_ticks:
                return False

            st["breaches"] = 0
            st["cooldown_until"] = now + self.cooldown_seconds
            st["high"] = price   # rearm from the crash level, not the old high
            log(f"crash guard: {symbol} at {price} is {self.drop_pct:.0%} under the "
                f"window high — dumping position")
            return True

    def floor_for(self, trigger_price: int) -> int:
        return int(trigger_price * (1.0 - self.floor_pct))

    def status(self) -> dict:
        with self._lock:
            now = self._clock()
            out = {
                "drop_pct": self.drop_pct,
                "confirm_ticks": self.confirm_ticks,
                "floor_pct": self.floor_pct,
                "cooldown_seconds": self.cooldown_seconds,
            }
            for sym, st in self._state.items():
                out[sym] = {
                    "high": st["high"],
                    "breaches": st["breaches"],
                    "cooldown": now < st["cooldown_until"],
                    "cooldown_remaining_s": round(max(0.0, st["cooldown_until"] - now), 1),
                }
            return out


class CrashResponder:
    """Wires the guard to the shared exit path (risk/exits.py).

    On a trigger: read the book snapshot, price the sweep, flatten. If the dump
    order is still resting `reprice_after` seconds later, chase it once with a
    fresh book read. Strategies are told to stand down while the symbol cools.
    """

    def __init__(self, guard, client, orders, reprice_after=5.0, clock=None):
        self.guard = guard
        self._flattener = Flattener(client, orders, floor_pct=guard.floor_pct,
                                    reprice_after=reprice_after,
                                    clock=clock or guard._clock, label="crash guard")

    def allows_strategy(self, symbol: str) -> bool:
        return not self.guard.in_cooldown(symbol)

    def on_price(self, symbol: str, price: int) -> None:
        if self.guard.on_price(symbol, price):
            self._flattener.flatten(symbol, int(price))
        else:
            self._flattener.on_price(symbol, int(price))

    def status(self) -> dict:
        out = self.guard.status()
        out["exits"] = self._flattener.status()
        return out
