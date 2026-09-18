"""LLM sentiment analysis of exchange announcements (S1).

Sophia Tan's requirement is specific, and it rules out what the keyword
classifier in news.engine does: "When an announcement fires, I don't want a
keyword match on the named ticker. I want the system to **reason across the
supply chain** — sanctions on freight hit AXON directly but ripple to NOVA and
CRVX for different reasons. That's an LLM job."

So this returns a view per *affected* symbol, not just the one the exchange
named, and every view carries a confidence and a rationale — "If the assistant
gives me a number, I need to know where it came from and how confident it is.
Citations and confidence, always."

Four rules this module holds to:

1. **It returns a view, never an order.** Direction, confidence and rationale.
   No quantities, no prices, no side. Sizing stays in deterministic risk code,
   which is what makes "never as fact" enforceable rather than aspirational.

2. **One call per announcement_id, memoised.** The team grant is ~$25 and a
   top-up carries a scoring penalty. Re-deriving the same announcement on every
   price tick is the only way to burn it; `analyze` refuses a repeat outright.

3. **It fails closed.** A timeout, a 402 INSUFFICIENT_CREDIT, an unparseable
   body or a low-confidence answer all produce no views. The caller keeps
   whatever deterministic bias it already had; nothing trades on a guess.

4. **It never runs on a latency-critical thread.** The call takes seconds. See
   news.engine for the dispatch: the keyword classifier gives an immediate
   provisional bias, and this refines it asynchronously.

Routed through the organizer LLM service, which is required for the feature to
be assessed at all: "Only AI features that call the provided LLM service are
considered for scoring." It speaks the Anthropic Messages API shape, takes the
team's exchange api_key as `X-API-Key`, and rejects streaming with
400 STREAMING_UNSUPPORTED — so this uses a plain non-streaming
`messages.create()`.
"""
import json
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# The relationships the model is asked to reason over, from the opening brief's
# instrument table and James Wee's page 4. Supplying them as fact is the
# difference between reasoning across the supply chain and guessing at tickers.
SUPPLY_CHAIN = """\
NOVA  Nova Energy      Oil & Gas        Trending and event-sensitive.
AXON  Axon Freight     Logistics        Freight margins follow energy costs, so
                                        NOVA leads AXON. Anything that moves
                                        energy prices moves AXON's costs.
CRVX  Curvex Semi      Semiconductors   Growth-like. Sensitive to demand cycles
                                        and to input/logistics costs.
DRFT  Driftware Def.   Defense Systems  Event-driven. Moves on announcements
                                        rather than with the other names.
ELXR  Elixar Labs      Pharma           Defensive and range-bound; the least
                                        correlated of the five."""

SYSTEM_PROMPT = f"""\
You are a trading signal for a simulated exchange with five instruments that \
form a supply-chain web. A single announcement can ripple across several of \
them, some up, some down, for different reasons.

{SUPPLY_CHAIN}

Given an ANNOUNCEMENT, identify every instrument whose price the news should \
move — including instruments the announcement does not name, when the \
supply-chain relationship implies it. Do not simply echo the named ticker.

Reply with ONLY a JSON object, no prose and no code fence:

{{"views": [
  {{"symbol": "NOVA", "direction": "up", "confidence": 0.7,
    "rationale": "one short sentence naming the causal link"}}
]}}

Rules:
- `symbol` must be one of NOVA, AXON, CRVX, DRFT, ELXR.
- `direction` is "up", "down" or "neutral".
- `confidence` is 0.0-1.0 and must reflect genuine uncertainty. A second-order \
ripple deserves a lower number than a direct hit.
- `rationale` must state the mechanism, not restate the headline.
- Omit an instrument entirely rather than inventing a weak link for it.
- Return {{"views": []}} if the announcement implies nothing tradeable."""

