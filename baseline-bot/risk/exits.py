"""Exit path shared by the crash guard and the stop-loss: sell a whole
position into the resting bids.

The exchange is limit-only, so "sell at whatever the buyers are already
bidding" means one sell order priced at the bid level that absorbs the whole
position. Matching is price-time priority, so that single order fills against
every bid at or above its price, best first. A floor (`floor_pct` under the
trigger price) stops a thin book from pulling the sweep price to nothing; the
remainder rests at the floor and is repriced once after `reprice_after`
seconds if it is still there.

Prices are integer cents throughout.
"""
import time

from utils.logger import log


def sweep_price(bids, quantity: int, floor: int) -> int:
    """Lowest bid level needed to absorb `quantity`, clamped to `floor`.

    `bids` is the exchange's book shape: one entry per resting order, highest
    price first. Malformed entries are skipped. An empty or too-thin book
    returns the lowest usable level, or the floor when there is none.
    """
    remaining = int(quantity)
    level = None
    for entry in bids or []:
        try:
            price = int(entry["price"])
            qty = int(entry["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0 or qty <= 0:
            continue
        level = price
        remaining -= qty
        if remaining <= 0:
            break
    if level is None:
        return int(floor)
    return max(level, int(floor))


class Flattener:
    """Read the book, price the sweep, flatten through OrderManager; chase once."""

    def __init__(self, client, orders, floor_pct=0.03, reprice_after=5.0,
                 clock=time.monotonic, label="exit"):
        self._client = client
        self._orders = orders
        self.floor_pct = float(floor_pct)
        self._reprice_after = float(reprice_after)
        self._clock = clock
        self._label = label
        self._dumps = {}   # symbol -> {"at", "order_id", "status", "repriced"}

    def floor_for(self, trigger_price: int) -> int:
        return int(trigger_price * (1.0 - self.floor_pct))

    def flatten(self, symbol: str, price: int, repriced: bool = False):
        floor = self.floor_for(int(price))
        try:
            book = self._client.get_orderbook(symbol) or {}
            bids = book.get("bids") or []
        except Exception as exc:
            log(f"{self._label}: {symbol} book read failed ({exc}); selling at floor {floor}")
            bids = []

        def pricer(quantity: int) -> int:
            return sweep_price(bids, quantity, floor)

        result = self._orders.flatten(symbol, pricer)
        order_id = getattr(result, "order_id", None) if result is not None else None
        status = getattr(result, "status", None) if result is not None else "NOT_SENT"
        log(f"{self._label}: {symbol} flatten {'reprice ' if repriced else ''}-> {status}")
        self._dumps[symbol] = {"at": self._clock(), "order_id": order_id,
                               "status": status, "repriced": repriced}
        return result

    def on_price(self, symbol: str, price: int) -> None:
        """Chase a resting exit once, `reprice_after` seconds after it was sent."""
        dump = self._dumps.get(symbol)
        if dump and not dump["repriced"] and dump["order_id"] \
                and self._clock() - dump["at"] >= self._reprice_after \
                and self._orders.has_active(symbol):
            log(f"{self._label}: {symbol} exit order still resting — repricing once")
            self.flatten(symbol, int(price), repriced=True)

    def status(self) -> dict:
        return {s: dict(d) for s, d in self._dumps.items()}


class StopLossResponder:
    """Feed prices to the risk model; flatten when it confirms a stop."""

    def __init__(self, model, flattener: Flattener):
        self._model = model
        self._flattener = flattener

    def on_price(self, symbol: str, price):
        trigger = self._model.on_price(symbol, price)
        if trigger is not None:
            self._flattener.flatten(symbol, int(price))
        else:
            self._flattener.on_price(symbol, int(price))
        return trigger

    def status(self) -> dict:
        return self._flattener.status()
