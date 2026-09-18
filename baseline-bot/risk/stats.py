"""Per-instrument market statistics for the risk model.

Everything here is computed from the public SSE feed alone: `price` events build
fixed-length time buckets ("bars"), `book_update` events give visible depth.
No REST call is made and nothing is cached across a session boundary.

Why bars and not raw samples: ticks arrive irregularly (0.2 to 0.4 per second
per instrument on the sandbox), so "the last 14 samples" spans anything from
30 seconds to several minutes and a quiet spell reads as low volatility while
the price is mid-swing. Bucketing ticks into `bucket_seconds` bars makes the
ATR a function of time, which is what a stop distance and a regime need.

True range per closed bar is the classic definition, gap included:

    TR = max(high, prev_close) - min(low, prev_close)
    ATR = mean(TR over the last `atr_buckets` bars)          (James p.3, M ~ 14)
    ATR_pct = ATR / last_close

Regime (James p.3) is LOW / NEUTRAL / HIGH against per-instrument thresholds,
with hysteresis: a change is adopted only after `regime_confirm_buckets`
consecutive closed bars agree.

A bar with no ticks carries the previous close with zero range, so a market
that stops trading reads as quiet rather than as missing data.

Prices are integer cents throughout. Thread-safe.
"""
import itertools
import math
import threading
import time
from collections import deque

LOW = "LOW"
NEUTRAL = "NEUTRAL"
HIGH = "HIGH"

# Silent bars filled in one go are capped so a clock jump cannot spin.
_MAX_GAP_FILL = 1_000


class _Series:
    __slots__ = ("bar_idx", "open", "high", "low", "close", "prev_close",
                 "trs", "rets", "last_price", "open_price", "regime",
                 "candidate", "candidate_count", "depth")

    def __init__(self, history):
        self.bar_idx = None
        self.open = self.high = self.low = self.close = None
        self.prev_close = None
        self.trs = deque(maxlen=history)          # true range per closed bar (cents)
        self.rets = deque(maxlen=history)         # (bar index, log return) per closed bar
        self.last_price = None
        self.open_price = None
        self.regime = None
        self.candidate = None
        self.candidate_count = 0
        self.depth = None