VALID_DIRECTIONS = frozenset(("up", "down", "neutral"))
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class SymbolView:
    """One instrument's directional view. A view, not an instruction."""

    __slots__ = ("symbol", "direction", "confidence", "rationale")

    def __init__(self, symbol: str, direction: str, confidence: float,
                 rationale: str):
        self.symbol = symbol
        self.direction = direction
        self.confidence = confidence
        self.rationale = rationale

    @property
    def action(self) -> str | None:
        """The bias action this view implies, or None for neutral."""
        if self.direction == "up":
            return "buy"
        if self.direction == "down":
            return "sell"
        return None

    def __repr__(self):
        return (f"SymbolView({self.symbol} {self.direction} "
                f"conf={self.confidence:.2f})")


class SentimentResult:
    """The model's answer for one announcement, with its provenance.

    `model`, `usage` and `latency_ms` are kept so the dashboard can show where
    a number came from and what it cost, rather than presenting it as fact.
    """

    __slots__ = ("announcement_id", "views", "model", "usage", "latency_ms",
                 "raw")

    def __init__(self, announcement_id, views, model, usage, latency_ms, raw):
        self.announcement_id = announcement_id
        self.views = views
        self.model = model
        self.usage = usage
        self.latency_ms = latency_ms
        self.raw = raw

    def __repr__(self):
        return (f"SentimentResult(#{self.announcement_id} {self.views} "
                f"model={self.model} {self.latency_ms}ms)")


def parse_views(text: str, min_confidence: float = 0.0) -> list:
    """Parse the model's reply into views, discarding anything malformed.

    Deliberately tolerant about the envelope and strict about the contents: a
    stray code fence or a leading sentence must not cost us the signal, but a
    symbol outside the universe or a confidence that is not a number must not
    reach the trading path. Never raises — a parse failure is "no views", which
    is the fail-closed outcome.
    """
    if not text:
        return []
    match = _JSON_BLOCK.search(text)
    if match is None:
        logger.warning("sentiment: no JSON object in reply: %s", text[:200])
        return []
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError) as exc:
        logger.warning("sentiment: unparseable JSON (%s): %s", exc, text[:200])
        return []
    if not isinstance(payload, dict):
        return []

    views = []
    for raw in payload.get("views") or []:
        if not isinstance(raw, dict):
            continue
        symbol = raw.get("symbol")
        direction = raw.get("direction")
        if not isinstance(symbol, str) or direction not in VALID_DIRECTIONS:
            logger.warning("sentiment: discarding view %s", raw)
            continue
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            logger.warning("sentiment: non-numeric confidence in %s", raw)
            continue
        if not 0.0 <= confidence <= 1.0:
            logger.warning("sentiment: confidence out of range in %s", raw)
            continue
        if confidence < min_confidence:
            logger.info("sentiment: %s %s below confidence floor (%.2f < %.2f)",
                        symbol, direction, confidence, min_confidence)
            continue
        rationale = raw.get("rationale")
        views.append(SymbolView(
            symbol=symbol.upper(),
            direction=direction,
            confidence=confidence,
            rationale=str(rationale) if rationale else "",
        ))
    return views


