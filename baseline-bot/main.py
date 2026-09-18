"""Baseline trading bot entry point.

Discovers instruments, wires momentum (primary) and mean-reversion (secondary)
to the SSE price feed, gates on session state, and submits limit orders through
the OrderManager. Single process, stdout logging.

Threads:
    SSE reader  -> bounded EventWorker queue -> trading worker (on_session/on_price)
    ControlServer   GET /status, GET /orders?since=, POST /halt  (127.0.0.1:8092)
    Heartbeat       every ~5s: liveness snapshot + feed-stall watchdog
    main            waits for the shutdown event, then exits 0/1
"""
import os
import signal
import sys
import threading

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from exchange.ratelimit import RollingWindow
from orders.manager import OrderManager
from risk.crash_guard import CrashGuard, CrashResponder
from risk.limits import RiskLimits
from strategies.dca import DcaOverlay
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from utils.control import ControlServer
from utils.event_worker import EventWorker
from utils.heartbeat import Heartbeat
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
    validate_config(config)
    return config


def validate_config(config):
    """Reject a config that would trade wrongly rather than fail loudly.

    Raises ValueError naming the first bad field. Thresholds must be positive
    fractions, sizes and lookbacks positive integers, caps non-negative; the
    control port and heartbeat timings must be sane. Nothing here reads the
    network.
    """
    def positive_int(section, key):
        v = section.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise ValueError(f"{key} must be a positive integer, got {v!r}")

    def non_negative_int(section, key):
        v = section.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError(f"{key} must be a non-negative integer, got {v!r}")

    def fraction(section, key):
        v = section.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v < 1:
            raise ValueError(f"{key} must be a fraction between 0 and 1, got {v!r}")

    active = config.get("instruments", {}).get("active", {})
    primary, secondary = active.get("primary"), active.get("secondary")
    if not primary or not secondary or primary == secondary:
        raise ValueError("instruments.active needs distinct primary and secondary symbols")

    strategies = config.get("strategy", {})
    for symbol in (primary, secondary):
        strat = strategies.get(symbol)
        if not strat:
            raise ValueError(f"no strategy configured for {symbol}")
        positive_int(strat, "lookback_samples")
        positive_int(strat, "trade_quantity")
        non_negative_int(strat, "limit_offset_bps")
        if strat.get("type") == "momentum":
            fraction(strat, "momentum_threshold")
        elif strat.get("type") == "mean_reversion":
            fraction(strat, "deviation_threshold")
        else:
            raise ValueError(f"unknown strategy type for {symbol}: {strat.get('type')!r}")

    risk = config.get("risk", {})
    non_negative_int(risk, "max_position_primary")
    non_negative_int(risk, "max_position_secondary")
    for key in ("max_active_orders_per_symbol", "max_orders_per_minute"):
        if key in risk:
            positive_int(risk, key)
    if "anomaly_pause_seconds" in risk and not risk["anomaly_pause_seconds"] > 0:
        raise ValueError("anomaly_pause_seconds must be positive")

    control = config.get("control", {})
    port = control.get("port", 8092)
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError(f"control.port must be 0..65535, got {port!r}")

    hb = config.get("heartbeat", {})
    interval = hb.get("interval_seconds", 5)
    degraded = hb.get("feed_degraded_after_seconds", 10)
    stall = hb.get("feed_stall_after_seconds", 20)
    if not (0 < interval and 0 < degraded < stall):
        raise ValueError("heartbeat: need 0 < interval and 0 < degraded < stall")
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


