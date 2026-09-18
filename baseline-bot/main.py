"""Baseline trading bot entry point.

Discovers instruments, wires momentum (primary) and mean-reversion (secondary)
to the SSE price feed, gates on session state, and submits limit orders through
the REST client. Single process, stdout logging.
"""
import os
import time
import uuid

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from risk.limits import RiskLimits
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from utils.event_worker import EventWorker
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
        position = client.get_positions(symbol)
        signal = strat.signal(position)
        if signal is None:
            return
        if signal.side == "buy" and not risk.allows_buy(symbol, position, signal.quantity):
            return
        client.submit_limit_order(
            symbol, signal.side, signal.quantity, signal.limit_price, str(uuid.uuid4()))
        log(f"submitted {signal} for {symbol}")

    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    worker = EventWorker({"session": on_session, "price": on_price})
    worker.start()
    feed.on("session", lambda payload: worker.submit("session", payload))
    feed.on("price", lambda payload: worker.submit("price", payload))
    feed.start()
    log("bot started")

    # Keep the process alive. NOTE: there is no graceful shutdown — a Ctrl-C or
    # kill signal exits without cancelling resting orders, which stay on the
    # exchange. A SIGTERM handler that cancels open orders was never finished.
    try:
        while True:
            time.sleep(1)
    except Exception:
        pass


if __name__ == "__main__":
    main()
