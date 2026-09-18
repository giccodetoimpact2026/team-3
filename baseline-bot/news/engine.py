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


class NewsBias:
    __slots__ = ("symbol", "action", "size_multiplier", "announcement_id",
                 "expires_at")

    def __init__(self, symbol: str, action: str, size_multiplier: float,
                 announcement_id: int, expires_at: float):
        self.symbol = symbol
        self.action = action
        self.size_multiplier = size_multiplier
        self.announcement_id = announcement_id
        self.expires_at = expires_at

    def __repr__(self):
        return (f"NewsBias({self.symbol} {self.action} "
                f"x{self.size_multiplier:.2f} exp={self.expires_at:.0f})")


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
    def __init__(self, config: dict):
        self._lock = threading.Lock()
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
                logger.info("news: unclassified announcement %s for %s: %s",
                            ann_id, symbol, message[:80])
                return None

            multiplier = self._multipliers.get(symbol, self._default_multiplier)
            bias = NewsBias(
                symbol=symbol,
                action=action,
                size_multiplier=multiplier,
                announcement_id=ann_id,
                expires_at=now + self._ttl_seconds,
            )
            self._biases[symbol] = bias
            self._cooldowns[symbol] = now + self._cooldown_seconds

            logger.info("news: %s bias for %s (x%.2f, ttl=%.0fs) from ann %s: %s",
                        action, symbol, multiplier, self._ttl_seconds,
                        ann_id, message[:80])
            return bias

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

    # -- config read/write for dashboard ------------------------------------

    def get_config(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "cooldown_seconds": self._cooldown_seconds,
                "directive_ttl_seconds": self._ttl_seconds,
                "default_size_multiplier": self._default_multiplier,
                "size_multiplier": dict(self._multipliers),
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
