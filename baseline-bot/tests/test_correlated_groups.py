"""Correlated-group notional cap (BUG-008). Prices are integer cents.

The invariant: per-instrument caps can all be satisfied while the book is one
directional bet. NOVA and AXON move together, and AXON's follower strategy is
long-only, so it stacks on NOVA rather than hedging it.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from exchange.results import Outcome, SubmitResult
from orders.manager import OrderManager
from risk.limits import RiskLimits

GROUPS = {"energy_freight": {"symbols": ["NOVA", "AXON"],
                             "max_notional_cents": 15_000_000}}


def risk(**kwargs):
    return RiskLimits(500, 400, "NOVA", "ELXR",
                      extra_limits={"AXON": 500, "CRVX": 600},
                      correlated_groups=kwargs.pop("groups", GROUPS), **kwargs)


# =========================================================================
# RiskLimits
# =========================================================================

def test_group_membership_is_looked_up_both_ways():
    r = risk()
    assert r.group_for("NOVA") == "energy_freight"
    assert r.group_for("AXON") == "energy_freight"
    assert set(r.group_symbols("energy_freight")) == {"NOVA", "AXON"}


def test_uncorrelated_symbol_has_no_group():
    assert risk().group_for("ELXR") is None


def test_group_allows_a_buy_inside_the_cap():
    assert risk().allows_group_buy("energy_freight", 10_000_000, 4_000_000)


def test_group_blocks_a_buy_that_would_breach():
    assert not risk().allows_group_buy("energy_freight", 14_000_000, 2_000_000)


def test_group_allows_exactly_at_the_cap():
    assert risk().allows_group_buy("energy_freight", 14_000_000, 1_000_000)


def test_unknown_group_never_blocks():
    """An uncorrelated symbol has no joint cap — that is the default."""
    assert risk().allows_group_buy("nope", 10**12, 10**12)


def test_nonpositive_addition_is_refused():
    assert not risk().allows_group_buy("energy_freight", 0, 0)


# --- config validation --------------------------------------------------

def test_one_symbol_group_is_rejected():
    """Already covered by max_position; a group of one is a config mistake."""
    with pytest.raises(ValueError, match="at least two symbols"):
        risk(groups={"solo": {"symbols": ["NOVA"], "max_notional_cents": 1}})


def test_group_without_a_cap_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        risk(groups={"g": {"symbols": ["NOVA", "AXON"]}})


def test_overlapping_groups_are_rejected():
    """Two caps on one symbol makes the binding constraint ambiguous."""
    with pytest.raises(ValueError, match="may belong to one group"):
        risk(groups={
            "a": {"symbols": ["NOVA", "AXON"], "max_notional_cents": 1},
            "b": {"symbols": ["AXON", "CRVX"], "max_notional_cents": 1},
        })


# =========================================================================
# OrderManager: notional measurement
# =========================================================================

@pytest.fixture
def client():
    c = Mock()
    c.get_orders.return_value = []
    c.get_portfolio.return_value = {
        "session_id": "w1", "cash": 1_000_000_000, "reserved_cash": 0,
        "positions": []}
    c.submit_limit_order.side_effect = lambda s, side, q, p, k: SubmitResult(
        Outcome.ACKNOWLEDGED, k, order_id=k, status="OPEN", remaining_qty=q)
    return c


@pytest.fixture
def manager(client):
    return OrderManager(client, risk(), session_id="w1", max_active_per_symbol=99)


def test_mark_comes_from_the_live_print(manager):
    manager.record_mark("NOVA", 15_000)
    assert manager.mark("NOVA") == 15_000


def test_mark_falls_back_to_the_portfolio(manager, client):
    client.get_portfolio.return_value["positions"] = [
        {"symbol": "NOVA", "quantity": 100, "reserved_qty": 0,
         "avg_cost": 14_000, "last_price": 14_500}]
    manager.refresh_positions(force=True)
    assert manager.mark("NOVA") == 14_500


def test_never_traded_symbol_contributes_nothing(manager):
    """No mark means no fills are possible, so no exposure."""
    assert manager.projected_notional(["NOVA", "AXON"]) == 0


def test_notional_sums_pending_exposure_across_the_group(manager):
    manager.record_mark("NOVA", 15_000)
    manager.record_mark("AXON", 8_000)
    manager.submit_intent("NOVA", "buy", 100, 15_000)   # 1_500_000 cents
    manager.submit_intent("AXON", "buy", 100, 8_000)    #   800_000 cents
    assert manager.projected_notional(["NOVA", "AXON"]) == 2_300_000


# =========================================================================
# The gate, end to end
# =========================================================================

def test_group_cap_blocks_the_second_leg(manager):
    """The case Clarissa named: both per-symbol caps fine, theme doubled."""
    manager.record_mark("NOVA", 20_000)
    manager.record_mark("AXON", 20_000)
    # 500 NOVA @ 20_000 = 10_000_000, inside NOVA's 500-share cap.
    assert manager.submit_intent("NOVA", "buy", 500, 20_000) is not None
    # 500 AXON would be inside AXON's own 500-share cap, but takes the group
    # to 20_000_000 against a 15_000_000 cap.
    assert manager.submit_intent("AXON", "buy", 500, 20_000) is None


def test_group_cap_permits_a_smaller_second_leg(manager):
    manager.record_mark("NOVA", 20_000)
    manager.record_mark("AXON", 20_000)
    manager.submit_intent("NOVA", "buy", 500, 20_000)   # 10_000_000
    assert manager.submit_intent("AXON", "buy", 200, 20_000) is not None


def test_uncorrelated_symbol_is_unaffected(manager):
    manager.record_mark("NOVA", 20_000)
    manager.record_mark("ELXR", 20_000)
    manager.submit_intent("NOVA", "buy", 500, 20_000)
    assert manager.submit_intent("ELXR", "buy", 400, 20_000) is not None


def test_sells_are_never_group_gated(manager, client):
    """Reducing correlated exposure must never be blocked by its own cap."""
    client.get_portfolio.return_value["positions"] = [
        {"symbol": "AXON", "quantity": 500, "reserved_qty": 0,
         "avg_cost": 20_000, "last_price": 20_000},
        {"symbol": "NOVA", "quantity": 500, "reserved_qty": 0,
         "avg_cost": 20_000, "last_price": 20_000}]
    manager.refresh_positions(force=True)
    assert manager.projected_notional(["NOVA", "AXON"]) == 20_000_000  # over cap
    assert manager.submit_intent("AXON", "sell", 500, 20_000) is not None


def test_pending_orders_count_toward_the_group(manager):
    """A resting buy is exposure; the cap must not wait for the fill."""
    manager.record_mark("NOVA", 20_000)
    manager.record_mark("AXON", 20_000)
    manager.submit_intent("NOVA", "buy", 400, 20_000)   # 8_000_000, unfilled
    assert manager.submit_intent("AXON", "buy", 400, 20_000) is None


def test_group_state_clears_at_a_session_boundary(manager):
    manager.record_mark("NOVA", 20_000)
    manager.submit_intent("NOVA", "buy", 500, 20_000)
    manager.reset_for_session("w2")
    assert manager.projected_notional(["NOVA", "AXON"]) == 0


# =========================================================================
# The real config
# =========================================================================

def test_real_config_groups_nova_with_axon():
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    r = RiskLimits(
        config["risk"]["max_position_primary"],
        config["risk"]["max_position_secondary"],
        config["instruments"]["active"]["primary"],
        config["instruments"]["active"]["secondary"],
        extra_limits=config["risk"].get("max_position", {}),
        correlated_groups=config["risk"].get("correlated_groups", {}),
    )
    group = r.group_for("NOVA")
    assert group is not None
    assert r.group_for("AXON") == group
    assert r.max_group_notional(group) > 0


def test_real_config_group_cap_binds_before_both_caps_fill():
    """A cap above the sum of its members' worth would never bind."""
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    group = config["risk"]["correlated_groups"]["energy_freight"]
    caps = {"NOVA": config["risk"]["max_position_primary"],
            **config["risk"]["max_position"]}
    shares = sum(caps[s] for s in group["symbols"])
    # At the top of the documented $60-$300 instrument range.
    assert group["max_notional_cents"] < shares * 30_000
