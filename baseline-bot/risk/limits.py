"""Position risk limits.

The baseline enforces only a per-instrument max long position. There is no
portfolio-level cap and no stop-loss yet — those are intentional gaps (bonus).
"""


class RiskLimits:
    def __init__(self, max_position_primary: int, max_position_secondary: int,
                 primary_symbol: str, secondary_symbol: str):
        self._max = {
            primary_symbol: int(max_position_primary),
            secondary_symbol: int(max_position_secondary),
        }

    def max_position(self, symbol: str) -> int:
        return self._max.get(symbol, 0)

    def allows_buy(self, symbol: str, position: int, quantity: int) -> bool:
        """True if buying `quantity` keeps the long within the per-symbol cap."""
        return position + quantity <= self.max_position(symbol)
