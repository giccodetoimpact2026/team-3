"""Single owner of order state: what is resting, what is pending, what is unknown.

Every exchange write goes through here so that the invariants hold in one place:

  - At most `max_active_per_symbol` live orders per symbol. This, not the
    position cap, is what stops a persistent signal from re-submitting on every
    price tick.
  - No new intent for a symbol while that symbol has an unreconciled
    idempotency key. An order we cannot account for is treated as live.
  - Risk sees *projected* exposure (filled + pending buys - pending sells),
    never the filled position alone.
  - New intents can be blocked atomically, before any cancellation begins, so
    shutdown and the emergency halt cannot race a submit.

Fill tracking comes from two places, both authenticated: the synchronous
`POST /orders` response (matching is synchronous, so a crossing order reports
its fills immediately) and `poll()`, which refreshes state from `GET /orders`.
The public SSE `trade` event carries only `{symbol, price, qty, timestamp}` —
no order ids and no team — so our own fills are not identifiable from the feed.

Thread-safe: the trading worker submits, the shutdown path cancels, and both
may touch this concurrently.
"""
import threading
import time
from dataclasses import dataclass

import requests

from exchange.client import positions_by_symbol
from exchange.results import CancelOutcome, SubmitResult
from utils.logger import log

# Order statuses that mean the order is still working on the exchange.
LIVE_STATUSES = ("PENDING", "OPEN", "PARTIALLY_FILLED")


@dataclass
class ActiveOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: int
    remaining: int
    idempotency_key: str
    status: str = "OPEN"


@dataclass
class UnknownIntent:
    """An intent whose fate we don't know. Blocks its symbol until resolved."""
    idempotency_key: str
    symbol: str
    side: str
    quantity: int
    price: int
    submitted_at: float
    absent_reads: int = 0


