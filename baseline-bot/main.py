"""Baseline trading bot entry point.

Discovers instruments, wires momentum (primary) and mean-reversion (secondary)
to the SSE price feed, gates on session state, and submits limit orders through
the REST client. Single process, stdout logging.
"""
import importlib
import os
import signal
import sys
import threading
import time
import uuid

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from exchange.orderbook import OrderBookCache
from exchange.ratelimit import RollingWindow
from orders.manager import OrderManager
from risk.exits import ExitPolicy
from risk.limits import RiskLimits
from strategies.base import Signal
from strategies.crash_buy import CrashBuyStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from news.engine import NewsEngine
from news.sentiment import SentimentAnalyzer
from strategies.pairs import PairsFollowStrategy
from strategies.regime_policy import RegimePolicy, StaticRegimeClassifier
from utils.control import ControlServer
from utils.event_worker import EventWorker
from utils.logger import log


def load_env_file():
    """Read KEY=VALUE lines from .env next to this file into os.environ.

    Dependency-free (no python-dotenv). Existing environment variables win, so
    an explicitly exported EXCHANGE_API_KEY still overrides the file.
    """
    path = os.path.join(os.path.dirname(__file__), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def load_config():
    load_env_file()
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)

    exchange = config.setdefault("exchange", {})
    exchange["base_url"] = os.environ.get(
        "EXCHANGE_BASE_URL", exchange.get("base_url", "")
    ).rstrip("/")
    exchange["api_key"] = os.environ.get(
        "EXCHANGE_API_KEY", ""
    )
    if not exchange["base_url"]:
        raise ValueError("exchange base URL is required")
    if not exchange["api_key"] or exchange["api_key"] == "REPLACE_WITH_TEAM_API_KEY":
        raise ValueError("set EXCHANGE_API_KEY; never commit the team API key")
    return config


def build_strategy(symbol, strat_cfg, built=None):
    """Construct one strategy.

    `built` supplies the already-constructed strategies, needed only by types
    that read another instrument's price series (pairs_follow reads its leader).
    """
    kind = strat_cfg["type"]
    if kind == "momentum":
        return MomentumStrategy(symbol, strat_cfg)
    if kind == "mean_reversion":
        return MeanReversionStrategy(symbol, strat_cfg)
    if kind == "crash_buy":
        return CrashBuyStrategy(symbol, strat_cfg)
    if kind == "pairs_follow":
        leader_symbol = strat_cfg["leader"]
        leader = (built or {}).get(leader_symbol)
        if leader is None:
            raise ValueError(
                f"{symbol}: leader {leader_symbol!r} is not an active strategy; "
                f"add it to instruments.active and strategy")
        return PairsFollowStrategy(symbol, strat_cfg, leader)
    raise ValueError(f"unknown strategy type: {kind}")


def build_strategies(config):
    """Build every active strategy, resolving followers after their leaders.

    Two passes rather than one: a follower holds a reference to its leader's
    live price buffer, so leaders must exist first regardless of the order
    symbols happen to appear in config.
    """
    active = config["instruments"]["active"]
    wanted = [active["primary"], active["secondary"]]
    # Extras are optional — an instrument listed without a strategy block is
    # simply not traded, which is how a name gets parked without code changes.
    wanted += [s for s in active.get("extra", []) if s in config["strategy"]]

    strategies = {}
    deferred = []
    for symbol in wanted:
        strat_cfg = config["strategy"][symbol]
        if strat_cfg["type"] == "pairs_follow":
            deferred.append(symbol)
        else:
            strategies[symbol] = build_strategy(symbol, strat_cfg)
    for symbol in deferred:
        strategies[symbol] = build_strategy(
            symbol, config["strategy"][symbol], strategies)
    return strategies


