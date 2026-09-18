"""Regime-aware strategy modification (James Wee's notes, page 3).

Page 3 is the section "the desk most wants implemented", and its complaint is
specific: "Momentum bleeds money in flat, low-volatility markets — it keeps
trading a trend that isn't there." Adeline Loh said the same from the execution
desk: "When NOVA goes sideways ... momentum bleeds money slowly. The bot should
detect that and switch or cut size."

Page 3 also dictates the shape: "a regime check that runs before strategy
selection and routes to the right strategy instance — not a giant if-else in the
main loop. A clean BaseStrategy + a router/decorator is the right shape." So the
decision lives in a table here, and the trading path makes one call.

Two halves, deliberately separated:

* CLASSIFICATION — which regime a symbol is in — is somebody else's problem.
  This module only defines the seam (`RegimeClassifier`). Hysteresis lives
  there too: page 3 asks for "hysteresis so it doesn't flip-flop on the
  boundary", and debouncing a label is a property of the labeller, not of the
  consumer. Per-instrument thresholds likewise ("the thin name will look more
  volatile per sample even when nothing is happening").

* POLICY — what a regime does to a signal — is here, driven by config.

The policy can only ever REDUCE risk:

* A sell is never touched. Regime must not trap us in a position, exactly as a
  correlated-group cap must not block the sell that unwinds a breach. A label
  that can stop us getting out is a bug, not a risk control.
* A buy is scaled by a multiplier in [0, 1]. Zero means no new entries.

Where it sits in the chain: exits override everything, then the strategy
produces a signal, then a news bias may enlarge or generate one, and regime runs
LAST as the final gate on how much risk we add. News can raise size; regime can
only trim it. That ordering means a SHOCK regime — usually caused by the very
announcement the news engine reacted to — still gets the final word.
"""


class Regime:
    """The six labels the classifier emits."""

    TREND = "TREND"
    RANGE = "RANGE"
    COMPRESSION = "COMPRESSION"
    SHOCK = "SHOCK"
    NEUTRAL = "NEUTRAL"
    WARMUP = "WARMUP"

    ALL = (TREND, RANGE, COMPRESSION, SHOCK, NEUTRAL, WARMUP)


# Strategy class name -> policy family. Keyed on the class rather than on the
# config `type` string so a renamed config key cannot silently fall through to
# the default policy.
FAMILY_BY_CLASS = {
    "MomentumStrategy": "momentum",
    "MeanReversionStrategy": "mean_reversion",
    "PairsFollowStrategy": "pairs_follow",
}


class RegimeClassifier:
    """The seam a classifier implements. Not an ABC, just the contract.

    `observe` is called for every price event, before `classify`, so the
    implementation can keep its own buffers rather than reaching into the
    strategies. `classify` must return one of `Regime.ALL`; anything else is
    treated as WARMUP by the policy, which is the fail-safe label.
    """

    def observe(self, symbol: str, price: int) -> None:
        raise NotImplementedError

    def classify(self, symbol: str) -> str:
        raise NotImplementedError

    def reset(self, symbol: str | None = None) -> None:
        """Drop per-window state at a session boundary. Optional.

        Each trading window re-anchors the price engine, so any history a
        classifier accumulated belongs to the window it was observed in. An
        implementation that holds no state can leave this as a no-op; the
        trading path calls it defensively.
        """


class StaticRegimeClassifier(RegimeClassifier):
    """Reports one fixed regime. For tests and for pinning behaviour."""

    def __init__(self, regime: str = Regime.NEUTRAL, per_symbol: dict | None = None):
        self.regime = regime
        self.per_symbol = dict(per_symbol or {})

    def observe(self, symbol: str, price: int) -> None:
        pass

    def classify(self, symbol: str) -> str:
        return self.per_symbol.get(symbol, self.regime)


