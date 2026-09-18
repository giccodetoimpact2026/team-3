"""Adapt the price-history classifier to the policy's per-symbol interface.

Two jobs, both at the seam between classification and policy:

1. **Label mapping.** The price classifier emits eight directional labels;
   the policy table has six. TREND_UP/TREND_DOWN collapse to TREND and
   SHOCK_UP/SHOCK_DOWN to SHOCK, which is safe because the strategies are
   long-only and their own signals are directional — a downtrend can only ever
   reach a sell, and regime never gates a sell.

2. **Hysteresis.** James Wee, page 3: "hysteresis so it doesn't flip-flop on
   the boundary — once you switch regime, require a few samples before
   switching back." A raw label computed from a rolling window will oscillate
   whenever the market sits near a threshold, and each oscillation would
   resize or cancel entries. So a new label has to be confirmed on
   `confirm_samples` consecutive observations before it is published.

   **The debounce is asymmetric, and that is the important part.** Delaying a
   transition that would RAISE the size multiplier is purely protective.
   Delaying one that LOWERS it is dangerous: it would keep us trading at full
   size through the opening ticks of a shock. So a label that is risk-off in
   EVERY family is adopted immediately, without confirmation.

   "In every family" is the load-bearing part, and it is enforced rather than
   assumed — `build_regime_classifier` checks each immediate label is 0.0 for
   every family and refuses to start otherwise. SHOCK used to qualify and no
   longer does: the `crash_buy` family trades the shock itself (SHOCK: 1.0), so
   adopting SHOCK on one print would hand that family full size instantly,
   which is exactly the flip-flop this module exists to prevent. WARMUP is the
   only label that is still 0.0 everywhere.
"""

from collections import deque

from strategies.regime import Regime as MarketRegime
from strategies.regime import RegimeClassifier as PriceClassifier
from strategies.regime_policy import Regime, RegimeClassifier

# The eight classifier labels mapped onto the six policy labels.
LABEL_MAP = {
    MarketRegime.WARMUP: Regime.WARMUP,
    MarketRegime.COMPRESSION: Regime.COMPRESSION,
    MarketRegime.RANGE: Regime.RANGE,
    MarketRegime.NEUTRAL: Regime.NEUTRAL,
    MarketRegime.TREND_UP: Regime.TREND,
    MarketRegime.TREND_DOWN: Regime.TREND,
    MarketRegime.SHOCK_UP: Regime.SHOCK,
    MarketRegime.SHOCK_DOWN: Regime.SHOCK,
}

# Adopted without waiting for confirmation. Only labels that forbid entries in
# EVERY family belong here, because adopting one early must never be able to
# raise risk. SHOCK is deliberately absent: `crash_buy` trades the shock.
DEFAULT_IMMEDIATE = (Regime.WARMUP,)