def _check_immediate_labels_are_risk_off(regime_cfg, hysteresis):
    """A label adopted without confirmation must never be able to raise risk.

    The hysteresis debounce is asymmetric on purpose: risk-reducing
    transitions skip the confirmation wait. That is only sound for a label
    that forbids entries in EVERY family — otherwise one print could hand a
    family full size, which is the flip-flop the debounce exists to prevent.

    SHOCK is the live example. It was risk-off everywhere until the
    `crash_buy` family arrived, whose entry IS the shock. Rather than trust a
    comment to stay true, this fails at startup.
    """
    labels = hysteresis.get("immediate_labels")
    if not labels:
        return
    policy = RegimePolicy({**regime_cfg, "enabled": True})
    for label in labels:
        for family in policy.table():
            multiplier = policy.multiplier("*", family, label)
            if multiplier:
                raise ValueError(
                    f"regime.hysteresis.immediate_labels includes {label!r}, "
                    f"but family {family!r} sizes it at {multiplier} — a label "
                    f"adopted without confirmation must be 0.0 everywhere, or "
                    f"one print can raise risk. Remove it from "
                    f"immediate_labels, or set {family}.{label} to 0.0.")


def build_regime_classifier(regime_cfg):
    """Resolve the regime classifier named in config.

    The classifier is somebody else's component; this only has to find it. Point
    `regime.classifier` at a dotted path to a zero-argument callable or class
    implementing strategies.regime_policy.RegimeClassifier (observe + classify).

    With the policy enabled and no classifier configured, this raises rather
    than defaulting: an absent classifier would mean every symbol reads WARMUP,
    and WARMUP forbids entries, so the bot would stop trading without saying so.
    """
    path = regime_cfg.get("classifier")
    if not path:
        static = regime_cfg.get("static_regime")
        if static:
            # Escape hatch for a demo or a bisect: pin every symbol to one
            # regime without standing up the real classifier.
            log(f"regime: using StaticRegimeClassifier({static})")
            return StaticRegimeClassifier(static)
        raise ValueError(
            "regime.enabled is true but regime.classifier is not set; set it to "
            "the dotted path of a RegimeClassifier implementation, or set "
            "regime.static_regime to pin one regime, or disable regime")

    module_name, _, attr = path.rpartition(".")
    if not module_name:
        raise ValueError(f"regime.classifier {path!r} must be a dotted path")
    module = importlib.import_module(module_name)
    try:
        factory = getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"regime.classifier {path!r} not found") from exc

    instance = factory()
    for method in ("observe", "classify"):
        if not callable(getattr(instance, method, None)):
            raise ValueError(
                f"regime.classifier {path!r} has no {method}(); it must "
                f"implement strategies.regime_policy.RegimeClassifier")

    # Hysteresis is the classifier's business — debouncing a label is a
    # property of the labeller, not of the policy table — so the settings are
    # handed over if the implementation accepts them. James p.3 asks for it:
    # "once you switch regime, require a few samples before switching back."
    hysteresis = regime_cfg.get("hysteresis")
    if hysteresis is not None:
        _check_immediate_labels_are_risk_off(regime_cfg, hysteresis)
        configure = getattr(instance, "configure_hysteresis", None)
        if callable(configure):
            configure(hysteresis)
            log(f"regime: hysteresis {hysteresis}")
        else:
            log(f"regime: {path} does not support hysteresis; "
                f"regime.hysteresis is being ignored")

    log(f"regime: classifier {path}")
    return instance