class MarketStats:
    def __init__(self, symbols, bucket_seconds=10.0, atr_buckets=14,
                 regime_thresholds=None, default_thresholds=(0.003, 0.008),
                 regime_confirm_buckets=3, correlation_buckets=30,
                 correlation_min_samples=20, clock=time.monotonic):
        self.bucket_seconds = float(bucket_seconds)
        self.atr_buckets = max(1, int(atr_buckets))
        self.regime_confirm = max(1, int(regime_confirm_buckets))
        self.correlation_buckets = max(2, int(correlation_buckets))
        self.correlation_min_samples = max(2, int(correlation_min_samples))
        self._thresholds = dict(regime_thresholds or {})
        self._default_thresholds = tuple(default_thresholds)
        self._clock = clock
        self._lock = threading.Lock()
        self._symbols = list(symbols)
        self._history = max(self.atr_buckets, self.correlation_buckets) + 1
        self._series = {}
        self.reset_for_session()

    # --- lifecycle -------------------------------------------------------

    def reset_for_session(self) -> None:
        """Drop every bar, mark and regime: prices re-anchor at a window open."""
        with self._lock:
            self._series = {s: _Series(self._history) for s in self._symbols}

    def thresholds(self, symbol: str) -> tuple:
        return self._thresholds.get(symbol, self._default_thresholds)

    # --- feed ------------------------------------------------------------

    def on_price(self, symbol: str, price, now=None) -> None:
        """Feed one `price` event. Unknown symbols and bad prices are ignored."""
        try:
            price = int(price)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        with self._lock:
            st = self._series.get(symbol)
            if st is None:
                return
            now = self._clock() if now is None else now
            idx = int(now // self.bucket_seconds)
            st.last_price = price
            if st.open_price is None:
                st.open_price = price
            if st.bar_idx is None:
                self._open_bar(st, idx, price)
                return
            if idx == st.bar_idx:
                st.high = max(st.high, price)
                st.low = min(st.low, price)
                st.close = price
                return
            self._close_bar(st, symbol)
            gaps = min(idx - st.bar_idx - 1, _MAX_GAP_FILL)
            for _ in range(gaps):
                st.bar_idx += 1
                self._open_bar(st, st.bar_idx, st.prev_close)
                self._close_bar(st, symbol)
            self._open_bar(st, idx, price)

    def on_book(self, symbol: str, bids, asks) -> None:
        """Feed one `book_update` (top-5 resting orders per side)."""
        with self._lock:
            st = self._series.get(symbol)
            if st is None:
                return
            bid_prices, bid_qty = self._side(bids)
            ask_prices, ask_qty = self._side(asks)
            st.depth = {
                "best_bid": max(bid_prices) if bid_prices else None,
                "best_ask": min(ask_prices) if ask_prices else None,
                "bid_depth": bid_qty,
                "ask_depth": ask_qty,
            }

    @staticmethod
    def _side(entries):
        prices, total = [], 0
        for entry in entries or []:
            try:
                price = int(entry["price"])
                qty = int(entry["quantity"])
            except (KeyError, TypeError, ValueError):
                continue
            if price <= 0 or qty <= 0:
                continue
            prices.append(price)
            total += qty
        return prices, total

    # --- bars ------------------------------------------------------------

    @staticmethod
    def _open_bar(st, idx, price):
        st.bar_idx = idx
        st.open = st.high = st.low = st.close = price

    def _close_bar(self, st, symbol):
        if st.prev_close is not None:
            tr = max(st.high, st.prev_close) - min(st.low, st.prev_close)
            st.trs.append(tr)
            ret = (math.log(st.close / st.prev_close)
                   if st.close > 0 and st.prev_close > 0 else 0.0)
            st.rets.append((st.bar_idx, ret))
            self._update_regime(st, symbol)
        st.prev_close = st.close

    def _update_regime(self, st, symbol):
        atr_pct = self._atr_pct(st)
        if atr_pct is None:
            return
        low, high = self.thresholds(symbol)
        if atr_pct < low:
            candidate = LOW
        elif atr_pct > high:
            candidate = HIGH
        else:
            candidate = NEUTRAL
        if candidate == st.regime:
            st.candidate, st.candidate_count = None, 0
            return
        if candidate == st.candidate:
            st.candidate_count += 1
        else:
            st.candidate, st.candidate_count = candidate, 1
        if st.candidate_count >= self.regime_confirm:
            st.regime = candidate
            st.candidate, st.candidate_count = None, 0

    # --- reads -----------------------------------------------------------

    def _atr(self, st):
        if len(st.trs) < self.atr_buckets:
            return None
        window = list(st.trs)[-self.atr_buckets:]
        return sum(window) / len(window)

    def _atr_pct(self, st):
        atr = self._atr(st)
        if atr is None or not st.prev_close and not st.close:
            return None
        ref = st.close if st.close else st.prev_close
        return atr / ref if ref else None

    def atr(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None else self._atr(st)

    def atr_pct(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None else self._atr_pct(st)

    def regime(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None else st.regime

    def last_price(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None else st.last_price

    def session_open_price(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None else st.open_price

    def depth(self, symbol: str):
        with self._lock:
            st = self._series.get(symbol)
            return None if st is None or st.depth is None else dict(st.depth)

    def correlation(self, a: str, b: str):
        """Pearson correlation of bar log returns over the correlation window,
        or None when fewer than `correlation_min_samples` bars overlap or a
        series is flat."""
        with self._lock:
            return self._correlation(a, b)

    def _correlation(self, a, b):
        sa, sb = self._series.get(a), self._series.get(b)
        if sa is None or sb is None:
            return None
        ra = dict(list(sa.rets)[-self.correlation_buckets:])
        rb = dict(list(sb.rets)[-self.correlation_buckets:])
        common = sorted(set(ra) & set(rb))
        if len(common) < self.correlation_min_samples:
            return None
        xs = [ra[i] for i in common]
        ys = [rb[i] for i in common]
        n = len(common)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        if sxx <= 0 or syy <= 0:
            return None
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        return sxy / math.sqrt(sxx * syy)

    def correlation_matrix(self) -> dict:
        with self._lock:
            return {f"{a}/{b}": self._correlation(a, b)
                    for a, b in itertools.combinations(self._symbols, 2)}

    def status(self) -> dict:
        with self._lock:
            out = {
                "bucket_seconds": self.bucket_seconds,
                "atr_buckets": self.atr_buckets,
                "regime_confirm_buckets": self.regime_confirm,
                "correlation_window_seconds": self.correlation_buckets * self.bucket_seconds,
                "instruments": {},
            }
            for sym, st in self._series.items():
                low, high = self.thresholds(sym)
                atr = self._atr(st)
                out["instruments"][sym] = {
                    "last_price": st.last_price,
                    "session_open_price": st.open_price,
                    "atr": None if atr is None else round(atr, 2),
                    "atr_pct": None if atr is None else self._atr_pct(st),
                    "regime": st.regime,
                    "low_vol": low,
                    "high_vol": high,
                    "bars": len(st.trs),
                    "depth": dict(st.depth) if st.depth else None,
                }
            out["correlation"] = {f"{a}/{b}": self._correlation(a, b)
                                  for a, b in itertools.combinations(self._symbols, 2)}
            return out
