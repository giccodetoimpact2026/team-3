"""Deterministic news-reaction engine.

Classifies exchange announcement events using keyword matching and stores
short-lived directional biases that the price handler consults before
generating strategy signals.  All state is in-memory and resets at session
boundaries.
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

BULLISH_TERMS = frozenset([
    "rally", "surge", "growth", "discovery", "breakthrough", "expansion",
    "upgrade", "easing", "recovery", "boom", "soar", "gains", "optimism",
    "approval", "awarded", "record", "bullish", "positive",
])

BEARISH_TERMS = frozenset([
    "crisis", "recall", "downturn", "collapse", "sanctions", "default",
    "downgrade", "losses", "tighten", "decline", "slump", "crash",
    "warning", "fears", "suspension", "outage", "shutdown", "bearish",
    "negative",
])


def _dispatch_thread(fn):
    """Run `fn` on a throwaway daemon thread.

    Announcements are rare, so a thread per announcement is cheaper in
    complexity than a pool, and a daemon thread cannot delay shutdown.
    """
    threading.Thread(target=fn, daemon=True).start()


class NewsBias:
    """A short-lived directional bias for one symbol.

    `source`, `confidence` and `rationale` exist because Sophia Tan asked for
    provenance on anything the system asserts: "If the assistant gives me a
    number, I need to know where it came from and how confident it is.
    Citations and confidence, always." `source` is "keyword" for the
    deterministic tier and "llm" once the model has refined it.
    """

    __slots__ = ("symbol", "action", "size_multiplier", "announcement_id",
                 "expires_at", "source", "confidence", "rationale")

    def __init__(self, symbol: str, action: str, size_multiplier: float,
                 announcement_id: int, expires_at: float,
                 source: str = "keyword", confidence: float | None = None,
                 rationale: str = ""):
        self.symbol = symbol
        self.action = action
        self.size_multiplier = size_multiplier
        self.announcement_id = announcement_id
        self.expires_at = expires_at
        self.source = source
        self.confidence = confidence
        self.rationale = rationale

    def as_dict(self) -> dict:
        """Serialisable form, for the dashboard to cite."""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "size_multiplier": self.size_multiplier,
            "announcement_id": self.announcement_id,
            "source": self.source,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }

    def __repr__(self):
        conf = "" if self.confidence is None else f" conf={self.confidence:.2f}"
        return (f"NewsBias({self.symbol} {self.action} "
                f"x{self.size_multiplier:.2f} src={self.source}{conf} "
                f"exp={self.expires_at:.0f})")


def _classify(message: str):
    """Return 'buy', 'sell', or None based on keyword presence."""
    lower = message.lower()
    has_bull = any(t in lower for t in BULLISH_TERMS)
    has_bear = any(t in lower for t in BEARISH_TERMS)
    if has_bull and not has_bear:
        return "buy"
    if has_bear and not has_bull:
        return "sell"
    return None


class NewsEngine:
    """Announcement -> directional bias, in two tiers.

    TIER 0, synchronous: the keyword classifier below. An LLM round trip takes
    seconds and the event worker is a single thread, so waiting for it here
    would stall every queued price event behind the announcement. The keyword
    answer is coarse but instant, and the price move starts immediately.

    TIER 1, asynchronous: `SentimentAnalyzer` reasons across the supply chain
    and replaces the provisional bias with per-symbol views that carry a
    confidence and a rationale. It may name instruments the announcement did
    not — Sophia's whole point — so it can install biases on symbols tier 0
    never touched.

    If tier 1 fails or is disabled, tier 0's bias simply stands and expires on
    its own TTL. Nothing waits on the model.
    """

    def __init__(self, config: dict, analyzer=None, dispatch=None):
        self._lock = threading.Lock()
        # Injectable so tests can run the refinement inline instead of on a
        # thread, and so the dispatcher is visible rather than implicit.
        self._analyzer = analyzer
        self._dispatch = dispatch or _dispatch_thread
        self._enabled = config.get("enabled", True)
        self._cooldown_seconds = float(config.get("cooldown_seconds", 60))
        self._ttl_seconds = float(config.get("directive_ttl_seconds", 120))
        self._default_multiplier = float(config.get("default_size_multiplier", 0.5))
        self._multipliers = {
            k: float(v)
            for k, v in config.get("size_multiplier", {}).items()
        }
        self._seen: set[int] = set()
        self._biases: dict[str, NewsBias] = {}
        self._cooldowns: dict[str, float] = {}

    # -- core interface -----------------------------------------------------

    def on_announcement(self, payload: dict) -> "NewsBias | None":
        message = payload.get("message")
        symbol = payload.get("affected_symbol")
        ann_id = payload.get("announcement_id")

        if not message or not symbol or ann_id is None:
            logger.warning("news: malformed announcement payload: %s", payload)
            return None

        with self._lock:
            if not self._enabled:
                return None

            if ann_id in self._seen:
                logger.debug("news: duplicate announcement %s, skipping", ann_id)
                return None
            self._seen.add(ann_id)

            now = time.monotonic()
            cooldown_until = self._cooldowns.get(symbol, 0)
            if now < cooldown_until:
                logger.info("news: %s in cooldown (%.0fs left), skipping ann %s",
                            symbol, cooldown_until - now, ann_id)
                return None

            action = _classify(message)
            if action is None:
                # No provisional bias — but this is exactly the case tier 1
                # exists for. The keyword list cannot label a headline whose
                # wording it has never seen, and refusing to ask the model here
                # would mean the LLM only ever confirmed answers we already had.
                logger.info("news: unclassified announcement %s for %s, "
                            "deferring to sentiment analysis: %s",
                            ann_id, symbol, message[:80])
                bias = None
            else:
                multiplier = self._multipliers.get(symbol,
                                                   self._default_multiplier)
                bias = NewsBias(
                    symbol=symbol,
                    action=action,
                    size_multiplier=multiplier,
                    announcement_id=ann_id,
                    expires_at=now + self._ttl_seconds,
                )
                self._biases[symbol] = bias
                self._cooldowns[symbol] = now + self._cooldown_seconds

                logger.info(
                    "news: %s bias for %s (x%.2f, ttl=%.0fs) from ann %s: %s",
                    action, symbol, multiplier, self._ttl_seconds,
                    ann_id, message[:80])

        # Outside the lock and off this thread: the model call takes seconds and
        # the event worker is single-threaded.
        self._refine(message, symbol, ann_id)
        return bias

    # -- tier 1: LLM refinement --------------------------------------------

    def _refine(self, message, affected_symbol, ann_id):
        """Dispatch the LLM analysis, if one is configured."""
        analyzer = self._analyzer
        if analyzer is None or not getattr(analyzer, "enabled", False):
            return

        def run():
            result = analyzer.analyze(message, affected_symbol, ann_id)
            if result is not None:
                self.apply_sentiment(result)

        self._dispatch(run)

    def apply_sentiment(self, result) -> list:
        """Install per-symbol biases from an LLM result.

        Replaces whatever tier 0 decided, including on symbols the announcement
        never named — a sanctions headline about freight is information about
        NOVA and CRVX too. A neutral view clears any provisional bias rather
        than leaving a keyword guess standing.

        Cooldowns are NOT consulted here: the cooldown exists to stop repeated
        announcements stacking biases, and this is the same announcement being
        described more accurately, not a new one.
        """
        installed = []
        with self._lock:
            if not self._enabled:
                return installed
            now = time.monotonic()
            for view in result.views:
                action = view.action
                if action is None:
                    # Explicitly neutral: drop any provisional bias for this
                    # symbol rather than letting the keyword guess stand.
                    dropped = self._biases.pop(view.symbol, None)
                    if dropped is not None:
                        logger.info("news: %s neutral on review, clearing %s",
                                    view.symbol, dropped)
                    continue
                multiplier = self._multipliers.get(
                    view.symbol, self._default_multiplier)
                bias = NewsBias(
                    symbol=view.symbol,
                    action=action,
                    size_multiplier=multiplier,
                    announcement_id=result.announcement_id,
                    expires_at=now + self._ttl_seconds,
                    source="llm",
                    confidence=view.confidence,
                    rationale=view.rationale,
                )
                self._biases[view.symbol] = bias
                installed.append(bias)
                logger.info("news: llm %s bias for %s (conf %.2f) from ann %s: %s",
                            action, view.symbol, view.confidence,
                            result.announcement_id, view.rationale)
        return installed

    def active_bias(self, symbol: str) -> "NewsBias | None":
        with self._lock:
            bias = self._biases.get(symbol)
            if bias is None:
                return None
            if time.monotonic() >= bias.expires_at:
                del self._biases[symbol]
                return None
            return bias

    def reset(self):
        with self._lock:
            self._seen.clear()
            self._biases.clear()
            self._cooldowns.clear()
            logger.info("news: state reset (session boundary)")
        # Announcements are per-window: "each window runs its own news; nothing
        # from the last one still applies". The call budget is not reset — the
        # credit grant spans the whole event.
        if self._analyzer is not None:
            self._analyzer.reset()

    # -- config read/write for dashboard ------------------------------------

    def get_config(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "cooldown_seconds": self._cooldown_seconds,
                "directive_ttl_seconds": self._ttl_seconds,
                "default_size_multiplier": self._default_multiplier,
                "size_multiplier": dict(self._multipliers),
                "llm": (self._analyzer.stats()
                        if self._analyzer is not None else {"enabled": False}),
            }

    def update_config(self, patch: dict):
        with self._lock:
            if "enabled" in patch:
                self._enabled = bool(patch["enabled"])
            if "cooldown_seconds" in patch:
                self._cooldown_seconds = max(0, float(patch["cooldown_seconds"]))
            if "directive_ttl_seconds" in patch:
                self._ttl_seconds = max(0, float(patch["directive_ttl_seconds"]))
            if "default_size_multiplier" in patch:
                self._default_multiplier = max(0, min(2.0, float(patch["default_size_multiplier"])))
            if "size_multiplier" in patch:
                for k, v in patch["size_multiplier"].items():
                    self._multipliers[k] = max(0, min(2.0, float(v)))
            logger.info("news: config updated: %s", patch)
