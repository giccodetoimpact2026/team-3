"""Per-instrument stop-loss and take-profit.

Priya Sundaram's headline: "this system ran eighteen months with no stop-losses
and I only found out last month." Per-instrument, and the levels have to be
readable so the dashboard can show them rather than burying them in a config
file — hence `levels()` and `trigger_prices()`.

The exchange is LONG-ONLY, so every exit here is a sell that reduces or closes a
held long. There is no protective stop on a short because there are no shorts.

Cost basis comes from `GET /portfolio` -> positions[].avg_cost: the exchange's
own volume-weighted figure, in integer cents, 0 when flat. Reading it from the
exchange rather than tracking entries locally means the levels survive a bot
restart and reset by themselves at a session boundary, because the portfolio is
scoped to the trading window.

**Exits override the strategy, they do not compete with it.** A stop the
strategy can veto is not a stop — momentum riding a loser would simply never
produce the sell. The trading path checks exits first and skips the strategy
signal entirely when one fires.

Three legs, and which ones suit an instrument depends on its strategy:

* `stop_loss_pct` — every instrument. Non-negotiable risk control.

* `trailing_take_profit` — the right take-profit for a MOMENTUM name. A fixed
  target caps exactly the upside momentum exists to capture; a trailing stop
  lets the winner run and only exits once it gives back `trail_pct` from its
  high-water mark, after arming at `arm_pct` above cost. Momentum's own exit
  fires on the window return turning negative, which on a long window can mean
  handing back most of the move first — this closes that gap.

* `take_profit_pct` — a fixed target, for a name that spikes and reverts rather
  than trending. DRFT is the case: "it spikes on volume then drops." Waiting for
  a trail to trigger on a thin book means selling into the drop.

  Deliberately NOT set for ELXR: mean-reversion already sells when price runs
  above its mean, so that strategy's own exit *is* the take-profit. A second
  fixed level would only fight it at a different threshold.
"""


class ExitSignal:
    """A forced reduce/close. Mirrors strategies.base.Signal, always a sell."""

    STOP_LOSS = "stop_loss"
    TRAILING_TAKE_PROFIT = "trailing_take_profit"
    TAKE_PROFIT = "take_profit"

    def __init__(self, reason: str, symbol: str, quantity: int, limit_price: int,
                 avg_cost: int, mark: int, peak: int | None = None):
        self.side = "sell"
        self.reason = reason
        self.symbol = symbol
        self.quantity = quantity
        self.limit_price = limit_price
        self.avg_cost = avg_cost
        self.mark = mark
        self.peak = peak          # high-water mark, trailing exits only

    @property
    def move_pct(self) -> float:
        """Signed move against cost basis. Negative when stopped out."""
        if self.avg_cost <= 0:
            return 0.0
        return (self.mark - self.avg_cost) / self.avg_cost

    def __repr__(self):
        tail = "" if self.peak is None else f", peak {self.peak}"
        return (f"ExitSignal({self.reason} {self.symbol} {self.quantity} "
                f"@ {self.limit_price}, {self.move_pct:+.2%} vs cost "
                f"{self.avg_cost}{tail})")


def _fraction(symbol, name, value, upper=None):
    """Validate a positive fractional threshold, normalising sign."""
    if value is None:
        return None
    value = abs(float(value))
    if value <= 0 or (upper is not None and value >= upper):
        bound = "" if upper is None else f" below {upper}"
        raise ValueError(f"{symbol}: {name} must be a positive fraction{bound}, "
                         f"got {value}")
    return value