def make_price_handler(strategies, orders, state, lifecycle, exits=None,
                       news_engine=None, regime_policy=None, classifier=None):
    """Build the handler for `price` events.

    Module level rather than a closure inside main() so the trading path can be
    exercised without standing up the feed, the worker and the control server.

    Every order goes through OrderManager and only OrderManager. Submitting
    directly from here would bypass the one-active-order-per-symbol policy, the
    position cap measured against pending exposure, the unreconciled-key gate,
    the cash pre-check and the rate limiter.
    """
    def on_price(payload):
        if lifecycle["value"] != RUNNING:
            return

        symbol = payload.get("symbol")
        strat = strategies.get(symbol)
        if strat is None:
            return
        strat.on_price(payload["last_price"])
        # Feed the mark even when the session is closed: correlated-group
        # exposure is measured in notional, and a symbol that has not ticked
        # this window would otherwise be valued at 0.
        orders.record_mark(symbol, payload["last_price"])
        # The classifier keeps its own buffers, so it observes every print —
        # including between windows, so it is not cold at the open.
        if classifier is not None:
            classifier.observe(symbol, int(payload["last_price"]))
        if not state["session_open"]:
            return

        # NOTE(P1): this still runs inside the SSE listener thread. Once the
        # bounded event queue lands, everything below moves to the trading
        # worker — OrderManager is thread-safe and does not care which thread
        # calls it, but the REST calls must not block feed ingestion.
        orders.poll()

        # Held shares net of those reserved by our own resting sells. Both the
        # exit check and the strategy's sell branch size off this, so neither
        # oversells into a 400 INSUFFICIENT_POSITION. The buy-side cap is
        # enforced inside OrderManager against projected exposure.
        sellable = orders.available_to_sell(symbol)

        # Stop-loss / take-profit run BEFORE the strategy and return instead of
        # it. An exit the strategy can veto is not an exit — momentum riding a
        # loser would simply never produce the sell. The mark is the live SSE
        # print rather than the portfolio's ~1s cached one: an exit wants the
        # freshest price available.
        if exits is not None:
            forced = exits.check(symbol, orders.avg_cost(symbol),
                                 int(payload["last_price"]), sellable)
            if forced is not None:
                log(f"exit: {forced}")
                orders.submit_intent(symbol, "sell", forced.quantity,
                                     forced.limit_price)
                return

        signal = strat.signal(sellable)
        last_price = strat.prices[-1] if strat.prices else None

        bias = news_engine.active_bias(symbol) if news_engine else None
        if bias:
            if bias.action == "pause":
                return
            news_qty = max(1, int(strat.trade_quantity * bias.size_multiplier))
            if signal is not None:
                if bias.action == "buy" and signal.side == "sell":
                    log(f"news: suppressing sell for {symbol} (bullish bias)")
                    return
                if bias.action == "sell" and signal.side == "buy":
                    if isinstance(strat, CrashBuyStrategy):
                        log(f"news: bearish bias for {symbol} but crash_buy "
                            f"buys INTO sell-offs — allowing buy")
                    else:
                        log(f"news: suppressing buy for {symbol} (bearish bias)")
                        return
                if bias.action == signal.side:
                    signal.quantity = max(signal.quantity, news_qty)
            elif last_price is not None:
                if bias.action == "buy":
                    offset = last_price * strat.limit_offset_bps // 10_000
                    signal = Signal("buy", news_qty, last_price + offset)
                    log(f"news: generating buy for {symbol} (bullish bias, no strategy signal)")
                elif bias.action == "sell" and sellable > 0:
                    if isinstance(strat, CrashBuyStrategy):
                        log(f"news: skipping generated sell for {symbol} — "
                            f"crash_buy manages its own exit")
                    else:
                        sell_qty = min(news_qty, sellable)
                        offset = last_price * strat.limit_offset_bps // 10_000
                        signal = Signal("sell", sell_qty, last_price - offset)
                        log(f"news: generating sell for {symbol} "
                            "(bearish bias, no strategy signal)")

        if signal is None:
            return

        # Regime runs LAST, as the final gate on how much risk we add. News can
        # enlarge or generate a signal; regime can only trim or veto it, and it
        # never touches a sell — a market-state label must not be able to trap
        # us in a position. James p.3 is the source for the table itself.
        if regime_policy is not None and classifier is not None:
            before = signal.quantity
            signal = regime_policy.apply(symbol, strat, signal, classifier)
            if signal is None:
                log(f"regime: no entry for {symbol} in "
                    f"{regime_policy.regime_for(strat, classifier)}")
                return
            if signal.quantity != before:
                log(f"regime: {symbol} buy trimmed {before} -> "
                    f"{signal.quantity} in "
                    f"{regime_policy.regime_for(strat, classifier)}")

        orders.submit_intent(symbol, signal.side, signal.quantity, signal.limit_price)

    return on_price


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

RUNNING = "RUNNING"
QUIESCING = "QUIESCING"
STOPPED = "STOPPED"