def halt(orders, lifecycle, shutdown_event, feed=None, worker=None):
    """Emergency stop: block new intents, cancel every open order, stop.

    Shared by SIGINT/SIGTERM and the dashboard kill switch (POST /halt).
    Ordering matters: intents are blocked atomically inside OrderManager
    *before* any cancel is sent, so the trading worker cannot slip a fresh
    order into a book we are clearing. Cancels go through
    OrderManager.cancel_all(), which enumerates from the exchange as well as
    local state, treats already-filled / already-cancelled as settled, and
    reports what it could not confirm. Returns that unresolved count; the
    process exits non-zero when it is non-zero.
    """
    if lifecycle["value"] == STOPPED:
        return 0

    lifecycle["value"] = QUIESCING
    log("lifecycle: QUIESCING — blocking new intents")
    orders.block_new_intents()

    try:
        cancelled, unresolved = orders.cancel_all()
    except Exception as exc:  # cancel_all degrades, but never trust the halt path
        log(f"lifecycle: cancel_all failed: {exc}")
        cancelled, unresolved = [], ["<cancel_all failed>"]

    if feed is not None:
        try:
            feed.stop()
        except Exception as exc:
            log(f"lifecycle: feed stop failed: {exc}")
    if worker is not None:
        try:
            worker.stop()
        except Exception as exc:
            log(f"lifecycle: worker stop failed: {exc}")

    lifecycle["value"] = STOPPED
    lifecycle["unresolved"] = len(unresolved)
    shutdown_event.set()

    if unresolved:
        log(f"lifecycle: STOPPED — cancelled {len(cancelled)}, "
            f"{len(unresolved)} order(s) unresolved: {unresolved}")
    else:
        log(f"lifecycle: STOPPED — cancelled {len(cancelled)}, all orders resolved")
    return len(unresolved)


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
        max_position_default=config["risk"].get("max_position_default", 0),
    )

    limits = config.get("rate_limits", {})
    window = float(limits.get("window_seconds", 30))
    risk_cfg = config["risk"]
    orders = OrderManager(
        client, risk,
        max_active_per_symbol=risk_cfg.get("max_active_orders_per_symbol", 1),
        order_limiter=RollingWindow(limits.get("orders_per_window", 100), window),
        shared_limiter=RollingWindow(limits.get("shared_per_window", 300), window,
                                     reserve=limits.get("shared_reserve", 40)),
        anomaly_limiter=RollingWindow(risk_cfg.get("max_orders_per_minute", 20), 60.0),
        anomaly_pause_seconds=risk_cfg.get("anomaly_pause_seconds", 60),
    )

    # Report starting positions from one portfolio read.
    portfolio = client.get_portfolio()
    for sym in strategies:
        log(f"{sym} starting position: {client.position_detail(sym, portfolio)['quantity']}")

    # --- crash guard (all discovered instruments) ---------------------------
    cg = config.get("crash_guard") or {}
    crash = None
    if cg.get("enabled", True):
        symbols = [i.get("symbol") for i in instruments if i.get("symbol")]
        guard = CrashGuard(
            symbols,
            drop_pct=cg.get("drop_pct", 0.15),
            confirm_ticks=cg.get("confirm_ticks", 3),
            floor_pct=cg.get("floor_pct", 0.03),
            cooldown_seconds=cg.get("cooldown_seconds", 300),
        )
        crash = CrashResponder(guard, client, orders,
                               reprice_after=cg.get("reprice_after_seconds", 5))
        log(f"crash guard armed on {symbols}: drop {guard.drop_pct:.0%} over "
            f"{guard.confirm_ticks} ticks, floor {guard.floor_pct:.0%}, "
            f"cooldown {guard.cooldown_seconds:.0f}s")
    else:
        log("crash guard DISABLED by config")

    # --- DCA overlay (all discovered instruments) ---------------------------
    dca_cfg = config.get("dca") or {}
    dca = None
    if dca_cfg.get("enabled", False):
        dca_symbols = [i.get("symbol") for i in instruments if i.get("symbol")]
        dca = DcaOverlay(
            dca_symbols, orders,
            cash_per_buy_cents=dca_cfg.get("cash_per_buy_cents", 500_000),
            interval_seconds=dca_cfg.get("interval_seconds", 60),
            retry_seconds=dca_cfg.get("retry_seconds", 5),
            limit_offset_bps=dca_cfg.get("limit_offset_bps", 5),
            allow=(crash.allows_strategy if crash is not None else None),
        )
        log(f"dca armed on {dca_symbols}: {dca_cfg.get('cash_per_buy_cents', 500_000)} "
            f"cents every {dca_cfg.get('interval_seconds', 60)}s")

    # --- shared state -------------------------------------------------------
    state = {
        "session_open": False,
        "session_id": None,
    }
    lifecycle = {"value": RUNNING, "unresolved": 0}
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
    orders.reset_for_session(state["session_id"])

    # --- event handlers -----------------------------------------------------

    def on_session(payload):
        new_status = payload.get("status")

        # SSE session event uses open/halted/closed; map to session_open bool.
        state["session_open"] = new_status == "open"
        log(f"session: {new_status}")

        # P0-13: a new trading window is a clean slate on the exchange (cash,
        # positions, orders and idempotency keys all restart), so every piece
        # of per-window state here restarts with it: strategy price history
        # and the OrderManager's resting/unknown orders, positions and keys.
        new_id = payload.get("id")
        if new_id and new_id != state["session_id"]:
            log(f"session boundary: {state['session_id']} -> {new_id} — resetting "
                f"strategies and order state")
            for strat in strategies.values():
                strat.prices.clear()
            state["session_id"] = new_id
            orders.reset_for_session(new_id)
            if crash is not None:
                crash.guard.reset_for_session()
            if dca is not None:
                dca.reset_for_session()

    def on_price(payload):
        if lifecycle["value"] != RUNNING:
            return

        symbol = payload.get("symbol")
        last_price = payload.get("last_price")
        strat = strategies.get(symbol)
        if strat is not None:
            strat.on_price(last_price)
        if not state["session_open"]:
            return
        # Crash guard runs first and on every instrument, strategy or not:
        # a 15% collapse dumps the position before any strategy gets a say.
        if crash is not None:
            crash.on_price(symbol, last_price)
            if not crash.allows_strategy(symbol):
                return
        # DCA runs on every instrument, strategy or not, and never sells.
        if dca is not None:
            dca.on_price(symbol, last_price)
        if strat is None:
            return
        # Runs on the trading worker thread, never on the SSE reader: REST
        # calls here cannot block feed ingestion.
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
        # Refusals, rejections and unknowns are all logged and journalled by
        # OrderManager. Nothing is reported as submitted unless the exchange
        # acknowledged it.
        orders.submit_intent(symbol, signal.side, signal.quantity, signal.limit_price)

    # --- feed, worker, heartbeat ---------------------------------------------
    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    worker = EventWorker({"session": on_session, "price": on_price})
    hb_cfg = config.get("heartbeat", {})
    heartbeat = Heartbeat(
        feed, worker=worker, lifecycle=lifecycle, orders=orders,
        interval=hb_cfg.get("interval_seconds", 5),
        degraded_after=hb_cfg.get("feed_degraded_after_seconds", 10),
        stall_after=hb_cfg.get("feed_stall_after_seconds", 20),
    )

    def do_halt():
        return halt(orders, lifecycle, shutdown_event, feed=feed, worker=worker)

    # --- signal handlers (P0-11, P0-14) -------------------------------------

    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log(f"received {sig_name} — initiating shutdown")
        do_halt()

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # --- control endpoint (kill switch + health, P0-14) ---------------------
    # The dashboard's HALT button POSTs /halt here through its local proxy; its
    # bot pill and order lifecycle table read /status and /orders?since=.
    control_cfg = config.get("control", {})

    def control_status():
        snapshot = {
            "state": lifecycle["value"],
            "session_id": state["session_id"],
            "session_open": state["session_open"],
            "unresolved": lifecycle.get("unresolved", 0),
            "orders": orders.status(),
            "crash_guard": crash.status() if crash is not None else {"enabled": False},
            "dca": dca.status() if dca is not None else {"enabled": False},
        }
        snapshot.update(heartbeat.snapshot())
        return snapshot

    control = ControlServer(
        control_status,
        do_halt,
        host=control_cfg.get("host", "127.0.0.1"),
        port=int(os.environ.get("BOT_CONTROL_PORT") or control_cfg.get("port", 8092)),
        events_fn=orders.journal_page,
    )
    control.start()

    # --- start ------------------------------------------------------------
    worker.start()
    feed.on("session", lambda payload: worker.submit("session", payload))
    feed.on("price", lambda payload: worker.submit("price", payload))
    feed.start()
    heartbeat.start()
    log("bot started")

    # Wait for the shutdown signal (Ctrl-C, SIGTERM or the dashboard kill
    # switch). A bare `while True: sleep` here would never let halt() end the
    # process. Polling keeps the main thread responsive to signals on every OS.
    while not shutdown_event.wait(1):
        pass
    heartbeat.stop()
    feed.join(5)
    worker.join(5)
    control.stop()

    exit_code = 0 if lifecycle["value"] == STOPPED and not lifecycle.get("unresolved") else 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
