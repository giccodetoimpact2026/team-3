"""Parameter grid, sweep runner, and ranking.

The grid is small, explicit, and centred on the live config value so the
"current vs best" comparison is always meaningful. We vary only the parameters
that change signal *quality* (lookback and the strategy threshold); trade size
and the limit offset are held at the config value so the grid stays interpretable.

Ranking is on total P&L (realized + final unrealized), descending. `is_winner`
additionally requires MIN_TRADES fills so a single lucky fill cannot "win".
Sharpe/return-vol was rejected: one short session gives an unstable variance
estimate -- total P&L is the honest, legible metric, and the caveats cover the rest.
"""
from itertools import product

from risk.limits import RiskLimits

from .engine import replay

MIN_TRADES = 2

GRID = {
    "momentum": {
        "lookback_samples": [5, 10, 15],
        "momentum_threshold": [0.003, 0.005, 0.008],
    },
    "mean_reversion": {
        "lookback_samples": [10, 15, 20],
        "deviation_threshold": [0.002, 0.004, 0.006],
    },
    # pairs_follow reads its leader's breakout, so the tunables are the breakout
    # window, how long a breakout stays actionable, and the exit give-back -- not
    # lookback (own buffer only supplies the limit reference) or a threshold.
    # leader_breakout_samples must be >= 2 and <= the leader's lookback_samples;
    # the strategy raises at startup otherwise, so keep grid values within the
    # tightest active leader lookback (NOVA = 25).
    "pairs_follow": {
        "leader_breakout_samples": [5, 10, 15],
        "breakout_arm_samples": [3, 5, 8],
        "exit_drawdown": [0.002, 0.004, 0.008],
    },
}


def current_params(strategy_type: str, base_cfg: dict) -> dict:
    """The live config's swept params for this instrument.

    Reads exactly the keys this strategy type sweeps (see GRID) and casts each to
    match its grid values, so the result compares equal to the matching override
    set and the "current" row is found in the sweep.
    """
    grid = GRID[strategy_type]
    return {key: type(values[0])(base_cfg[key]) for key, values in grid.items()}


def param_sets(strategy_type: str, base_cfg: dict) -> list[dict]:
    """Cartesian product of the grid, with the live config set guaranteed in."""
    grid = GRID[strategy_type]
    keys = list(grid.keys())
    sets = [dict(zip(keys, combo)) for combo in product(*(grid[k] for k in keys))]
    current = current_params(strategy_type, base_cfg)
    if current not in sets:
        sets.append(current)
    return sets


def _same_params(a: dict, b: dict) -> bool:
    return a == b


def rank(results: list[dict]) -> list[dict]:
    """Sort by total P&L desc, assign rank, flag the winner (MIN_TRADES gated)."""
    ordered = sorted(
        results, key=lambda r: r["metrics"]["total_pnl_cents"], reverse=True
    )
    winner_assigned = False
    for idx, r in enumerate(ordered):
        r["rank"] = idx + 1
        eligible = r["metrics"]["trade_count"] >= MIN_TRADES
        r["is_winner"] = eligible and not winner_assigned
        if r["is_winner"]:
            winner_assigned = True
    return ordered


def _build_risk(config: dict) -> RiskLimits:
    active = config["instruments"]["active"]
    risk = config.get("risk", {})
    return RiskLimits(
        risk.get("max_position_primary", 0),
        risk.get("max_position_secondary", 0),
        active["primary"],
        active["secondary"],
        risk.get("max_position"),
    )


def run_symbol(symbol: str, base_cfg: dict, series: list, max_position: int,
               slippage_bps: int = 0, leader: dict | None = None) -> dict:
    """Run every param set for one instrument and return its ranked block.

    `leader` is forwarded to `engine.replay` for follower types; see there.
    """
    strategy_type = base_cfg["type"]
    current = current_params(strategy_type, base_cfg)

    results = []
    for overrides in param_sets(strategy_type, base_cfg):
        res = replay(symbol, base_cfg, overrides, series, max_position,
                     slippage_bps, leader)
        res["is_current"] = _same_params(overrides, current)
        results.append(res)

    ranked = rank(results)

    winner = next((r for r in ranked if r["is_winner"]), None)
    current_res = next((r for r in ranked if r["is_current"]), None)

    # Keep equity curves only for the winner and the current set (the two the
    # dashboard draws) to keep results.json small.
    keep = {id(r) for r in (winner, current_res) if r is not None}
    for r in ranked:
        if id(r) not in keep:
            r["equity_curve"] = []

    return {
        "symbol": symbol,
        "strategy_type": strategy_type,
        "ticks": len(series),
        "current_params": current,
        "winner_params": winner["params"] if winner else None,
        "results": ranked,
    }


def _leader_for(base_cfg: dict, strat_cfg: dict, series_by_symbol: dict) -> dict | None:
    """Resolve the leader bundle a follower needs, or None if unavailable.

    A follower can only be backtested when its leader is configured and has price
    data in this segment (its buffer is the follower's whole signal). Missing
    either, we return None and the caller skips the instrument rather than crash.
    """
    leader_symbol = base_cfg.get("leader")
    leader_cfg = strat_cfg.get(leader_symbol)
    leader_series = series_by_symbol.get(leader_symbol)
    if leader_cfg is None or not leader_series:
        return None
    return {"symbol": leader_symbol, "cfg": leader_cfg, "series": leader_series}


def run_all(config: dict, series_by_symbol: dict, slippage_bps: int = 0) -> list[dict]:
    """Run the sweep for every configured instrument that has price data."""
    risk = _build_risk(config)
    strat_cfg = config.get("strategy", {})
    out = []
    for symbol, series in series_by_symbol.items():
        base_cfg = strat_cfg.get(symbol)
        if base_cfg is None or not series:
            continue
        if base_cfg["type"] not in GRID:
            # No sweep grid for this strategy type -- skip rather than KeyError.
            continue
        leader = None
        if base_cfg["type"] == "pairs_follow":
            leader = _leader_for(base_cfg, strat_cfg, series_by_symbol)
            if leader is None:
                continue
        out.append(run_symbol(
            symbol, base_cfg, series, risk.max_position(symbol), slippage_bps,
            leader,
        ))
    return out
