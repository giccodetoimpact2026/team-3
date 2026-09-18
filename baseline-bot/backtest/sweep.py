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
}

# The signal parameter that pairs with lookback_samples, per strategy type.
_THRESHOLD_KEY = {
    "momentum": "momentum_threshold",
    "mean_reversion": "deviation_threshold",
}


def current_params(strategy_type: str, base_cfg: dict) -> dict:
    """The live config's tunable params for this instrument."""
    tkey = _THRESHOLD_KEY[strategy_type]
    return {
        "lookback_samples": int(base_cfg["lookback_samples"]),
        tkey: float(base_cfg[tkey]),
    }


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
               slippage_bps: int = 0) -> dict:
    """Run every param set for one instrument and return its ranked block."""
    strategy_type = base_cfg["type"]
    current = current_params(strategy_type, base_cfg)

    results = []
    for overrides in param_sets(strategy_type, base_cfg):
        res = replay(symbol, base_cfg, overrides, series, max_position, slippage_bps)
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


def run_all(config: dict, series_by_symbol: dict, slippage_bps: int = 0) -> list[dict]:
    """Run the sweep for every configured instrument that has price data."""
    risk = _build_risk(config)
    strat_cfg = config.get("strategy", {})
    out = []
    for symbol, series in series_by_symbol.items():
        base_cfg = strat_cfg.get(symbol)
        if base_cfg is None or not series:
            continue
        out.append(run_symbol(
            symbol, base_cfg, series, risk.max_position(symbol), slippage_bps
        ))
    return out
