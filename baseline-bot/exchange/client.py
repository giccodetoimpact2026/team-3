"""REST client for the exchange.

Prices and money are INTEGER CENTS on the wire (exchange-api-reference.md): a
$42.50 limit is `price: 4250`. `side` is lowercase `"buy"`/`"sell"` — every
order is a limit order; there is no type field — and each order carries a
client-generated `idempotency_key` so a timed-out submit can be recovered:
call `GET /orders` and match on the key you sent, since `/orders/{id}` takes
the exchange's order id only.
"""
import requests

from .results import CancelOutcome, CancelResult, Outcome, SubmitResult


def _data(payload):
    """Unwrap the exchange's {status, data} envelope."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def positions_by_symbol(portfolio) -> dict:
    """Index a /portfolio response's positions by symbol, tolerating gaps.

    `positions[]` holds one entry per instrument held *this window*, so an
    instrument you haven't traded is simply absent — that is the normal case,
    not an error. Entries with a missing or malformed field are read as flat
    rather than raising, because this runs on the trading path.
    """
    out = {}
    for p in (portfolio or {}).get("positions") or []:
        symbol = (p or {}).get("symbol")
        if not symbol:
            continue
        out[symbol] = {
            "quantity": _int(p.get("quantity")),
            # Shares locked by our own resting sells — NOT available to sell again.
            "reserved_qty": _int(p.get("reserved_qty")),
            "avg_cost": _int(p.get("avg_cost")),
            "last_price": _int(p.get("last_price")),
        }
    return out


class ExchangeClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self.session.verify = False

    # --- reads ------------------------------------------------------------
    def get_instruments(self):
        r = self.session.get(f"{self.base_url}/instruments", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def get_orderbook(self, symbol: str):
        r = self.session.get(f"{self.base_url}/orderbook/{symbol}", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def get_portfolio(self):
        r = self.session.get(f"{self.base_url}/portfolio", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def get_positions(self, symbol: str) -> int:
        """Held quantity for a symbol (shares); 0 when the symbol is absent.

        This is the *gross* long, including shares reserved by our own resting
        sells. Anything deciding how much we may sell must subtract
        `reserved_qty` — see position_detail() — or it will oversell into a
        400 INSUFFICIENT_POSITION.
        """
        return self.position_detail(symbol)["quantity"]

    def position_detail(self, symbol: str, portfolio=None) -> dict:
        """Quantity, reserved quantity, cost and mark for one symbol.

        Pass `portfolio` to read several symbols off a single call rather than
        spending a shared-tier slot per symbol.
        """
        if portfolio is None:
            portfolio = self.get_portfolio()
        return positions_by_symbol(portfolio).get(
            symbol, {"quantity": 0, "reserved_qty": 0, "avg_cost": 0, "last_price": 0})
        """Held quantity for a symbol (shares)."""
        portfolio = self.get_portfolio()
        by_symbol = {p["symbol"]: p["quantity"] for p in portfolio.get("positions", [])}
        return by_symbol.get(symbol, 0)

    def get_order(self, order_id: str):
        """Look up one order by the exchange's order id.

        Not by idempotency_key: /orders/{id} takes an order id, the same as
        DELETE. To find an order you only hold a key for, use get_orders()
        and match on its idempotency_key.
        """
        r = self.session.get(f"{self.base_url}/orders/{order_id}", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def get_orders(self):
        r = self.session.get(f"{self.base_url}/orders", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def get_session(self):
        """Current trading-session state, or None before the first window."""
        r = self.session.get(f"{self.base_url}/session", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def health(self):
        r = self.session.get(f"{self.base_url}/health", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    # --- writes -----------------------------------------------------------
    @staticmethod
    def _envelope(response):
        """The response body as a dict, or {} if it isn't JSON at all."""
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def submit_limit_order(self, symbol: str, side: str, quantity: int,
                           price: int, idempotency_key: str) -> SubmitResult:
        """Place a limit order. `price` is integer cents. Never raises.

        Success is keyed on the envelope's `status`, not on an exact 200: the
        create endpoint answers 201. Matching is synchronous, so an order that
        crossed already carries its fills in `data.trades` here — we book them
        immediately instead of waiting for a portfolio read.
        """
        body = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "idempotency_key": idempotency_key,
        }
        try:
            r = self.session.post(f"{self.base_url}/orders", json=body, timeout=5)
        except (requests.Timeout, requests.ConnectionError):
            # The write may or may not have committed. The key is the only
            # handle we have on it: reconcile via GET /orders, never resubmit.
            return SubmitResult(Outcome.UNKNOWN, idempotency_key, code="TIMEOUT")

        remaining = _int(r.headers.get("X-RateLimit-Remaining"), None)
        if r.status_code == 504:
            return SubmitResult(Outcome.UNKNOWN, idempotency_key,
                                code="GATEWAY_TIMEOUT", rate_remaining=remaining)

        payload = self._envelope(r)
        if r.ok and payload.get("status") == "success":
            data = _data(payload) or {}
            order = data.get("order") or {}
            trades = data.get("trades") or []
            return SubmitResult(
                Outcome.ACKNOWLEDGED, idempotency_key,
                order_id=order.get("id"),
                status=order.get("status"),
                remaining_qty=_int(order.get("remaining_qty")),
                filled_qty=sum(_int(t.get("quantity")) for t in trades),
                rate_remaining=remaining,
            )

        code = (payload.get("error") or {}).get("code")
        if r.status_code == 429:
            return SubmitResult(Outcome.RATE_LIMITED, idempotency_key,
                                code=code or "RATE_LIMIT_EXCEEDED",
                                retry_after=_float(r.headers.get("Retry-After"), 1.0),
                                rate_remaining=remaining)
        if r.status_code == 503:
            # MARKET_HALTED / SESSION_CLOSED. Nothing landed, but this is a
            # session gate, not a transient fault: retrying on a backoff would
            # just hammer a halted market. Wait for a session event instead.
            return SubmitResult(Outcome.REJECTED, idempotency_key,
                                code=code or "SESSION_CLOSED", rate_remaining=remaining)
        if r.status_code >= 500:
            return SubmitResult(Outcome.RETRYABLE, idempotency_key,
                                code=code or "INTERNAL_ERROR", rate_remaining=remaining)
        return SubmitResult(Outcome.REJECTED, idempotency_key, code=code or "REJECTED",
                            rate_remaining=remaining)

    def get_session(self):
        r = self.session.get(f"{self.base_url}/session", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    def cancel_order(self, order_id: str) -> CancelResult:
        """Cancel one resting order. Never raises.

        A shutdown races the matching engine, so "already filled" and "already
        cancelled" (400 ORDER_NOT_CANCELLABLE) and "not found" (404) are all
        normal answers meaning the order is not resting — see
        CancelResult.settled. Cancels draw on the *shared* 300/30s read tier,
        not the orders tier.
        """
        try:
            r = self.session.delete(f"{self.base_url}/orders/{order_id}", timeout=5)
        except (requests.Timeout, requests.ConnectionError):
            return CancelResult(CancelOutcome.UNKNOWN, order_id, code="TIMEOUT")

        remaining = _int(r.headers.get("X-RateLimit-Remaining"), None)
        payload = self._envelope(r)
        if r.ok and payload.get("status") == "success":
            return CancelResult(CancelOutcome.CANCELLED, order_id, rate_remaining=remaining)

        code = (payload.get("error") or {}).get("code")
        if code == "ORDER_NOT_CANCELLABLE":
            return CancelResult(CancelOutcome.NOT_CANCELLABLE, order_id, code=code,
                                rate_remaining=remaining)
        if r.status_code == 404 or code == "NOT_FOUND":
            return CancelResult(CancelOutcome.NOT_FOUND, order_id, code=code or "NOT_FOUND",
                                rate_remaining=remaining)
        if r.status_code == 429:
            return CancelResult(CancelOutcome.RATE_LIMITED, order_id,
                                code=code or "RATE_LIMIT_EXCEEDED",
                                retry_after=_float(r.headers.get("Retry-After"), 1.0),
                                rate_remaining=remaining)
        if r.status_code == 504:
            return CancelResult(CancelOutcome.UNKNOWN, order_id, code="GATEWAY_TIMEOUT",
                                rate_remaining=remaining)
        return CancelResult(CancelOutcome.RETRYABLE, order_id, code=code or "ERROR",
                            rate_remaining=remaining)
