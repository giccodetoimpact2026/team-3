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
import importlib
import os
import signal
import sys
import threading

import yaml

from exchange.client import ExchangeClient
from exchange.feed import SSEFeed
from exchange.orderbook import OrderBookCache
from exchange.ratelimit import RollingWindow
from orders.manager import OrderManager
from risk.crash_guard import CrashGuard, CrashResponder
from risk.exits import ExitPolicy
from risk.limits import RiskLimits
from strategies.base import Signal
from strategies.dca import DcaOverlay
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from news.engine import NewsEngine
from news.sentiment import SentimentAnalyzer
from strategies.pairs import PairsFollowStrategy
from strategies.regime_policy import RegimePolicy, StaticRegimeClassifier
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
    log(f"regime: classifier {path}")
    return instance


def make_price_handler(strategies, orders, state, lifecycle, exits=None,
                       news_engine=None, regime_policy=None, classifier=None,
                       crash=None, dca=None, tags=None):
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
        last_price = payload.get("last_price")
        strat = strategies.get(symbol)
        if strat is not None:
            strat.on_price(last_price)
        # Feed the mark even when the session is closed: correlated-group
        # exposure is measured in notional, and a symbol that has not ticked
        # this window would otherwise be valued at 0.
        orders.record_mark(symbol, last_price)
        # The classifier keeps its own buffers, so it observes every print
        # of a traded instrument whether or not the session is open.
        if classifier is not None and strat is not None:
            classifier.observe(symbol, int(last_price))
        if not state["session_open"]:
            return
        # Crash guard runs first and on every discovered instrument, strategy
        # or not: a collapse dumps the position before any strategy gets a say.
        if crash is not None:
            crash.on_price(symbol, last_price)
            if not crash.allows_strategy(symbol):
                return
        # DCA overlay runs on every instrument, strategy or not, and never sells.
        if dca is not None:
            dca.on_price(symbol, last_price)
        if strat is None:
            return
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
                                     forced.limit_price, source="exit")
                return

        signal = strat.signal(sellable)
        # Who is responsible for the order that may follow: the symbol\'s
        # configured strategy type, or "news" when the bias creates the order.
        source = (tags or {}).get(symbol) or type(strat).__name__
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
                    log(f"news: suppressing buy for {symbol} (bearish bias)")
                    return
                if bias.action == signal.side:
                    signal.quantity = max(signal.quantity, news_qty)
            elif last_price is not None:
                if bias.action == "buy":
                    offset = last_price * strat.limit_offset_bps // 10_000
                    signal = Signal("buy", news_qty, last_price + offset)
                    source = "news"
                    log(f"news: generating buy for {symbol} (bullish bias, no strategy signal)")
                elif bias.action == "sell" and sellable > 0:
                    sell_qty = min(news_qty, sellable)
                    offset = last_price * strat.limit_offset_bps // 10_000
                    signal = Signal("sell", sell_qty, last_price - offset)
                    source = "news"
                    log(f"news: generating sell for {symbol} (bearish bias, no strategy signal)")

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

        orders.submit_intent(symbol, signal.side, signal.quantity, signal.limit_price,
                             source=source)

    return on_price


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
    symbols = [i.get("symbol") for i in instruments if i.get("symbol")]
    log(f"instruments: {symbols}")

    primary = config["instruments"]["active"]["primary"]
    secondary = config["instruments"]["active"]["secondary"]
    strategies = build_strategies(config)
    risk = RiskLimits(
        config["risk"]["max_position_primary"],
        config["risk"]["max_position_secondary"],
        primary, secondary,
        max_position_default=config["risk"].get("max_position_default", 0),
        extra_limits=config["risk"].get("max_position", {}),
        correlated_groups=config["risk"].get("correlated_groups", {}),
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

    ob_cfg = config.get("orderbook", {})
    book_cache = OrderBookCache(
        stale_seconds=float(ob_cfg.get("stale_threshold_seconds", 5)),
    )

    # Attach book cache to each strategy for book-aware pricing + spread filter.
    spread_limits = ob_cfg.get("max_spread_bps", {})
    for sym, strat in strategies.items():
        strat.orderbook = book_cache
        if sym in spread_limits:
            strat.max_spread_bps = float(spread_limits[sym])

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

        # P0-13: a new trading window is a clean slate on the exchange (cash,
        # positions, orders and idempotency keys all restart), so every piece
        # of per-window state here restarts with it: strategy price history
        # and the OrderManager's resting/unknown orders, positions and keys.
        new_id = payload.get("id")
        if new_id and new_id != state["session_id"]:
            log(f"session boundary: {state['session_id']} -> {new_id} — resetting "
                f"strategies and order state")
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
            state["session_id"] = new_id
            orders.reset_for_session(new_id)
            if crash is not None:
                crash.guard.reset_for_session()
            if dca is not None:
                dca.reset_for_session()

    on_price = make_price_handler(strategies, orders, state, lifecycle, exits,
                                  news_engine=news_engine,
                                  regime_policy=regime_policy,
                                  classifier=classifier,
                                  crash=crash, dca=dca,
                                  tags={sym: cfg.get("type") for sym, cfg in config["strategy"].items()})

    def on_announcement(payload):
        log(
            f"announcement #{payload.get('announcement_id')} "
            f"[{payload.get('affected_symbol')}]: {payload.get('message')}"
        )
        if lifecycle["value"] != RUNNING or not state["session_open"]:
            return
        if news_engine:
            news_engine.on_announcement(payload)

    # --- feed, worker, heartbeat ---------------------------------------------
    feed = SSEFeed(config["exchange"]["base_url"], config["exchange"]["api_key"])
    worker = EventWorker({
        "session": on_session, "price": on_price, "announcement": on_announcement,
        "book_update": on_book_update,
    })
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
            "limits": risk.snapshot(symbols),
        }
        snapshot.update(heartbeat.snapshot())
        return snapshot

    control = ControlServer(
        control_status,
        do_halt,
        host=control_cfg.get("host", "127.0.0.1"),
        port=int(os.environ.get("BOT_CONTROL_PORT") or control_cfg.get("port", 8092)),
        events_fn=orders.journal_page,
        news_engine=news_engine,
    )
    control.start()

    # --- start ------------------------------------------------------------
    worker.start()
    feed.on("session", lambda payload: worker.submit("session", payload))
    feed.on("price", lambda payload: worker.submit("price", payload))
    feed.on("announcement", lambda payload: worker.submit("announcement", payload))
    feed.on("book_update", lambda payload: worker.submit("book_update", payload))
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
