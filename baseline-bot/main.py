"""Baseline trading bot entry point.

Discovers instruments, wires momentum (primary) and mean-reversion (secondary)
to the SSE price feed, gates on session state, and submits limit orders through
the REST client. Single process, stdout logging.
"""
import os
import signal
import sys
import threading
import time
import uuid

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from exchange.ratelimit import RollingWindow
from orders.manager import OrderManager
from risk.limits import RiskLimits
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from utils.event_worker import EventWorker
from utils.logger import log


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)

    exchange = config.setdefault("exchange", {})
    exchange["base_url"] = os.environ.get(
        "EXCHANGE_BASE_URL", exchange.get("base_url", "")
    ).rstrip("/")
    exchange["api_key"] = os.environ.get(
        "EXCHANGE_API_KEY", exchange.get("api_key", "")
    )
    if not exchange["base_url"]:
        raise ValueError("exchange base URL is required")
    if not exchange["api_key"] or exchange["api_key"] == "REPLACE_WITH_TEAM_API_KEY":
        raise ValueError("set EXCHANGE_API_KEY; never commit the team API key")
    return config


def build_strategy(symbol, strat_cfg):
    if strat_cfg["type"] == "momentum":
        return MomentumStrategy(symbol, strat_cfg)
    if strat_cfg["type"] == "mean_reversion":
        return MeanReversionStrategy(symbol, strat_cfg)
    raise ValueError(f"unknown strategy type: {strat_cfg['type']}")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

RUNNING = "RUNNING"
QUIESCING = "QUIESCING"
STOPPED = "STOPPED"


def halt(client, lifecycle, shutdown_event):
    """Block new order intents and cancel all open orders.

    Reusable by signal handlers and a future kill-switch command.
    """
    if lifecycle["value"] == STOPPED:
        return

    lifecycle["value"] = QUIESCING
    log("lifecycle: QUIESCING — blocking new intents")

    unknown = 0
    try:
        orders = client.get_orders()
        open_orders = [
            o for o in orders
            if o.get("status") in ("OPEN", "PARTIALLY_FILLED")
        ]
        if not open_orders:
            log("no open orders to cancel")
        for order in open_orders:
            oid = order["id"]
            try:
                client.cancel_order(oid)
                log(f"cancelled order {oid}")
            except Exception as exc:
                log(f"cancel {oid} failed (may already be filled/cancelled): {exc}")
                unknown += 1
    except Exception as exc:
        log(f"failed to fetch orders for cancellation: {exc}")
        unknown += 1

    lifecycle["value"] = STOPPED
    shutdown_event.set()

    if unknown:
        log(f"lifecycle: STOPPED — {unknown} order(s) unresolved")
    else:
        log("lifecycle: STOPPED — all orders resolved")
    return unknown


class Bot:
    def __init__(self, client, config, trade=False):
        self.client = client
        self.config = config
        self.trade = trade
        self.session_id = None
        self.unknown_key = None
        self.stopping = threading.Event()

        primary = config["instruments"]["active"]["primary"]
        secondary = config["instruments"]["active"]["secondary"]
        self.strategies = {
            primary: build_strategy(primary, config["strategy"][primary]),
            secondary: build_strategy(secondary, config["strategy"][secondary]),
        }
        self.risk = RiskLimits(
            config["risk"]["max_position_primary"],
            config["risk"]["max_position_secondary"],
            primary, secondary,
        )

    def evaluate(self, strategy):
        session = self.client.get_session()
        new_id = session.get("id")
        if new_id and new_id != self.session_id:
            for s in self.strategies.values():
                s.prices.clear()
            self.session_id = new_id

        if session.get("mode") == "live":
            return
        if not self.trade:
            return

        orders = self.client.get_orders()

        if self.unknown_key:
            if any(o.get("idempotency_key") == self.unknown_key for o in orders):
                self.unknown_key = None
            else:
                return

        if any(
            o.get("symbol") == strategy.symbol
            and o.get("status") in ("OPEN", "PARTIALLY_FILLED")
            for o in orders
        ):
            return

        position = self.client.get_positions(strategy.symbol)
        signal = strategy.signal(position)
        if signal is None:
            return

        if signal.side == "buy" and not self.risk.allows_buy(
            strategy.symbol, position, signal.quantity
        ):
            return

        key = str(uuid.uuid4())
        try:
            self.client.submit_limit_order(
                strategy.symbol, signal.side, signal.quantity, signal.limit_price, key,
            )
        except Exception:
            self.unknown_key = key
            raise

    def shutdown(self):
        orders = self.client.get_orders()
        for order in orders:
            if order.get("status") in ("OPEN", "PARTIALLY_FILLED"):
                self.client.cancel_order(order["id"])
        time.sleep(1)
        self.client.get_orders()
        self.stopping.set()
        return True

    def on_price(self, payload):
        symbol = payload.get("symbol")
        strategy = self.strategies.get(symbol)
        if strategy is None:
            return
        strategy.on_price(payload["last_price"])
        self.evaluate(strategy)


