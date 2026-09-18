"""Position risk limits.

Per-instrument max long only — there is still no portfolio-level cap and no
stop-loss (bonus work).

The parameters are named `projected_position` and `available` rather than
`position` on purpose. Checking a cap against the *filled* position is what let
the bot stack an unbounded number of resting buys: the position stays 0 until
something fills, so the cap never binds. Callers must pass exposure that
includes pending orders — OrderManager.projected_position() computes it.
"""


class RiskLimits:
    def __init__(self, max_position_primary: int, max_position_secondary: int,
                 primary_symbol: str, secondary_symbol: str,
                 max_position_default: int = 0):
        self._max = {
            primary_symbol: int(max_position_primary),
            secondary_symbol: int(max_position_secondary),
        }
        # Cap for every other discovered instrument (the DCA overlay buys all
        # five). Zero keeps the old behaviour: no buys outside primary/secondary.
        self._default = int(max_position_default)

    def max_position(self, symbol: str) -> int:
        return self._max.get(symbol, self._default)

    def snapshot(self, symbols) -> dict:
        """Risk parameters for GET /status, so the dashboard's Risk view can show
        the cap each discovered instrument is held to."""
        return {"max_position": {s: self.max_position(s) for s in symbols}}

    def allows_buy(self, symbol: str, projected_position: int, quantity: int) -> bool:
        """True if buying `quantity` keeps the projected long within the cap.

        `projected_position` must be filled + pending buys - pending sells, not
        the filled position alone.
        """
        if quantity <= 0:
            return False
        return projected_position + quantity <= self.max_position(symbol)

    def allows_sell(self, symbol: str, available: int, quantity: int) -> bool:
        """True if selling `quantity` stays within what is unreserved.

        Long-only: `available` is the held quantity minus shares already locked
        by our own resting sells. Selling more is a 400 INSUFFICIENT_POSITION,
        and never opens a short.
        """
        if quantity <= 0:
            return False
        return quantity <= available