class ExitPolicy:
    """Stop-loss and take-profit levels, keyed by symbol.

    Thresholds are fractions of the cost basis (0.03 = 3%). Any leg may be
    omitted per instrument; a symbol absent from the config has no exits at all,
    deliberately, so nothing guesses at a risk tolerance nobody set.

    Holds the trailing high-water mark per symbol, which is per-window state:
    call `reset()` at a session boundary.
    """

    def __init__(self, per_symbol: dict, limit_offset_bps: int = 25,
                 orderbook=None):
        self._levels = {}
        for symbol, cfg in (per_symbol or {}).items():
            cfg = cfg or {}
            trail_cfg = cfg.get("trailing_take_profit") or {}
            level = {
                "stop_loss_pct": _fraction(
                    symbol, "stop_loss_pct", cfg.get("stop_loss_pct"), upper=1),
                "take_profit_pct": _fraction(
                    symbol, "take_profit_pct", cfg.get("take_profit_pct")),
                "trail_arm_pct": _fraction(
                    symbol, "trailing_take_profit.arm_pct", trail_cfg.get("arm_pct")),
                "trail_pct": _fraction(
                    symbol, "trailing_take_profit.trail_pct",
                    trail_cfg.get("trail_pct"), upper=1),
            }
            if bool(level["trail_arm_pct"]) != bool(level["trail_pct"]):
                raise ValueError(
                    f"{symbol}: trailing_take_profit needs both arm_pct and "
                    f"trail_pct, or neither")
            if (level["take_profit_pct"] is not None
                    and level["trail_pct"] is not None):
                raise ValueError(
                    f"{symbol}: set take_profit_pct OR trailing_take_profit, not "
                    f"both — the fixed target would pre-empt the trail")
            if any(v is not None for v in level.values()):
                self._levels[symbol] = level

        self.limit_offset_bps = int(limit_offset_bps)
        self.orderbook = orderbook
        self._peak: dict[str, int] = {}

    # --- introspection (for the dashboard / MCP) --------------------------

    def levels(self, symbol: str | None = None):
        """Configured levels, for display. Risk parameters you cannot see
        "might as well not exist" (Priya)."""
        if symbol is None:
            return {s: dict(v) for s, v in self._levels.items()}
        return dict(self._levels.get(symbol, {}))

    def peak(self, symbol: str):
        """Current trailing high-water mark in cents, or None if unarmed."""
        return self._peak.get(symbol)

    def trigger_prices(self, symbol: str, avg_cost: int) -> dict:
        """Absolute cent prices at which each configured leg fires.

        More useful on a screen than a percentage: directly comparable to the
        live mark. The trailing level moves with the high-water mark, so it is
        only defined once the position has actually run.
        """
        level = self._levels.get(symbol) or {}
        out = {"stop_loss": None, "take_profit": None, "trailing_stop": None}
        if not level or avg_cost <= 0:
            return out
        if level["stop_loss_pct"] is not None:
            out["stop_loss"] = int(avg_cost * (1 - level["stop_loss_pct"]))
        if level["take_profit_pct"] is not None:
            out["take_profit"] = int(avg_cost * (1 + level["take_profit_pct"]))
        peak = self._peak.get(symbol)
        if level["trail_pct"] is not None and peak is not None:
            armed_at = avg_cost * (1 + level["trail_arm_pct"])
            if peak >= armed_at:
                out["trailing_stop"] = int(peak * (1 - level["trail_pct"]))
        return out

    # --- state ------------------------------------------------------------

    def reset(self, symbol: str | None = None):
        """Forget high-water marks — all of them, or one symbol's.

        Per-window state: the portfolio (and so the cost basis these levels are
        measured against) starts fresh each trading window.
        """
        if symbol is None:
            self._peak.clear()
        else:
            self._peak.pop(symbol, None)

    # --- evaluation -------------------------------------------------------

    def check(self, symbol: str, avg_cost: int, mark: int, sellable: int):
        """Return an ExitSignal if a leg has been breached, else None.

        `sellable` is the held quantity net of shares already reserved by our
        own resting sells, so an exit never oversells into a 400
        INSUFFICIENT_POSITION. `mark` should be the freshest price available —
        the live SSE print, not the portfolio's cached mark.

        A missing cost basis (`avg_cost <= 0`) means the exchange does not think
        we hold this symbol, so there is nothing to protect and any high-water
        mark is stale.
        """
        if sellable <= 0 or avg_cost <= 0 or mark <= 0:
            # Flat: drop the high-water mark so the next position starts clean
            # rather than inheriting the last one's peak.
            self._peak.pop(symbol, None)
            return None

        level = self._levels.get(symbol)
        if not level:
            return None

        peak = max(self._peak.get(symbol, 0), mark)
        self._peak[symbol] = peak

        move = (mark - avg_cost) / avg_cost

        # Stop first: if one print breaches both a stop and a profit leg, the
        # loss side is the one that matters.
        if level["stop_loss_pct"] is not None and move <= -level["stop_loss_pct"]:
            return self._exit(ExitSignal.STOP_LOSS, symbol, sellable, avg_cost, mark)

        if level["trail_pct"] is not None:
            armed = peak >= avg_cost * (1 + level["trail_arm_pct"])
            given_back = (mark - peak) / peak if peak > 0 else 0.0
            if armed and given_back <= -level["trail_pct"]:
                return self._exit(ExitSignal.TRAILING_TAKE_PROFIT, symbol, sellable,
                                  avg_cost, mark, peak=peak)

        if level["take_profit_pct"] is not None and move >= level["take_profit_pct"]:
            return self._exit(ExitSignal.TAKE_PROFIT, symbol, sellable, avg_cost, mark)

        return None

    def _exit(self, reason, symbol, sellable, avg_cost, mark, peak=None):
        offset = mark * self.limit_offset_bps // 10_000
        fallback = max(1, mark - offset)

        price = fallback
        if self.orderbook is not None:
            bid = self.orderbook.best_bid(symbol)
            if bid is not None and bid >= fallback:
                price = bid

        self._peak.pop(symbol, None)
        return ExitSignal(reason, symbol, sellable, price,
                          avg_cost, mark, peak=peak)