def main():
    config = load_config()
    client = ExchangeClient(config["exchange"]["base_url"], config["exchange"]["api_key"])

    # Discover instruments — never hardcode the universe.
    instruments = client.get_instruments()
    log(f"instruments: {[i.get('symbol') for i in instruments]}")

    primary = config["instruments"]["active"]["primary"]
    secondary = config["instruments"]["active"]["secondary"]
    strategies = {
        primary: build_strategy(primary, config["strategy"][primary]),
        secondary: build_strategy(secondary, config["strategy"][secondary]),
    }
    risk = RiskLimits(
        config["risk"]["max_position_primary"],
        config["risk"]["max_position_secondary"],
        primary, secondary,
    )

    limits = config.get("rate_limits", {})
    window = float(limits.get("window_seconds", 30))
    orders = OrderManager(
        client, risk,
        max_active_per_symbol=config["risk"].get("max_active_orders_per_symbol", 1),
        order_limiter=RollingWindow(limits.get("orders_per_window", 100), window),
        shared_limiter=RollingWindow(limits.get("shared_per_window", 300), window,
                                     reserve=limits.get("shared_reserve", 40)),
    )

    # Report starting positions.
    for sym in strategies:
        log(f"{sym} starting position: {client.get_positions(sym)}")

    # --- shared state -------------------------------------------------------
    state = {
        "session_open": False,
        "session_id": None,
    }
    lifecycle = {"value": RUNNING}
    shutdown_event = threading.Event()

    # --- P0-12: session bootstrap -------------------------------------------
    try:
        session_data = client.get_session()
        if session_data is not None:
            status = session_data.get("status")
            state["session_id"] = session_data.get("id")
            state["session_open"] = status == "running"
            log(f"session bootstrap: status={status}, id={state['session_id']}")
        else:
            log("session bootstrap: no active session yet")
    except Exception as exc:
        log(f"session bootstrap failed (will rely on SSE): {exc}")

    # --- event handlers -----------------------------------------------------

    def on_session(payload):
        new_status = payload.get("status")

        # SSE session event uses open/halted/closed; map to session_open bool.
        state["session_open"] = new_status == "open"
        log(f"session: {new_status}")

        # P0-13: detect session boundary — clear stale state on ID change.
        new_id = payload.get("id")
        if new_id and new_id != state["session_id"]:
            log(f"session boundary: {state['session_id']} -> {new_id} — resetting strategies")
            for strat in strategies.values():
                strat.prices.clear()
            state["session_id"] = new_id

    def on_price(payload):
        if lifecycle["value"] != RUNNING:
            return

        symbol = payload.get("symbol")
        strat = strategies.get(symbol)
        if strat is None:
            return
        strat.on_price(payload["last_price"])
        if not state["session_open"]:
            return
        # NOTE(P1): this still runs inside the SSE listener thread. Once the
        # bounded event queue lands, everything below moves to the trading
        # worker — OrderManager is thread-safe and does not care which thread
        # calls it, but the REST calls must not block feed ingestion.
        orders.poll()
        # The strategies take one `position` argument, used only to decide
        # whether they may sell and how much. Pass what is actually sellable —
        # held shares minus those already reserved by our own resting sells —
        # rather than the gross long, which oversells into INSUFFICIENT_POSITION.
        # The buy-side cap is enforced inside OrderManager against projected
        # exposure, which includes pending orders.
        signal = strat.signal(orders.available_to_sell(symbol))
        if signal is None:
            return
        # Refusals, rejections and unknowns are all logged by OrderManager.
        # Nothing is reported as submitted unless the exchange acknowledged it.
        orders.submit_intent(symbol, signal.side, signal.quantity, signal.limit_price)
        position = client.get_positions(symbol)
        signal_obj = strat.signal(position)
        if signal_obj is None:
            return
        if signal_obj.side == "buy" and not risk.allows_buy(symbol, position, signal_obj.quantity):
            return
        client.submit_limit_order(
            symbol, signal_obj.side, signal_obj.quantity, signal_obj.limit_price, str(uuid.uuid4()))
        log(f"submitted {signal_obj} for {symbol}")

    # --- signal handlers (P0-11, P0-14) -------------------------------------

    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log(f"received {sig_name} — initiating shutdown")
        halt(client, lifecycle, shutdown_event)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # --- start feed ---------------------------------------------------------
    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    worker = EventWorker({"session": on_session, "price": on_price})
    worker.start()
    feed.on("session", lambda payload: worker.submit("session", payload))
    feed.on("price", lambda payload: worker.submit("price", payload))
    feed.start()
    log("bot started")

    # Keep the process alive. NOTE(P3): there is still no graceful shutdown or
    # session bootstrap here — `session_open` starts False and only moves on an
    # SSE event, so a bot started mid-window never trades. OrderManager now
    # provides what the lifecycle work needs:
    #   orders.block_new_intents()      -- atomic, call before cancelling
    #   orders.cancel_all()             -- (cancelled, unresolved); exit nonzero
    #                                      if `unresolved` is non-empty
    #   orders.reset_for_session(id)    -- drop per-window state at a boundary
    #   orders.last_seen_session_id     -- read from GET /portfolio, no extra call
    try:
        while True:
            time.sleep(1)
    except Exception:
        pass
    # Wait for shutdown signal instead of sleeping in a bare loop.
    shutdown_event.wait()

    exit_code = 0 if lifecycle["value"] == STOPPED else 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
