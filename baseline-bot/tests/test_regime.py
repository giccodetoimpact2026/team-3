"""Tests for the price-only regime classifier."""

from collections import deque

import pytest

from strategies.regime import (
    Regime,
    RegimeClassifier,
    RegimeConfig,
    classify_regime,
)


def test_warmup_before_atr_and_trend_windows_are_ready():
    result = classify_regime([10_000] * 10)

    assert result.regime is Regime.WARMUP
    assert result.atr is None
    assert result.changed is False


def test_smooth_rise_is_an_uptrend_not_merely_high_volatility():
    prices = [10_000 + 100 * index for index in range(16)]

    result = classify_regime(prices)

    assert result.regime is Regime.TREND_UP
    assert result.efficiency_ratio == pytest.approx(1.0)
    assert result.atr == pytest.approx(100.0)
    assert result.move_atr >= 2.0


def test_smooth_fall_is_a_downtrend():
    prices = [12_000 - 100 * index for index in range(16)]

    result = classify_regime(prices)

    assert result.regime is Regime.TREND_DOWN
    assert result.return_pct < 0


def test_choppy_high_volatility_path_is_a_range_not_a_trend():
    prices = [10_000, 10_200, 9_800, 10_200, 9_800, 10_200, 9_800, 10_200,
              9_800, 10_200, 9_800, 10_200, 9_800, 10_200, 9_800, 10_000]

    result = classify_regime(prices)

    assert result.regime is Regime.RANGE
    assert result.atr_pct > RegimeConfig().low_volatility_pct
    assert result.efficiency_ratio <= RegimeConfig().range_efficiency_max


def test_flat_low_volatility_path_is_compression():
    prices = [10_000 + index % 2 for index in range(16)]

    result = classify_regime(prices)

    assert result.regime is Regime.COMPRESSION
    assert result.atr_pct <= RegimeConfig().low_volatility_pct


def test_newest_move_is_compared_with_pre_shock_atr():
    prices = [10_000 + (10 if index % 2 else 0) for index in range(15)]
    prices.append(10_500)

    result = classify_regime(prices)

    assert result.regime is Regime.SHOCK_UP
    assert result.shock_score >= RegimeConfig().shock_atr_multiple


def test_stateful_classifier_reports_transition_and_can_reset():
    classifier = RegimeClassifier()
    rising = [10_000 + 100 * index for index in range(16)]
    ranging = [10_000, 10_200, 9_800, 10_200, 9_800, 10_200, 9_800, 10_200,
               9_800, 10_200, 9_800, 10_200, 9_800, 10_200, 9_800, 10_000]

    first = classifier.classify(rising)
    second = classifier.classify(deque(ranging))

    assert first.changed is False
    assert second.changed is True
    assert second.previous_regime is Regime.TREND_UP
    assert second.transition == "trend_up->range"

    classifier.reset()
    after_reset = classifier.classify(ranging)
    assert after_reset.changed is False
    assert after_reset.previous_regime is None


@pytest.mark.parametrize("prices", [[10_000, 0], [10_000, -1], [10_000, float("nan")]])
def test_invalid_prices_are_rejected(prices):
    with pytest.raises(ValueError, match="finite positive"):
        classify_regime(prices)


def test_invalid_config_rejects_overlapping_efficiency_thresholds():
    with pytest.raises(ValueError, match="below"):
        RegimeConfig(range_efficiency_max=0.7, trend_efficiency_min=0.6)
