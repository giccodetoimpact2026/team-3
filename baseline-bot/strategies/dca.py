"""Dollar-cost-averaging overlay.

Runs beside the per-instrument strategies rather than through the router, so
it covers every discovered instrument regardless of which strategy (if any)
owns it. Each interval it buys a fixed cash amount at the last price. It never
sells; exits belong to the strategies and the crash guard.

All gating (position cap, cash, one live order per symbol, halt, pause) is
the OrderManager's. A locally refused buy is simply retried a few seconds
later rather than at the next full interval.
"""
import time

from utils.logger import log


class DcaOverlay:
    def __init__(self, symbols, orders, cash_per_buy_cents, interval_seconds=60.0,
                 retry_seconds=5.0, limit_offset_bps=5, allow=None, clock=time.monotonic):
        self._orders = orders
        self._cash = int(cash_per_buy_cents)
        self._interval = float(interval_seconds)
        self._retry = float(retry_seconds)
        self._offset_bps = int(limit_offset_bps)
        self._allow = allow or (lambda symbol: True)
        self._clock = clock
        # None = never bought; otherwise the time of the last acknowledged buy.
        self._last_buy = {s: None for s in symbols}
        self._next_try = {s: 0.0 for s in symbols}
        self._buys = {s: 0 for s in symbols}

    def reset_for_session(self) -> None:
        for s in self._last_buy:
            self._last_buy[s] = None
            self._next_try[s] = 0.0
            self._buys[s] = 0

    def on_price(self, symbol: str, price) -> None:
        if symbol not in self._last_buy or not price or int(price) <= 0:
            return
        now = self._clock()
        if now < self._next_try[symbol]:
            return
        last = self._last_buy[symbol]
        if last is not None and now - last < self._interval:
            return
        if not self._allow(symbol):
            self._next_try[symbol] = now + self._retry
            return

        price = int(price)
        quantity = self._cash // price
        if quantity <= 0:
            self._next_try[symbol] = now + self._interval
            return
        limit = price + price * self._offset_bps // 10_000
        result = self._orders.submit_intent(symbol, "buy", quantity, limit, source="dca")
        if result is not None and result.acknowledged:
            self._last_buy[symbol] = now
            self._next_try[symbol] = now + self._interval
            self._buys[symbol] += 1
            log(f"dca: {symbol} bought {quantity}@{limit} (#{self._buys[symbol]})")
        else:
            # Refused locally (slot busy, cap, cash) or rejected: try again soon.
            self._next_try[symbol] = now + self._retry

    def status(self) -> dict:
        now = self._clock()
        return {
            "interval_seconds": self._interval,
            "cash_per_buy_cents": self._cash,
            "symbols": {
                s: {"buys": self._buys[s],
                    "next_in_s": round(max(0.0, self._next_try[s] - now), 1)}
                for s in self._last_buy
            },
        }
