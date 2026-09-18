"""REST client for the exchange.

Prices and money are INTEGER CENTS on the wire (exchange-api-reference.md): a
$42.50 limit is `price: 4250`. `side` is lowercase `"buy"`/`"sell"` — every
order is a limit order; there is no type field — and each order carries a
client-generated `idempotency_key` so a timed-out submit can be recovered:
call `GET /orders` and match on the key you sent, since `/orders/{id}` takes
the exchange's order id only.
"""
import requests


def _data(payload):
    """Unwrap the exchange's {status, data} envelope."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


class ExchangeClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

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

    def health(self):
        r = self.session.get(f"{self.base_url}/health", timeout=5)
        r.raise_for_status()
        return _data(r.json())

    # --- writes -----------------------------------------------------------
    def submit_limit_order(self, symbol: str, side: str, quantity: int,
                           price: int, idempotency_key: str):
        """Place a limit order. `price` is integer cents."""
        body = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "idempotency_key": idempotency_key,
        }
        try:
            r = self.session.post(f"{self.base_url}/orders", json=body, timeout=5)
            r.raise_for_status()
            return _data(r.json())
        except Exception as e:
            print(f"order submit failed: {e}")
            return None

    def cancel_order(self, order_id: str):
        r = self.session.delete(f"{self.base_url}/orders/{order_id}", timeout=5)
        r.raise_for_status()
        return _data(r.json())