class SentimentAnalyzer:
    """Calls the organizer LLM service and returns parsed views.

    `allowed_symbols` filters the model's answer down to instruments we
    actually trade, so a hallucinated ticker cannot create a bias.
    """

    def __init__(self, config: dict, api_key: str, allowed_symbols=None,
                 client=None):
        self._lock = threading.Lock()
        self.enabled = bool(config.get("enabled", False))
        self.base_url = config.get("base_url", "https://llm.codetoimpact.sg")
        # The service's own reference names sonnet the default and asks for the
        # smallest model that does the job; the reasoning here is a short
        # supply-chain inference at single-digit calls per window, so cost is
        # not the binding constraint and sonnet is the sensible middle.
        self.model = config.get("model", "sonnet")
        self.max_tokens = int(config.get("max_tokens", 1024))
        self.timeout = float(config.get("timeout_seconds", 20))
        self.min_confidence = float(config.get("min_confidence", 0.6))
        # Hard stop on spend. The grant is ~$25 and a top-up is recorded and
        # may carry a scoring penalty, so the budget is enforced here rather
        # than trusted to announcement volume.
        self.max_calls = int(config.get("max_calls_per_session", 60))
        self._allowed = frozenset(allowed_symbols or ())

        self._client = client
        self._calls = 0
        self._failures = 0
        self._results: dict = {}          # announcement_id -> SentimentResult
        self._tokens_in = 0
        self._tokens_out = 0

        if self.enabled and client is None and not api_key:
            raise ValueError(
                "news.llm.enabled is true but no API key is available; the LLM "
                "service authenticates with the same EXCHANGE_API_KEY")
        self._api_key = api_key

    # --- client -----------------------------------------------------------

    def _ensure_client(self):
        """Build the SDK client on first use.

        Imported lazily so the bot still starts, with the deterministic
        classifier alone, when the SDK is not installed.
        """
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
        except ImportError:
            logger.error(
                "sentiment: anthropic SDK not installed; falling back to the "
                "deterministic classifier. pip install -r requirements.txt")
            self.enabled = False
            return None
        # Same secret as the exchange: the service has no separate key.
        self._client = Anthropic(api_key=self._api_key, base_url=self.base_url,
                                 timeout=self.timeout)
        return self._client

    # --- analysis ---------------------------------------------------------

    def analyze(self, message: str, affected_symbol: str,
                announcement_id) -> "SentimentResult | None":
        """Analyse one announcement. Returns None on any failure.

        Safe to call from a background thread; the caller must not call it from
        the SSE reader or the event worker, because it blocks for seconds.
        """
        if not self.enabled or not message:
            return None

        with self._lock:
            if announcement_id in self._results:
                # Memoised: the same announcement is never charged twice.
                return self._results[announcement_id]
            if self._calls >= self.max_calls:
                logger.warning(
                    "sentiment: call budget spent (%d); not analysing #%s",
                    self.max_calls, announcement_id)
                return None
            self._calls += 1

        client = self._ensure_client()
        if client is None:
            return None

        prompt = (f"ANNOUNCEMENT (names {affected_symbol}): {message}"
                  if affected_symbol else f"ANNOUNCEMENT: {message}")
        started = time.monotonic()
        try:
            # Non-streaming: the service rejects stream=true with
            # 400 STREAMING_UNSUPPORTED.
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            # Includes 402 INSUFFICIENT_CREDIT, timeouts and transport errors.
            # Fail closed and say so; the deterministic bias already stands.
            with self._lock:
                self._failures += 1
            logger.warning("sentiment: call failed for #%s (%s): %s",
                           announcement_id, type(exc).__name__, exc)
            return None

        latency_ms = int((time.monotonic() - started) * 1000)
        text = _response_text(response)
        views = parse_views(text, self.min_confidence)
        if self._allowed:
            kept = [v for v in views if v.symbol in self._allowed]
            for view in views:
                if view.symbol not in self._allowed:
                    logger.warning("sentiment: discarding view for untraded "
                                   "symbol %s", view.symbol)
            views = kept

        usage = _usage(response)
        result = SentimentResult(announcement_id, views, self.model, usage,
                                 latency_ms, text)
        with self._lock:
            self._results[announcement_id] = result
            self._tokens_in += usage.get("input_tokens", 0)
            self._tokens_out += usage.get("output_tokens", 0)

        logger.info("sentiment: #%s -> %s (%dms, %s)", announcement_id, views,
                    latency_ms, usage)
        return result

    # --- introspection ----------------------------------------------------

    def stats(self) -> dict:
        """Spend and reliability, for the dashboard and the write-up."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "model": self.model,
                "calls": self._calls,
                "budget": self.max_calls,
                "failures": self._failures,
                "input_tokens": self._tokens_in,
                "output_tokens": self._tokens_out,
                "min_confidence": self.min_confidence,
            }

    def result_for(self, announcement_id) -> "SentimentResult | None":
        """The cached answer, so the dashboard can cite it."""
        with self._lock:
            return self._results.get(announcement_id)

    def reset(self):
        """Clear memoised results at a session boundary.

        Announcements are per-window — "each window runs its own news; nothing
        from the last one still applies" — but the call budget is NOT reset:
        the credit grant spans the whole event.
        """
        with self._lock:
            self._results.clear()


def _response_text(response) -> str:
    """Concatenate the text blocks of a Messages API response."""
    parts = []
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _usage(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }
