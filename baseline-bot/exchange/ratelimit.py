"""Client-side view of one rate-limit tier.

Each tier is its own rolling 30-second window, burstable, counted per team
(exchange-api-reference.md §Rate limits). Every call counts whether it is
accepted or refused, so a 400 still spends a slot.

We count our own calls to decide *before* sending, then correct the count from
`X-RateLimit-Remaining` whenever the server tells us where we actually stand —
that header is authoritative and means we never have to guess.

`reserve` holds back slots for calls we cannot afford to have refused. On the
shared tier that is shutdown cancels and 504 reconciliation: if `GET /orders`
gets a 429 while an order is unaccounted for, we cannot tell whether it landed.
"""
import threading
import time


class RollingWindow:
    def __init__(self, limit: int, window: float = 30.0, reserve: int = 0,
                 clock=time.monotonic):
        self.limit = int(limit)
        self.window = float(window)
        self.reserve = int(reserve)
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: list[float] = []
        self._blocked_until = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        self._calls = [t for t in self._calls if t > cutoff]

    def available(self, privileged: bool = False) -> int:
        """Slots we believe are free. `privileged` may draw on the reserve."""
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                return 0
            self._prune(now)
            ceiling = self.limit if privileged else self.limit - self.reserve
            return max(0, ceiling - len(self._calls))

    def acquire(self, privileged: bool = False) -> bool:
        """Spend a slot if one is free. False means: don't send, you'd get a 429."""
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                return False
            self._prune(now)
            ceiling = self.limit if privileged else self.limit - self.reserve
            if len(self._calls) >= ceiling:
                return False
            self._calls.append(now)
            return True

    def observe(self, remaining: int | None = None, retry_after: float | None = None) -> None:
        """Reconcile against the response headers.

        `Retry-After` on a 429 is whole seconds until *one* slot returns, not
        until the window clears — so we refuse everything until then rather
        than retrying in a loop.
        """
        with self._lock:
            now = self._clock()
            if retry_after is not None:
                self._blocked_until = max(self._blocked_until, now + float(retry_after))
            if remaining is not None:
                self._prune(now)
                used = max(0, self.limit - int(remaining))
                if used > len(self._calls):
                    # The server counted calls we didn't (another process on the
                    # same team key, or a retry we lost track of). Trust it.
                    self._calls.extend([now] * (used - len(self._calls)))
