"""Position risk limits: per-instrument caps and correlated-group caps.

The per-instrument parameters are named `projected_position` and `available`
rather than `position` on purpose. Checking a cap against the *filled* position
is what let the bot stack an unbounded number of resting buys: the position
stays 0 until something fills, so the cap never binds. Callers must pass
exposure that includes pending orders — OrderManager.projected_position()
computes it.

**Correlated groups** exist because per-instrument caps can all be satisfied
while the book is one directional bet. Clarissa Ng: "NOVA and AXON move
together; if we're long both we've doubled a position." Farrukh Tashkentov
wanted the same thing from the other side: "if everything's falling and we're
long across the board, that's a problem I want to see." AXON's strategy makes
this concrete — it is long-only, so following NOVA's breakout stacks exposure on
NOVA rather than hedging it.

Group caps are denominated in **notional cents**, not shares. A share count
cannot express joint exposure across instruments at different prices: 500 shares
of a $300 name is five times the money of 500 shares of a $60 name. Notional is
what "doubled a position" actually means.
"""


class RiskLimits:
    def __init__(self, max_position_primary: int, max_position_secondary: int,
                 primary_symbol: str, secondary_symbol: str,
                 max_position_default: int = 0,
                 extra_limits: dict | None = None,
                 correlated_groups: dict | None = None):
        self._max = {
            primary_symbol: int(max_position_primary),
            secondary_symbol: int(max_position_secondary),
        }
        # Cap for every other discovered instrument. Zero keeps the old
        # behaviour: no buys outside primary/secondary.
        self._default = int(max_position_default)

        if extra_limits:
            for sym, cap in extra_limits.items():
                self._max[sym] = int(cap)

        # name -> {"symbols": [...], "max_notional_cents": int}
        self._groups = {}
        # symbol -> group name, so a lookup on the trading path is O(1)
        self._group_of = {}
        for name, cfg in (correlated_groups or {}).items():
            symbols = list((cfg or {}).get("symbols") or [])
            cap = (cfg or {}).get("max_notional_cents")
            if len(symbols) < 2:
                raise ValueError(
                    f"correlated group {name!r} needs at least two symbols; a "
                    f"one-symbol group is already covered by max_position")
            if cap is None or int(cap) <= 0:
                raise ValueError(
                    f"correlated group {name!r} needs a positive "
                    f"max_notional_cents")
            for symbol in symbols:
                if symbol in self._group_of:
                    # Overlapping groups would make the binding cap ambiguous.
                    raise ValueError(
                        f"{symbol} is in both {self._group_of[symbol]!r} and "
                        f"{name!r}; a symbol may belong to one group")
                self._group_of[symbol] = name
            self._groups[name] = {"symbols": symbols,
                                  "max_notional_cents": int(cap)}

    # --- per instrument ---------------------------------------------------

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

    # --- correlated groups ------------------------------------------------

    def group_for(self, symbol: str):
        """The group a symbol belongs to, or None if it is uncorrelated."""
        return self._group_of.get(symbol)

    def group_symbols(self, name: str) -> list:
        return list((self._groups.get(name) or {}).get("symbols", []))

    def max_group_notional(self, name: str):
        return (self._groups.get(name) or {}).get("max_notional_cents")

    def groups(self) -> dict:
        """All configured groups, for display and for startup logging."""
        return {n: dict(g) for n, g in self._groups.items()}

    def allows_group_buy(self, group: str, projected_notional: int,
                         added_notional: int) -> bool:
        """True if adding `added_notional` keeps the group within its cap.

        `projected_notional` is the group's current projected exposure in cents
        — every member's projected position marked to its live price, so pending
        orders count. `added_notional` is what this order would add.

        An unknown group never blocks: a symbol with no group has no joint cap,
        which is the uncorrelated default.
        """
        cap = self.max_group_notional(group)
        if cap is None:
            return True
        if added_notional <= 0:
            return False
        return projected_notional + added_notional <= cap
