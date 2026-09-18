"""Replay one (symbol, params) combination and produce metrics + equity curve.

The replay reproduces the live sequence in main.Bot.on_price -- feed the price
into the real strategy, then ask it for a signal given the held position -- and
substitutes a simulated fill and P&L accounting for the live exchange client.
Because the strategy object is built by main.build_strategy, the buy/sell
decisions are provably the same code the live bot runs.

Order lifecycle (see fill.py for the model): a signal on tick i becomes a
pending one-shot order that settles against tick i+1's price, then is discarded
(filled or cancelled). A signal on the final tick therefore never fills -- the
anti-look-ahead guarantee.

All money is integer cents. Cash starts at 0, so total P&L == cash + position *
last_price throughout.
"""
from main import build_strategy

from .fill import try_fill


def make_strategy(symbol: str, base_cfg: dict, overrides: dict, leader=None):
    """Build the real strategy with `overrides` merged over the config block.

    `leader` (a built strategy) is required only by a follower type: it is passed
    through to `build_strategy` so a `pairs_follow` strategy can bind its leader's
    live price buffer, exactly as the live bot's two-pass builder does.
    """
    cfg = {**base_cfg, **overrides}
    built = {leader.symbol: leader} if leader is not None else None
    return build_strategy(symbol, cfg, built)


def _clamp_buy(quantity: int, position: int, max_position: int) -> int:
    """Long-only cap: never let the projected long exceed the instrument cap."""
    room = max_position - position
    return max(0, min(quantity, room))


def replay(symbol: str, base_cfg: dict, overrides: dict,
           series: list, max_position: int, slippage_bps: int = 0,
           leader: dict | None = None) -> dict:
    """Replay `series` (list of (seq, ts, price) ticks) for one param set.

    `leader`, when given, backtests a follower strategy (`pairs_follow`). It is
    {"symbol": str, "cfg": dict, "series": list} describing the leader instrument
    whose price series the follower reads. The leader strategy is fed its own
    ticks *interleaved by seq* ahead of each follower tick, so the follower sees
    exactly the leader-buffer state the live bot would at that moment. Only the
    follower trades; the leader is a read-only price reference.
    """
    leader_strat = None
    leader_series: list = []
    if leader is not None:
        leader_strat = build_strategy(leader["symbol"], leader["cfg"])
        leader_series = leader["series"]
    strat = make_strategy(symbol, base_cfg, overrides, leader_strat)

    position = 0
    avg_cost = 0        # VWAP cost of the held long, cents
    cash = 0            # signed cash flow, cents (starts flat)
    realized = 0        # realized P&L, cents
    trade_count = 0
    sell_count = 0
    wins = 0

    pending = None      # (side, qty, limit_price) awaiting the next tick
    curve = []
    peak_equity = None
    max_drawdown = 0
    last_price = 0

    leader_i = 0
    n_leader = len(leader_series)

    for i, tick in enumerate(series):
        seq = tick[0]
        px = tick[2]
        last_price = px

        # 0. Feed the leader every tick that happened before this follower tick,
        #    so leader_strat.prices holds exactly what the live bot would have
        #    seen at this seq. Global seqs are unique, so `< seq` is exact.
        if leader_strat is not None:
            while leader_i < n_leader and leader_series[leader_i][0] < seq:
                leader_strat.on_price(leader_series[leader_i][2])
                leader_i += 1

        # 1. Settle a pending order from the previous tick against this price.
        if pending is not None:
            side, qty, limit_price = pending
            fill = try_fill(side, limit_price, px, slippage_bps)
            if fill is not None:
                if side == "buy":
                    new_qty = position + qty
                    avg_cost = round((position * avg_cost + qty * fill) / new_qty)
                    position = new_qty
                    cash -= qty * fill
                    trade_count += 1
                else:  # sell
                    pnl = (fill - avg_cost) * qty
                    realized += pnl
                    cash += qty * fill
                    position -= qty
                    if position == 0:
                        avg_cost = 0
                    trade_count += 1
                    sell_count += 1
                    if pnl > 0:
                        wins += 1
            pending = None  # one-shot: filled or cancelled either way

        # 2. Feed the price into the real strategy.
        strat.on_price(px)

        # 3. Mark equity at this tick (after settlement).
        equity = cash + position * px
        curve.append({"t": i, "equity_cents": equity})
        peak_equity = equity if peak_equity is None else max(peak_equity, equity)
        max_drawdown = min(max_drawdown, equity - peak_equity)

        # 4. Generate a signal for the next tick (no next tick on the last one).
        if strat.ready() and i < len(series) - 1:
            sig = strat.signal(position)
            if sig is not None:
                if sig.side == "buy":
                    qty = _clamp_buy(sig.quantity, position, max_position)
                else:
                    qty = min(sig.quantity, position)  # available == position in sim
                if qty > 0:
                    pending = (sig.side, qty, sig.limit_price)

    unrealized = (last_price - avg_cost) * position if position else 0
    total = realized + unrealized
    win_rate = (wins / sell_count) if sell_count else None

    return {
        "params": dict(overrides),
        "metrics": {
            "total_pnl_cents": total,
            "realized_pnl_cents": realized,
            "unrealized_pnl_cents": unrealized,
            "trade_count": trade_count,
            "sell_count": sell_count,
            "win_rate": win_rate,
            "final_position": position,
            "max_drawdown_cents": max_drawdown,
        },
        "equity_curve": curve,
    }
