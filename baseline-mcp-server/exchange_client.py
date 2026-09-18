"""Thin REST wrapper around the exchange, authenticated with X-API-Key.

Read-only: the MCP server never places orders. Every response is the exchange's
{status, data} envelope; this unwraps to the data payload. Prices/money are
integer cents on the wire (the tools convert to dollars for display).
"""
import requests


class ExchangeClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def _get(self, path: str):
        r = self.session.get(f"{self.base_url}{path}", timeout=5)
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else body

    def get_portfolio(self) -> dict:
        return self._get("/portfolio")

    def get_orders(self) -> list:
        return self._get("/orders")

    def get_instruments(self) -> list:
        return self._get("/instruments")
