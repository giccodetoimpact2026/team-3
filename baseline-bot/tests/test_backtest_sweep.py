"""Unit tests for the parameter grid, ranking, and winner gating."""
from backtest.sweep import MIN_TRADES, param_sets, rank, run_symbol

MOM_CFG = {"type": "momentum", "lookback_samples": 10, "momentum_threshold": 0.008,
           "trade_quantity": 100, "limit_offset_bps": 5}


def _result(total, trades):
    return {"metrics": {"total_pnl_cents": total, "trade_count": trades}}


def test_grid_is_cartesian_product_with_off_grid_current_injected():
    # An off-grid live threshold (0.007) is appended to the 3x3 grid -> 10.
    cfg = {**MOM_CFG, "lookback_samples": 12, "momentum_threshold": 0.007}
    sets = param_sets("momentum", cfg)
    assert len(sets) == 10
    assert {"lookback_samples": 12, "momentum_threshold": 0.007} in sets


def test_on_grid_current_is_present_without_duplication():
    # The live (10, 0.008) is already a grid point -> stays 9.
    sets = param_sets("momentum", MOM_CFG)
    assert len(sets) == 9
    assert {"lookback_samples": 10, "momentum_threshold": 0.008} in sets


def test_grid_does_not_duplicate_an_on_grid_current():
    cfg = {**MOM_CFG, "lookback_samples": 5, "momentum_threshold": 0.003}
    sets = param_sets("momentum", cfg)
    assert len(sets) == 9  # current already in the grid


def test_rank_orders_by_total_pnl_desc_and_assigns_ranks():
    ranked = rank([_result(100, 5), _result(500, 5), _result(300, 5)])
    assert [r["metrics"]["total_pnl_cents"] for r in ranked] == [500, 300, 100]
    assert [r["rank"] for r in ranked] == [1, 2, 3]


def test_winner_requires_min_trades():
    # Highest P&L has too few trades; the winner is the next eligible one.
    ranked = rank([_result(900, MIN_TRADES - 1), _result(400, MIN_TRADES)])
    assert ranked[0]["is_winner"] is False
    assert ranked[1]["is_winner"] is True


def test_run_symbol_marks_current_and_keeps_curves_only_for_winner_and_current():
    series = [(i, None, 10_000 + i * 300) for i in range(30)]
    block = run_symbol("NOVA", MOM_CFG, series, max_position=1000)
    assert block["symbol"] == "NOVA"
    assert block["strategy_type"] == "momentum"
    assert any(r["is_current"] for r in block["results"])
    with_curves = [r for r in block["results"] if r["equity_curve"]]
    # At most the winner and the current set carry an equity curve.
    assert len(with_curves) <= 2