# Defaults straight out of page 3, for any family/regime the config omits.
#
#   RANGE       momentum "will bleed" in a flat market -> suspend it; the
#               range is exactly where mean-reversion belongs.
#   TREND       momentum active. Mean-reversion in a trend is the falling-knife
#               case James warns about on page 2 — it reads a downtrend as a run
#               of dips — so it is suspended, not merely trimmed.
#   COMPRESSION low volatility and a narrow range: momentum has no trend to ride
#               and mean-reversion has no deviation worth capturing. Page 3's
#               other suggestion here is market-making, which is still DISABLED,
#               so cut size instead of switching.
#   NEUTRAL     page 3 verbatim: "hold current posture, reduce size".
#   SHOCK       a violent repricing. Mean-reversion is at its most dangerous
#               (the move is information, not noise) and momentum at its most
#               whipsawed. No new entries; exits still work.
#   WARMUP      not enough data to classify. Never trade on an unknown regime.
DEFAULT_POLICY = {
    "momentum": {
        Regime.TREND: 1.0,
        Regime.RANGE: 0.0,
        Regime.COMPRESSION: 0.25,
        Regime.SHOCK: 0.0,
        Regime.NEUTRAL: 0.5,
        Regime.WARMUP: 0.0,
    },
    "mean_reversion": {
        Regime.TREND: 0.0,
        Regime.RANGE: 1.0,
        Regime.COMPRESSION: 0.5,
        Regime.SHOCK: 0.0,
        Regime.NEUTRAL: 0.5,
        Regime.WARMUP: 0.0,
    },
    # A follower trades the LEADER's breakout, so it is the leader's regime that
    # matters — see RegimePolicy.regime_for. The multipliers mirror momentum's,
    # because a breakout signal is a momentum signal wearing another instrument.
    "pairs_follow": {
        Regime.TREND: 1.0,
        Regime.RANGE: 0.0,
        Regime.COMPRESSION: 0.25,
        Regime.SHOCK: 0.0,
        Regime.NEUTRAL: 0.5,
        Regime.WARMUP: 0.0,
    },
}


class RegimePolicy:
    """Maps (strategy family, regime) -> size multiplier for a new entry.

    `per_symbol` overrides the family table for one instrument, for the case
    page 3 flags: parameters "are *per-instrument* — the thin name will look
    more volatile per sample even when nothing is happening".
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))

        self._policy = {family: dict(table)
                        for family, table in DEFAULT_POLICY.items()}
        for family, table in (config.get("policy") or {}).items():
            if family not in self._policy:
                raise ValueError(
                    f"regime.policy: unknown strategy family {family!r}; "
                    f"expected one of {sorted(self._policy)}")
            for regime, size in (table or {}).items():
                self._policy[family][_regime(f"regime.policy.{family}", regime)] = \
                    _multiplier(f"regime.policy.{family}.{regime}", size)

        self._per_symbol = {}
        for symbol, table in (config.get("per_symbol") or {}).items():
            self._per_symbol[symbol] = {
                _regime(f"regime.per_symbol.{symbol}", regime):
                    _multiplier(f"regime.per_symbol.{symbol}.{regime}", size)
                for regime, size in (table or {}).items()
            }

    # --- lookup -----------------------------------------------------------

    @staticmethod
    def family_of(strategy) -> str:
        family = FAMILY_BY_CLASS.get(type(strategy).__name__)
        if family is None:
            raise ValueError(
                f"{type(strategy).__name__} has no regime family; add it to "
                f"strategies.regime_policy.FAMILY_BY_CLASS")
        return family

    @staticmethod
    def regime_for(strategy, classifier) -> str:
        """The regime that governs this strategy.

        A follower reads its LEADER's regime: AXON's entry is triggered by
        NOVA breaking out, so whether that breakout means anything is a question
        about NOVA's market state, not AXON's.
        """
        subject = getattr(strategy, "leader", None) or strategy
        return classifier.classify(subject.symbol)

    def multiplier(self, symbol: str, family: str, regime: str) -> float:
        if regime not in Regime.ALL:
            # An unrecognised label is treated as WARMUP: never trade on a
            # regime we cannot interpret.
            regime = Regime.WARMUP
        override = self._per_symbol.get(symbol)
        if override is not None and regime in override:
            return override[regime]
        return self._policy[family][regime]

    def table(self, family: str | None = None):
        """The policy in force, for startup logging and the dashboard."""
        if family is None:
            return {f: dict(t) for f, t in self._policy.items()}
        return dict(self._policy[family])

    # --- application ------------------------------------------------------

    def apply(self, symbol: str, strategy, signal, classifier):
        """Scale or veto `signal` for the current regime.

        Returns the signal (possibly resized), or None to place nothing. A sell
        is returned untouched: regime never blocks risk reduction.
        """
        if signal is None or not self.enabled:
            return signal
        if signal.side != "buy":
            return signal

        regime = self.regime_for(strategy, classifier)
        size = self.multiplier(symbol, self.family_of(strategy), regime)
        if size >= 1.0:
            return signal

        scaled = int(signal.quantity * size)
        if scaled < 1:
            # Either the regime forbids entries, or the clip rounded away to
            # nothing — a 0-share order is a 400 ORDER_QUANTITY_INVALID.
            return None
        signal.quantity = scaled
        return signal


def _regime(where: str, value) -> str:
    if value not in Regime.ALL:
        raise ValueError(
            f"{where}: unknown regime {value!r}; expected one of "
            f"{list(Regime.ALL)}")
    return value


def _multiplier(where: str, value) -> float:
    value = float(value)
    if not 0 <= value <= 1:
        # Above 1 would let a regime label *increase* risk, which is the one
        # thing this layer must never do.
        raise ValueError(f"{where}: size multiplier must be in [0, 1], got {value}")
    return value
