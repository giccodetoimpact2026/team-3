"""Baseline trading bot entry point.

Discovers instruments, wires momentum (primary) and mean-reversion (secondary)
to the SSE price feed, gates on session state, and submits limit orders through
the REST client. Single process, stdout logging.
"""
import os
import time

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from exchange.ratelimit import RollingWindow
from orders.manager import OrderManager
from risk.limits import RiskLimits
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from utils.logger import log


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def build_strategy(symbol, strat_cfg):
    if strat_cfg["type"] == "momentum":
        return MomentumStrategy(symbol, strat_cfg)
    if strat_cfg["type"] == "mean_reversion":
        return MeanReversionStrategy(symbol, strat_cfg)
    raise ValueError(f"unknown strategy type: {strat_cfg['type']}")


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

    session_open = {"value": False}

    def on_session(payload):
        session_open["value"] = payload.get("status") == "open"
        log(f"session: {payload.get('status')}")

    def on_price(payload):
        symbol = payload.get("symbol")
        strat = strategies.get(symbol)
        if strat is None:
            return
        strat.on_price(payload["last_price"])
        if not session_open["value"]:
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

    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    feed.on("session", on_session)
    feed.on("price", on_price)
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


if __name__ == "__main__":
    main()
