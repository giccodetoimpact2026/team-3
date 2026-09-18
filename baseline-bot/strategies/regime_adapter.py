"""Adapt the price-history classifier to the policy's per-symbol interface."""

from collections import deque

from strategies.regime import Regime as MarketRegime, RegimeClassifier as PriceClassifier
from strategies.regime_policy import Regime, RegimeClassifier


class AtrRegimeClassifier(RegimeClassifier):
    """Keep bounded, independent histories and expose the six policy labels."""

    def __init__(self):
        self._classifiers = {}
        self._histories = {}
        self._regimes = {}

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
        self._regimes[symbol] = {
            MarketRegime.WARMUP: Regime.WARMUP,
            MarketRegime.COMPRESSION: Regime.COMPRESSION,
            MarketRegime.RANGE: Regime.RANGE,
            MarketRegime.NEUTRAL: Regime.NEUTRAL,
            MarketRegime.TREND_UP: Regime.TREND,
            MarketRegime.TREND_DOWN: Regime.TREND,
            MarketRegime.SHOCK_UP: Regime.SHOCK,
            MarketRegime.SHOCK_DOWN: Regime.SHOCK,
        }[result.regime]

    def classify(self, symbol: str) -> str:
        return self._regimes.get(symbol, Regime.WARMUP)