def halt(client, lifecycle, shutdown_event, order_manager=None):
    """Block new order intents and cancel all open orders.

    Reusable by signal handlers and a future kill-switch command.
    When *order_manager* is provided, delegates to its cancel_all() which
    atomically blocks intents, enumerates from both local state and the
    exchange, and handles 504-reconciliation keys.
    """
    if lifecycle["value"] == STOPPED:
        return

    lifecycle["value"] = QUIESCING
    log("lifecycle: QUIESCING — blocking new intents")

    unknown = 0
    if order_manager is not None:
        cancelled, unresolved = order_manager.cancel_all()
        unknown = len(unresolved)
        if cancelled:
            log(f"halt: cancelled {len(cancelled)} order(s)")
        if not cancelled and not unresolved:
            log("no open orders to cancel")
    else:
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
        self.strategies = build_strategies(config)
        self.risk = RiskLimits(
            config["risk"]["max_position_primary"],
            config["risk"]["max_position_secondary"],
            primary, secondary,
            extra_limits=config["risk"].get("max_position", {}),
            correlated_groups=config["risk"].get("correlated_groups", {}),
        )

    def evaluate(self, strategy):
        session = self.client.get_session()
        new_id = session.get("id")
        if new_id and new_id != self.session_id:
            for s in self.strategies.values():
                s.reset()
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
    strategies = build_strategies(config)
    risk = RiskLimits(
        config["risk"]["max_position_primary"],
        config["risk"]["max_position_secondary"],
        primary, secondary,
        extra_limits=config["risk"].get("max_position", {}),
        correlated_groups=config["risk"].get("correlated_groups", {}),
    )

    limits = config.get("rate_limits", {})
    window = float(limits.get("window_seconds", 30))
    anomaly_cfg = limits.get("anomaly_throttle", {})
    anomaly_limiter = RollingWindow(
        int(anomaly_cfg.get("max_orders", 20)),
        float(anomaly_cfg.get("window_seconds", 10)),
    )
    orders = OrderManager(
        client, risk,
        max_active_per_symbol=config["risk"].get("max_active_orders_per_symbol", 1),
        order_limiter=RollingWindow(limits.get("orders_per_window", 100), window),
        shared_limiter=RollingWindow(limits.get("shared_per_window", 300), window,
                                     reserve=limits.get("shared_reserve", 40)),
        submit_cooldown=float(config["risk"].get("order_cooldown_seconds", 3.0)),
        anomaly_limiter=anomaly_limiter,
    )

    ob_cfg = config.get("orderbook", {})
    book_cache = OrderBookCache(
        stale_seconds=float(ob_cfg.get("stale_threshold_seconds", 5)),
    )

    # Our own team name, so depth can exclude our resting orders: we cannot
    # trade against ourselves (409 SELF_MATCH), so counting our own size as
    # available liquidity overstates the book exactly when we are already in it.
    try:
        book_cache.team_name = (client.get_portfolio() or {}).get("team_name")
        log(f"order book: excluding own orders for team {book_cache.team_name!r}")
    except Exception as exc:
        # Depth then counts our own orders, which only ever overstates
        # liquidity — worth a warning, not worth failing to start.
        log(f"order book: could not read team_name ({exc}); depth will include "
            f"our own resting orders")

    # Attach book cache to each strategy for book-aware pricing, the spread
    # filter (how wide) and the liquidity filter (how deep).
    spread_limits = ob_cfg.get("max_spread_bps", {})
    depth_floors = ob_cfg.get("min_depth_shares", {})
    for sym, strat in strategies.items():
        strat.orderbook = book_cache
        if sym in spread_limits:
            strat.max_spread_bps = float(spread_limits[sym])
        if sym in depth_floors:
            strat.min_depth_shares = int(depth_floors[sym])

    exits = ExitPolicy(
        config["risk"].get("exits", {}),
        limit_offset_bps=config["risk"].get("exit_limit_offset_bps", 25),
        orderbook=book_cache,
    )

    # --- news engine -----------------------------------------------------------
    news_cfg = config.get("news", {})
    news_engine = None
    if news_cfg.get("enabled"):
        # Tier 1 (S1): the LLM reasons across the supply chain. It authenticates
        # with the same secret as the exchange — the service has no separate key.
        analyzer = SentimentAnalyzer(
            news_cfg.get("llm", {}),
            api_key=config["exchange"]["api_key"],
            allowed_symbols=strategies.keys(),
        )
        news_engine = NewsEngine(news_cfg, analyzer=analyzer)
        log(f"news engine enabled; llm sentiment {analyzer.stats()}")

    # --- regime policy (James Wee, page 3) -----------------------------------
    # The classifier is a separate component. Until it is wired, the policy is
    # off: a policy with no classifier would treat every symbol as WARMUP and
    # silently stop trading, so this fails loudly instead.
    regime_policy = RegimePolicy(config.get("regime", {}))
    classifier = None
    if regime_policy.enabled:
        classifier = build_regime_classifier(config.get("regime", {}))
        log(f"regime policy enabled: {regime_policy.table()}")

    # Report starting positions and the risk parameters in force.
    for sym, strat in strategies.items():
        log(f"{sym} [{type(strat).__name__}] starting position: "
            f"{client.get_positions(sym)}, cap {risk.max_position(sym)}, "
            f"exits {exits.levels(sym)}")
    for name, group in risk.groups().items():
        log(f"correlated group {name}: {'+'.join(group['symbols'])} capped at "
            f"{group['max_notional_cents']} cents notional")

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

    # --- startup: cancel leftover orders from a previous run ----------------
    cancelled, unresolved = orders.cancel_all()
    if cancelled:
        log(f"startup: cancelled {len(cancelled)} leftover order(s)")
    if unresolved:
        log(f"startup: {len(unresolved)} order(s) unresolved — positions may be locked")
    orders.allow_new_intents()

    if state["session_id"]:
        orders.reset_for_session(state["session_id"])

    # --- event handlers -----------------------------------------------------

    def on_book_update(payload):
        symbol = payload.get("symbol")
        if symbol:
            book_cache.update(symbol, payload.get("bids", []),
                              payload.get("asks", []))

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
                strat.reset()
            exits.reset()
            book_cache.reset()
            # The classifier's price history is per-window too: each window
            # re-anchors the price engine, so a series spanning two would show
            # a jump that happened in neither.
            if classifier is not None and hasattr(classifier, "reset"):
                classifier.reset()
            if news_engine:
                news_engine.reset()
            orders.reset_for_session(new_id)
            state["session_id"] = new_id

    on_price = make_price_handler(strategies, orders, state, lifecycle, exits,
                                  news_engine=news_engine,
                                  regime_policy=regime_policy,
                                  classifier=classifier)

    def on_announcement(payload):
        log(
            f"announcement #{payload.get('announcement_id')} "
            f"[{payload.get('affected_symbol')}]: {payload.get('message')}"
        )
        if lifecycle["value"] != RUNNING or not state["session_open"]:
            return
        if news_engine:
            news_engine.on_announcement(payload)

    # --- signal handlers (P0-11, P0-14) -------------------------------------

    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log(f"received {sig_name} — initiating shutdown")
        halt(client, lifecycle, shutdown_event, order_manager=orders)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # --- control endpoint (kill switch, P0-14) ------------------------------
    # The dashboard's HALT button POSTs /halt here through its local proxy.
    control_cfg = config.get("control", {})

    def control_status():
        return {
            "state": lifecycle["value"],
            "session_id": state["session_id"],
            "session_open": state["session_open"],
        }

    control = ControlServer(
        control_status,
        lambda: halt(client, lifecycle, shutdown_event, order_manager=orders),
        host=control_cfg.get("host", "127.0.0.1"),
        port=int(os.environ.get("BOT_CONTROL_PORT") or control_cfg.get("port", 8092)),
        news_engine=news_engine,
    )
    control.start()

    # --- start feed ---------------------------------------------------------
    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    worker = EventWorker({
        "session": on_session, "price": on_price, "announcement": on_announcement,
        "book_update": on_book_update,
    })
    worker.start()
    feed.on("session", lambda payload: worker.submit("session", payload))
    feed.on("price", lambda payload: worker.submit("price", payload))
    feed.on("announcement", lambda payload: worker.submit("announcement", payload))
    feed.on("book_update", lambda payload: worker.submit("book_update", payload))
    feed.start()
    log("bot started")

    # Wait for the shutdown signal (Ctrl-C, SIGTERM or the dashboard kill
    # switch). A bare `while True: sleep` here would never let halt() end the
    # process. Polling keeps the main thread responsive to signals on every OS.
    while not shutdown_event.wait(1):
        pass
    control.stop()

    exit_code = 0 if lifecycle["value"] == STOPPED else 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
