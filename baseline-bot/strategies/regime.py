"""Price-only market-regime classification.

The exchange does not expose candles, so this module works with an ordered
sequence of price samples accumulated from SSE ``price`` events (or closes from
locally-built fixed-time bars).  It deliberately classifies market state only;
strategy routing belongs to a separate coordinator.

ATR-like volatility measures the average absolute sample-to-sample move.
Directional efficiency distinguishes a clean trend from equally volatile
back-and-forth noise.  Shock detection compares the newest move with the ATR
that existed *before* that move, so the shock does not dilute its own score.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable


class Regime(str, Enum):
    """Actionable price regimes emitted by the classifier."""

    WARMUP = "warmup"
    COMPRESSION = "compression"
    RANGE = "range"
    NEUTRAL = "neutral"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    SHOCK_UP = "shock_up"
    SHOCK_DOWN = "shock_down"


@dataclass(frozen=True)
class RegimeConfig:
    """Thresholds for price-only regime classification.

    Defaults are starting hypotheses from the strategy optimisation playbook.
    They should be tuned on captured sandbox windows, preferably using
    fixed-time price bars rather than irregular raw ticks.
    """

    atr_lookback: int = 14
    trend_lookback: int = 15
    low_volatility_pct: float = 0.0015
    trend_efficiency_min: float = 0.60
    range_efficiency_max: float = 0.30
    trend_move_atr_min: float = 2.0
    shock_atr_multiple: float = 4.0

    def __post_init__(self) -> None:
        if self.atr_lookback < 2:
            raise ValueError("atr_lookback must be at least 2")
        if self.trend_lookback < 2:
            raise ValueError("trend_lookback must be at least 2")
        if self.low_volatility_pct < 0:
            raise ValueError("low_volatility_pct cannot be negative")
        if not 0 <= self.range_efficiency_max <= 1:
            raise ValueError("range_efficiency_max must be between 0 and 1")
        if not 0 <= self.trend_efficiency_min <= 1:
            raise ValueError("trend_efficiency_min must be between 0 and 1")
        if self.range_efficiency_max >= self.trend_efficiency_min:
            raise ValueError(
                "range_efficiency_max must be below trend_efficiency_min"
            )
        if self.trend_move_atr_min <= 0:
            raise ValueError("trend_move_atr_min must be positive")
        if self.shock_atr_multiple <= 0:
            raise ValueError("shock_atr_multiple must be positive")


@dataclass(frozen=True)
class RegimeResult:
    """Regime plus the measurements that explain the classification."""

    regime: Regime
    previous_regime: Regime | None
    changed: bool
    sample_count: int
    atr: float | None
    atr_pct: float | None
    efficiency_ratio: float | None
    return_pct: float | None
    move_atr: float | None
    shock_score: float | None

    @property
    def transition(self) -> str | None:
        """Return a log-friendly ``old->new`` label when a change occurred."""

        if not self.changed or self.previous_regime is None:
            return None
        return f"{self.previous_regime.value}->{self.regime.value}"


def _prices(values: Iterable[int | float]) -> list[float]:
    prices = [float(value) for value in values]
    if any(not isfinite(price) or price <= 0 for price in prices):
        raise ValueError("prices must be finite positive numbers")
    return prices


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _result(
    regime: Regime,
    previous_regime: Regime | None,
    sample_count: int,
    *,
    atr: float | None = None,
    atr_pct: float | None = None,
    efficiency_ratio: float | None = None,
    return_pct: float | None = None,
    move_atr: float | None = None,
    shock_score: float | None = None,
) -> RegimeResult:
    return RegimeResult(
        regime=regime,
        previous_regime=previous_regime,
        changed=previous_regime is not None and regime != previous_regime,
        sample_count=sample_count,
        atr=atr,
        atr_pct=atr_pct,
        efficiency_ratio=efficiency_ratio,
        return_pct=return_pct,
        move_atr=move_atr,
        shock_score=shock_score,
    )


def classify_regime(
    values: Iterable[int | float],
    previous_regime: Regime | None = None,
    config: RegimeConfig | None = None,
) -> RegimeResult:
    """Classify an ordered price history and describe any regime change.

    Classification precedence is shock, trend, compression, range, neutral.
    A shock is evaluated first because risk controls should react immediately.
    Trend requires both directional efficiency and a minimum net move in ATR
    units; ATR by itself never implies a trend.
    """

    cfg = config or RegimeConfig()
    prices = _prices(values)
    minimum_samples = max(cfg.trend_lookback, cfg.atr_lookback + 2)
    if len(prices) < minimum_samples:
        return _result(Regime.WARMUP, previous_regime, len(prices))

    moves = [abs(current - prior) for prior, current in zip(prices, prices[1:])]

    # Report current ATR using the latest moves.  For shock detection, exclude
    # the newest move so an extreme event cannot inflate its own baseline.
    atr = _mean(moves[-cfg.atr_lookback :])
    prior_atr = _mean(moves[-(cfg.atr_lookback + 1) : -1])
    atr_pct = atr / prices[-1]

    trend_prices = prices[-cfg.trend_lookback :]
    trend_moves = [
        abs(current - prior)
        for prior, current in zip(trend_prices, trend_prices[1:])
    ]
    net_move = trend_prices[-1] - trend_prices[0]
    path_length = sum(trend_moves)
    efficiency = abs(net_move) / path_length if path_length else 0.0
    return_pct = net_move / trend_prices[0]

    if atr > 0:
        move_atr = abs(net_move) / atr
    else:
        move_atr = 0.0 if net_move == 0 else float("inf")

    newest_move = prices[-1] - prices[-2]
    if prior_atr > 0:
        shock_score = abs(newest_move) / prior_atr
    else:
        shock_score = 0.0 if newest_move == 0 else float("inf")

    measurements = {
        "atr": atr,
        "atr_pct": atr_pct,
        "efficiency_ratio": efficiency,
        "return_pct": return_pct,
        "move_atr": move_atr,
        "shock_score": shock_score,
    }

    if shock_score >= cfg.shock_atr_multiple:
        regime = Regime.SHOCK_UP if newest_move > 0 else Regime.SHOCK_DOWN
    elif (
        efficiency >= cfg.trend_efficiency_min
        and move_atr >= cfg.trend_move_atr_min
    ):
        regime = Regime.TREND_UP if net_move > 0 else Regime.TREND_DOWN
    elif atr_pct <= cfg.low_volatility_pct:
        regime = Regime.COMPRESSION
    elif efficiency <= cfg.range_efficiency_max:
        regime = Regime.RANGE
    else:
        regime = Regime.NEUTRAL

    return _result(
        regime,
        previous_regime,
        len(prices),
        **measurements,
    )


class RegimeClassifier:
    """Stateful convenience wrapper for repeated live classifications.

    The caller owns the price history.  This object stores only the last emitted
    regime so it can report transitions.  Call ``reset`` at a session boundary.
    Hysteresis/confirmation belongs in the future routing layer, not this first
    price classifier.
    """

    def __init__(self, config: RegimeConfig | None = None):
        self.config = config or RegimeConfig()
        self.previous_regime: Regime | None = None

    def classify(self, values: Iterable[int | float]) -> RegimeResult:
        result = classify_regime(values, self.previous_regime, self.config)
        self.previous_regime = result.regime
        return result

    def reset(self) -> None:
        self.previous_regime = None
