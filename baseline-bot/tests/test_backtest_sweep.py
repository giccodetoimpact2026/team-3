"""Unit tests for the parameter grid, ranking, and winner gating."""
from backtest.sweep import (
    MIN_TRADES, current_params, param_sets, rank, run_all, run_symbol,
)

MOM_CFG = {"type": "momentum", "lookback_samples": 10, "momentum_threshold": 0.008,
           "trade_quantity": 100, "limit_offset_bps": 5}

PAIRS_CFG = {"type": "pairs_follow", "leader": "NOVA", "lookback_samples": 1,
             "leader_breakout_samples": 10, "breakout_arm_samples": 5,
             "exit_drawdown": 0.004, "trade_quantity": 400, "limit_offset_bps": 5}


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


# --- pairs_follow sweep support --------------------------------------------

def test_current_params_reads_the_swept_keys_per_strategy_type():
    # Generalised beyond lookback+threshold: pairs_follow has three, cast to
    # match their grid values (ints stay int, exit_drawdown stays float).
    cur = current_params("pairs_follow", PAIRS_CFG)
    assert cur == {"leader_breakout_samples": 10, "breakout_arm_samples": 5,
                   "exit_drawdown": 0.004}


def test_pairs_follow_grid_is_the_cartesian_product_with_current_on_grid():
    sets = param_sets("pairs_follow", PAIRS_CFG)
    assert len(sets) == 27  # 3x3x3, current already a grid point
    assert current_params("pairs_follow", PAIRS_CFG) in sets


def _pairs_config():
    return {
        "instruments": {"active": {"primary": "NOVA", "secondary": "ELXR",
                                   "extra": ["AXON"]}},
        "strategy": {"NOVA": {**MOM_CFG, "lookback_samples": 15}, "AXON": PAIRS_CFG},
        "risk": {"max_position_primary": 1000, "max_position_secondary": 1000,
                 "max_position": {"AXON": 1000}},
    }


def test_run_all_backtests_pairs_follow_when_its_leader_has_data():
    nova = [(2 * i + 1, None, 10_000 + i * 60) for i in range(30)]
    axon = [(2 * i + 2, None, 10_000) for i in range(30)]
    blocks = run_all(_pairs_config(), {"NOVA": nova, "AXON": axon})
    axon_block = next(b for b in blocks if b["symbol"] == "AXON")
    assert axon_block["strategy_type"] == "pairs_follow"
    assert len(axon_block["results"]) == 27


def test_run_all_skips_a_follower_whose_leader_has_no_data():
    # AXON present but its leader NOVA absent from the segment -> skipped, no crash.
    axon = [(2 * i + 2, None, 10_000) for i in range(30)]
    blocks = run_all(_pairs_config(), {"AXON": axon})
    assert all(b["symbol"] != "AXON" for b in blocks)