class AtrRegimeClassifier(RegimeClassifier):
    """Bounded per-symbol histories, six policy labels, debounced transitions."""

    def __init__(self, confirm_samples: int = 3, immediate_labels=None):
        self._classifiers = {}
        self._histories = {}
        # The label `classify()` reports — only updated once confirmed.
        self._published: dict[str, str] = {}
        # The undebounced label, kept for diagnostics and logging.
        self._raw: dict[str, str] = {}
        # The label currently accumulating confirmations, and how many.
        self._candidate: dict[str, str] = {}
        self._streak: dict[str, int] = {}
        self.configure_hysteresis({"confirm_samples": confirm_samples,
                                   "immediate_labels": immediate_labels})

    def configure_hysteresis(self, config) -> None:
        """Apply hysteresis settings. Called by main after construction.

        Kept separate from `__init__` so the classifier stays constructible
        with no arguments, which is the contract `build_regime_classifier`
        resolves against.
        """
        config = config or {}
        confirm = config.get("confirm_samples")
        self.confirm_samples = 1 if confirm is None else int(confirm)
        if self.confirm_samples < 1:
            raise ValueError(
                "regime.hysteresis.confirm_samples must be >= 1 "
                "(1 means no debounce)")
        labels = config.get("immediate_labels")
        labels = DEFAULT_IMMEDIATE if labels is None else tuple(labels)
        for label in labels:
            if label not in Regime.ALL:
                raise ValueError(
                    f"regime.hysteresis.immediate_labels: unknown regime "
                    f"{label!r}; expected one of {list(Regime.ALL)}")
        self.immediate_labels = frozenset(labels)

    # --- ingestion --------------------------------------------------------

    def observe(self, symbol: str, price: int) -> None:
        if symbol not in self._classifiers:
            classifier = PriceClassifier()
            self._classifiers[symbol] = classifier
            cfg = classifier.config
            self._histories[symbol] = deque(
                maxlen=max(cfg.trend_lookback, cfg.atr_lookback + 2)
            )
        self._histories[symbol].append(price)
        result = self._classifiers[symbol].classify(self._histories[symbol])
        raw = LABEL_MAP[result.regime]
        self._raw[symbol] = raw
        self._debounce(symbol, raw)

    def _debounce(self, symbol: str, raw: str) -> None:
        published = self._published.get(symbol)

        if published is None:
            # Nothing to flip-flop against yet. The first observation is always
            # WARMUP anyway, since the classifier needs a full window.
            self._publish(symbol, raw)
            return

        if raw == published:
            # Back to where we already are: abandon any part-built candidate,
            # which is exactly the boundary oscillation this exists to absorb.
            self._clear_candidate(symbol)
            return

        if raw in self.immediate_labels:
            # Risk-off: adopt now, do not wait for confirmation.
            self._publish(symbol, raw)
            return

        if self._candidate.get(symbol) == raw:
            self._streak[symbol] += 1
        else:
            self._candidate[symbol] = raw
            self._streak[symbol] = 1

        if self._streak[symbol] >= self.confirm_samples:
            self._publish(symbol, raw)

    def _publish(self, symbol: str, label: str) -> None:
        self._published[symbol] = label
        self._clear_candidate(symbol)

    def _clear_candidate(self, symbol: str) -> None:
        self._candidate.pop(symbol, None)
        self._streak.pop(symbol, None)

    # --- reads ------------------------------------------------------------

    def classify(self, symbol: str) -> str:
        """The confirmed label. WARMUP until a symbol has been observed."""
        return self._published.get(symbol, Regime.WARMUP)

    def raw(self, symbol: str) -> str:
        """The undebounced label, for logging and for the dashboard.

        Showing both makes the delay visible rather than looking like a lag
        bug: "raw RANGE, holding TREND, 2/3 confirmed".
        """
        return self._raw.get(symbol, Regime.WARMUP)

    def pending(self, symbol: str):
        """(candidate, confirmations) for a transition in progress, else None."""
        candidate = self._candidate.get(symbol)
        if candidate is None:
            return None
        return candidate, self._streak.get(symbol, 0)

    # --- session boundary -------------------------------------------------

    def reset(self, symbol: str | None = None) -> None:
        """Drop price history and debounce state at a session boundary.

        Each trading window re-anchors the price engine — every instrument
        opens at its `starting_price`, and the API reference is explicit that a
        series spanning two windows "would draw a jump that happened in
        neither". Carried across a boundary that discontinuity reads as a SHOCK
        (or a spurious TREND) precisely as the new window opens, which is when
        the classifier has the most influence and the least real data.

        Dropping the history returns every symbol to WARMUP, which forbids
        entries until the new window has produced enough samples to classify —
        and then the debounce applies again on the way out, so the first real
        label of a window is confirmed rather than taken on one print.
        """
        stores = (self._classifiers, self._histories, self._published,
                  self._raw, self._candidate, self._streak)
        if symbol is None:
            for store in stores:
                store.clear()
            return
        for store in stores:
            store.pop(symbol, None)