class OrderManager:
    # A write that timed out may still commit after we gave up waiting, so
    # absence from GET /orders is only meaningful once the write has had time
    # to land. Declaring "not landed" too early is how one unknown order
    # becomes two real ones.
    RECONCILE_MIN_AGE = 2.0
    # Two consecutive absent reads before we conclude the order never landed.
    RECONCILE_ABSENT_READS = 2
    # GET /portfolio is served from a ~1s per-team cache; polling faster than
    # this just spends a shared-tier slot for the same answer.
    POSITION_TTL = 1.0
    # Floor on GET /orders. Price ticks can arrive far faster than the shared
    # tier's 10-calls-per-second budget allows us to read.
    POLL_TTL = 1.0

    def __init__(self, client, risk, session_id=None, max_active_per_symbol=1,
                 order_limiter=None, shared_limiter=None, clock=time.monotonic):
        self._client = client
        self._risk = risk
        self._lock = threading.RLock()
        self._clock = clock
        self._max_active = int(max_active_per_symbol)
        self._order_limiter = order_limiter
        self._shared_limiter = shared_limiter

        self._active: dict[str, ActiveOrder] = {}
        self._unknown: dict[str, UnknownIntent] = {}
        self._filled: dict[str, int] = {}
        self._reserved: dict[str, int] = {}
        # Volume-weighted cost basis per symbol, integer cents, from
        # GET /portfolio. Stop-loss and take-profit measure against this, so
        # it is read from the exchange rather than tracked locally: the levels
        # then survive a restart and reset at a session boundary by themselves.
        self._avg_cost: dict[str, int] = {}
        # Latest known price per symbol, integer cents. Fed both by the
        # live SSE print (record_mark) and by the portfolio's own mark, so a
        # symbol that has not ticked recently still has a usable value.
        # Correlated-group exposure is measured in notional, which needs it.
        self._mark: dict[str, int] = {}
        self._cash = 0
        self._reserved_cash = 0
        self._positions_at: float | None = None
        self._polled_at: float | None = None
        self._session_id = session_id
        self._blocked = False
        self._seq = 0
        # Set from GET /portfolio, which names its window — P3 can watch this
        # for a boundary without polling /session.
        self.last_seen_session_id = None

    # --- lifecycle surface (P3: shutdown, halt, session boundary) ---------

    @property
    def blocked(self) -> bool:
        return self._blocked

    def block_new_intents(self) -> None:
        """Refuse all further intents. Atomic and idempotent.

        Must be called *before* cancellation starts, or the trading worker can
        submit a fresh order into a book we have just cleared.
        """
        with self._lock:
            if not self._blocked:
                self._blocked = True
                log("orders: new intents blocked")

    def allow_new_intents(self) -> None:
        with self._lock:
            self._blocked = False

    def reset_for_session(self, session_id) -> None:
        """Drop all per-window state at a session boundary (P0-13).

        Positions, cash, resting order ids and idempotency keys all belong to
        the window they were created in and are wrong the instant the next one
        opens. Anything still outstanding here is logged, because it means we
        left the previous window without accounting for an order.
        """
        with self._lock:
            if self._active:
                log(f"orders: {len(self._active)} order(s) still tracked at session "
                    f"boundary — they belong to the closed window: "
                    f"{sorted(self._active)}")
            if self._unknown:
                log(f"orders: {len(self._unknown)} unreconciled key(s) abandoned at "
                    f"session boundary: {sorted(self._unknown)}")
            self._active.clear()
            self._unknown.clear()
            self._filled.clear()
            self._reserved.clear()
            self._avg_cost.clear()
            self._mark.clear()
            self._cash = 0
            self._reserved_cash = 0
            self._positions_at = None
            self._polled_at = None
            self._seq = 0
            self._session_id = session_id
        self.refresh_positions(force=True)

    def cancel_all(self) -> tuple[list, list]:
        """Cancel everything we believe is live. Returns (cancelled, unresolved).

        Blocks new intents first. Enumerates from `GET /orders` as well as from
        local state, so an order we lost track of is still cancelled. Tolerates
        the already-filled / already-cancelled race: those count as settled.
        `unresolved` being non-empty is what should make the process exit
        nonzero — it means orders may still be resting.
        """
        self.block_new_intents()
        cancelled, unresolved = [], []
        for order_id in self._enumerate_live():
            result = self._client.cancel_order(order_id)
            self._observe_shared(result)
            if result.outcome is CancelOutcome.CANCELLED:
                cancelled.append(order_id)
            elif result.settled:
                log(f"orders: {order_id} already settled ({result.code})")
            else:
                unresolved.append(order_id)
                log(f"orders: cancel of {order_id} unresolved ({result.code})")
            if result.settled:
                with self._lock:
                    self._active.pop(order_id, None)
        with self._lock:
            unresolved.extend(intent.idempotency_key for intent in self._unknown.values())
        return cancelled, unresolved

    def _enumerate_live(self) -> list:
        """Union of what the exchange says is live and what we think is live."""
        ids = []
        try:
            for order in self._client.get_orders() or []:
                if (order or {}).get("status") in LIVE_STATUSES and order.get("id"):
                    ids.append(order["id"])
        except requests.RequestException as exc:
            log(f"orders: could not enumerate open orders ({exc}); "
                f"falling back to locally tracked orders")
        with self._lock:
            ids.extend(oid for oid in self._active if oid not in ids)
        return ids

    # --- exposure --------------------------------------------------------

    def _pending(self, symbol: str, side: str) -> int:
        return sum(o.remaining for o in self._active.values()
                   if o.symbol == symbol and o.side == side)

    def projected_position(self, symbol: str) -> int:
        """Filled + pending buys - pending sells (P0-02).

        This is what the position cap must be checked against. Unknown intents
        count at full size: an order we cannot account for might be live.
        """
        with self._lock:
            unknown_buy = sum(i.quantity for i in self._unknown.values()
                              if i.symbol == symbol and i.side == "buy")
            unknown_sell = sum(i.quantity for i in self._unknown.values()
                               if i.symbol == symbol and i.side == "sell")
            return (self._filled.get(symbol, 0)
                    + self._pending(symbol, "buy") + unknown_buy
                    - self._pending(symbol, "sell") - unknown_sell)

    def record_mark(self, symbol: str, price: int) -> None:
        """Record the freshest observed price. Called on every price event."""
        if price and price > 0:
            with self._lock:
                self._mark[symbol] = int(price)

    def mark(self, symbol: str) -> int:
        """Latest known price in cents, or 0 if the symbol has never traded."""
        with self._lock:
            return self._mark.get(symbol, 0)

    def projected_notional(self, symbols) -> int:
        """Projected exposure across `symbols`, in cents.

        Each symbol's projected position (filled + pending buys - pending
        sells, unknowns at full size) marked to its latest price. A symbol
        with no known mark contributes 0 — it has never traded, so we hold
        none of it and no order can have filled.
        """
        total = 0
        for symbol in symbols:
            projected = self.projected_position(symbol)
            if projected <= 0:
                continue
            total += projected * self.mark(symbol)
        return total

    def avg_cost(self, symbol: str) -> int:
        """Volume-weighted cost basis in cents; 0 when flat or unknown.

        0 is the exchange's own "flat" marker, and the exit policy treats it
        as "nothing to protect" rather than guessing at a basis.
        """
        with self._lock:
            return self._avg_cost.get(symbol, 0)

    def available_to_sell(self, symbol: str) -> int:
        """Held shares not already locked by one of our resting sells.

        Cross-checked against the portfolio's own `reserved_qty`; we take the
        more conservative of the two, and log a divergence because it means our
        order state has drifted from the exchange's.
        """
        with self._lock:
            held = self._filled.get(symbol, 0)
            ours = self._pending(symbol, "sell") + sum(
                i.quantity for i in self._unknown.values()
                if i.symbol == symbol and i.side == "sell")
            theirs = self._reserved.get(symbol, 0)
            if theirs > ours:
                log(f"{symbol}: reserved_qty {theirs} exceeds tracked resting sells "
                    f"{ours} — order state has drifted")
            return max(0, held - max(ours, theirs))

    def has_active(self, symbol: str) -> bool:
        with self._lock:
            return bool(self._active_ids(symbol)) or self._symbol_unresolved(symbol)

    def _active_ids(self, symbol: str) -> list:
        return [oid for oid, o in self._active.items() if o.symbol == symbol]

    def _symbol_unresolved(self, symbol: str) -> bool:
        return any(i.symbol == symbol for i in self._unknown.values())

    # --- reads -----------------------------------------------------------

    def refresh_positions(self, force: bool = False) -> None:
        """Refresh filled positions and cash from GET /portfolio.

        Degrades rather than raising: a 429 or 503 here must not take down the
        caller, it just means we keep working from the last known figures.
        """
        with self._lock:
            fresh = (self._positions_at is not None
                     and self._clock() - self._positions_at < self.POSITION_TTL)
        if fresh and not force:
            return
        if self._shared_limiter and not self._shared_limiter.acquire():
            return
        try:
            portfolio = self._client.get_portfolio()
        except requests.RequestException as exc:
            log(f"orders: portfolio read failed ({exc}); using last known positions")
            return
        by_symbol = positions_by_symbol(portfolio)
        with self._lock:
            self._filled = {s: d["quantity"] for s, d in by_symbol.items()}
            self._reserved = {s: d["reserved_qty"] for s, d in by_symbol.items()}
            self._avg_cost = {s: d["avg_cost"] for s, d in by_symbol.items()}
            for s, d in by_symbol.items():
                if d["last_price"] > 0:
                    self._mark[s] = d["last_price"]
            self._cash = int((portfolio or {}).get("cash") or 0)
            self._reserved_cash = int((portfolio or {}).get("reserved_cash") or 0)
            self._positions_at = self._clock()
            self.last_seen_session_id = (portfolio or {}).get("session_id")

    def poll(self, force: bool = False) -> None:
        """One pass of order-state maintenance. Safe to call on every tick.

        A single `GET /orders` covers fills, external cancels and 504
        reconciliation, so this costs one shared-tier slot rather than three —
        and it is skipped entirely when there is nothing outstanding to learn
        about, which is most ticks. Reconciliation reads are privileged: a 429
        while an order is unaccounted for leaves us unable to tell whether it
        landed.
        """
        with self._lock:
            outstanding = bool(self._active) or bool(self._unknown)
            privileged = bool(self._unknown)
            recent = (self._polled_at is not None
                      and self._clock() - self._polled_at < self.POLL_TTL)
        if outstanding and (force or not recent):
            if not self._shared_limiter or self._shared_limiter.acquire(privileged):
                try:
                    orders = self._client.get_orders() or []
                except requests.RequestException as exc:
                    log(f"orders: order read failed ({exc})")
                else:
                    with self._lock:
                        self._polled_at = self._clock()
                    self._sync_active(orders)
                    self._reconcile_unknown(orders)
        self.refresh_positions()

    def _sync_active(self, orders) -> None:
        by_id = {o["id"]: o for o in orders if (o or {}).get("id")}
        with self._lock:
            for order_id, tracked in list(self._active.items()):
                latest = by_id.get(order_id)
                if latest is None:
                    continue
                status = latest.get("status")
                tracked.status = status or tracked.status
                tracked.remaining = int(latest.get("remaining_qty") or 0)
                if status not in LIVE_STATUSES:
                    # Terminal. The position change shows up in the next
                    # portfolio read, so don't double-count it here.
                    self._active.pop(order_id, None)
                    self._positions_at = None
                    log(f"{tracked.symbol}: order {order_id} {status}")

    def _reconcile_unknown(self, orders) -> None:
        """Resolve timed-out submits by matching on the idempotency key (P0-03).

        Every order in `GET /orders` carries the key it was placed with, which
        is the only handle we have after a 504 — the order id was in the
        response we never received. /orders/{id} cannot be used here: it takes
        an order id, and a key sent there is simply an unknown order.
        """
        by_key = {o["idempotency_key"]: o for o in orders
                  if (o or {}).get("idempotency_key")}
        now = self._clock()
        with self._lock:
            for key, intent in list(self._unknown.items()):
                if now - intent.submitted_at < self.RECONCILE_MIN_AGE:
                    continue
                match = by_key.get(key)
                if match is not None:
                    self._unknown.pop(key, None)
                    self._adopt(match, intent)
                    log(f"{intent.symbol}: unknown order {key} landed as "
                        f"{match.get('id')} ({match.get('status')})")
                    continue
                intent.absent_reads += 1
                if intent.absent_reads >= self.RECONCILE_ABSENT_READS:
                    self._unknown.pop(key, None)
                    log(f"{intent.symbol}: unknown order {key} never landed; "
                        f"symbol unblocked")

    def _adopt(self, order, intent=None) -> None:
        """Take an order the exchange knows about into local tracking."""
        order_id = order.get("id")
        if not order_id:
            return
        if order.get("status") not in LIVE_STATUSES:
            self._positions_at = None
            return
        self._active[order_id] = ActiveOrder(
            order_id=order_id,
            symbol=order.get("symbol") or (intent.symbol if intent else ""),
            side=order.get("side") or (intent.side if intent else ""),
            quantity=int(order.get("quantity") or (intent.quantity if intent else 0)),
            price=int(order.get("price") or (intent.price if intent else 0)),
            remaining=int(order.get("remaining_qty") or 0),
            idempotency_key=order.get("idempotency_key") or (
                intent.idempotency_key if intent else ""),
            status=order.get("status") or "OPEN",
        )
        self._positions_at = None

    # --- writes ----------------------------------------------------------

    def submit_intent(self, symbol: str, side: str, quantity: int,
                      price: int) -> SubmitResult | None:
        """Gate an intent and submit it. None means refused locally, nothing sent.

        Gates run cheapest-and-most-blocking first so a refused intent never
        spends a rate-limit slot or a portfolio read.
        """
        with self._lock:
            if self._blocked:
                return None
            if quantity <= 0 or price <= 0:
                # The exchange would answer 400 and still charge us a slot.
                log(f"{symbol}: intent refused, non-positive size {quantity}@{price}")
                return None
            if self._symbol_unresolved(symbol):
                log(f"{symbol}: intent refused, unreconciled order outstanding")
                return None
            if len(self._active_ids(symbol)) >= self._max_active:
                return None

        self.refresh_positions()

        with self._lock:
            # Re-check under the lock: refresh_positions() released it, and the
            # halt path may have landed in the meantime.
            if self._blocked or self._symbol_unresolved(symbol):
                return None
            if len(self._active_ids(symbol)) >= self._max_active:
                return None
            if side == "buy":
                if not self._risk.allows_buy(symbol, self.projected_position(symbol),
                                             quantity):
                    return None
                if not self._group_allows(symbol, quantity, price):
                    return None
                if not self._affordable(quantity, price):
                    log(f"{symbol}: intent refused, unreserved cash below "
                        f"{quantity * price} cents")
                    return None
            elif not self._risk.allows_sell(symbol, self.available_to_sell(symbol),
                                            quantity):
                return None

            if self._order_limiter and not self._order_limiter.acquire():
                log(f"{symbol}: intent deferred, order rate window spent")
                return None

            key = self._next_key(symbol, side)
            # Recorded *before* the request so a crash between send and
            # response still leaves the key recoverable.
            self._unknown[key] = UnknownIntent(key, symbol, side, quantity, price,
                                               self._clock())

        result = self._client.submit_limit_order(symbol, side, quantity, price, key)
        self._observe_orders(result)

        with self._lock:
            if result.acknowledged:
                self._unknown.pop(key, None)
                self._record_ack(result, symbol, side, quantity, price, key)
            elif result.landed_nothing:
                # 4xx, 429 and 500 are all definitive refusals: no order was
                # created, so the key is spent and the next intent may use a
                # fresh one without risking a duplicate.
                self._unknown.pop(key, None)
                log(f"{symbol} {side} {quantity}@{price} not placed: "
                    f"{result.outcome.value} {result.code}")
            else:
                # UNKNOWN. Keep the key and the symbol blocked until poll()
                # resolves it against GET /orders.
                log(f"{symbol} {side} {quantity}@{price} UNKNOWN ({result.code}); "
                    f"holding key {key} for reconciliation")
        return result

    def _record_ack(self, result, symbol, side, quantity, price, key) -> None:
        if result.filled_qty:
            self._apply_fill(symbol, side, result.filled_qty)
        if result.status in LIVE_STATUSES and result.order_id:
            self._active[result.order_id] = ActiveOrder(
                order_id=result.order_id, symbol=symbol, side=side, quantity=quantity,
                price=price, remaining=result.remaining_qty,
                idempotency_key=key, status=result.status or "OPEN")
        log(f"{symbol} {side} {quantity}@{price} ACKNOWLEDGED "
            f"id={result.order_id} status={result.status} filled={result.filled_qty}")

    def _apply_fill(self, symbol: str, side: str, quantity: int) -> None:
        delta = quantity if side == "buy" else -quantity
        self._filled[symbol] = self._filled.get(symbol, 0) + delta
        self._positions_at = None  # force a fresh portfolio read

    def _group_allows(self, symbol: str, quantity: int, price: int) -> bool:
        """Correlated-group cap (BUG-008). Caller holds the lock.

        Per-instrument caps can all be satisfied while the book is one
        directional bet: NOVA and AXON move together, and AXON's follower
        strategy is long-only, so it stacks on NOVA rather than hedging it.
        The new slice is valued at its own limit price — that is what we
        would actually pay — and the existing position at its live mark.
        """
        group = self._risk.group_for(symbol)
        if group is None:
            return True
        members = self._risk.group_symbols(group)
        current = self.projected_notional(members)
        added = quantity * price
        if self._risk.allows_group_buy(group, current, added):
            return True
        log(f"{symbol}: intent refused, correlated group {group!r} "
            f"({'+'.join(members)}) would reach {current + added} cents "
            f"against a cap of {self._risk.max_group_notional(group)}")
        return False

    def _affordable(self, quantity: int, price: int) -> bool:
        """Cheap pre-check against a 402 that would still spend an order slot.

        `reserved_cash` is already committed to our resting buys, so only the
        remainder is spendable.
        """
        if self._positions_at is None:
            return True  # no figures yet — let the exchange decide
        return self._cash - self._reserved_cash >= quantity * price

    def _next_key(self, symbol: str, side: str) -> str:
        """One key per intent, scoped to the session.

        Keys are scoped to a trading window, so the same string in the next
        window places a new order rather than replaying this one. A reprice is
        a new intent and gets a new key; only a timed-out submit reuses one,
        and then only to look itself up.
        """
        self._seq += 1
        return f"{self._session_id or 'nosession'}:{symbol}:{side}:{self._seq}"

    # --- rate-limit bookkeeping ------------------------------------------

    def _observe_orders(self, result) -> None:
        if self._order_limiter:
            self._order_limiter.observe(result.rate_remaining, result.retry_after)

    def _observe_shared(self, result) -> None:
        if self._shared_limiter:
            self._shared_limiter.observe(result.rate_remaining, result.retry_after)
