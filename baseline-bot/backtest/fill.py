"""Fill model for the backtest — the crux of its honesty.

The recording is a price-only series (one `last_price` per tick). There is no
captured order book: no bid/ask, no depth, no queue. The live bot places
*marketable limit* orders (buy at last + offset, sell at last - offset; see
`strategies/base.py._limit_price`). We model those against the recorded prices as
follows:

  Next-tick marketable fill with capped slippage.

  * A signal formed on the tick at price P_t (limit = _limit_price(P_t, side))
    is filled on the *next* tick's price P_next -- never the same tick. The
    decision used only information available at t; the fill uses the first price
    the market actually printed after the decision. Same-tick fill would let the
    sim transact at the very price the signal was derived from (look-ahead).
  * Buy fills iff P_next <= limit_price; sell fills iff P_next >= limit_price.
    Otherwise the market ran past the limit and the order does NOT fill (it is
    treated as cancelled and re-evaluated next tick -- the live bot places
    one-shot marketable orders and re-evaluates each tick).
  * Fill price = min(limit, P_next) for a buy, max(limit, P_next) for a sell:
    you never do better than the market, never worse than your own limit.
  * `slippage_bps` (default 0) shifts the fill against you, so the model's
    optimism is an explicit, tunable knob rather than a hidden assumption.

Limitations (surfaced in the dashboard caveats): all-or-nothing fills (no
partials), no depth/liquidity limit, no queue position. A `trade_quantity` large
enough to move the real book fills instantly here -- this flatters high-turnover
and large-size parameter sets. Treat winners as hypotheses, not truths.

All prices are integer cents.
"""


def limit_from_reference(reference: int, side: str, bps: int) -> int:
    """Mirror strategies.base.BaseStrategy._limit_price exactly."""
    offset = reference * bps // 10_000
    return reference + offset if side == "buy" else reference - offset


def try_fill(side: str, limit_price: int, next_price: int,
             slippage_bps: int = 0) -> int | None:
    """Return the integer-cent fill price, or None if the order does not fill.

    `next_price` is P_next, the first price after the decision tick.
    """
    if side == "buy":
        if next_price > limit_price:
            return None  # market gapped up past our limit -> no fill
        fill = min(limit_price, next_price)
        fill += fill * slippage_bps // 10_000  # slip against the buyer (pay more)
        return fill
    if side == "sell":
        if next_price < limit_price:
            return None  # market gapped down past our limit -> no fill
        fill = max(limit_price, next_price)
        fill -= fill * slippage_bps // 10_000  # slip against the seller (get less)
        return fill
    raise ValueError(f"unknown side: {side!r}")
