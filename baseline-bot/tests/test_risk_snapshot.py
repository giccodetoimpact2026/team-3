"""The dashboard's Risk view shows the position caps (Priya: risk parameters
must be visible on the dashboard, not buried in a config file). Reported
under the `limits` key of GET /status; `risk` belongs to the risk model."""
from risk.limits import RiskLimits


def test_snapshot_reports_the_cap_for_every_discovered_instrument():
    risk = RiskLimits(1200, 800, "NOVA", "ELXR", max_position_default=1500)
    assert risk.snapshot(["AXON", "ELXR", "NOVA"]) == {
        "max_position": {"AXON": 1500, "ELXR": 800, "NOVA": 1200},
    }


def test_snapshot_with_no_instruments_is_empty():
    assert RiskLimits(1, 1, "A", "B").snapshot([]) == {"max_position": {}}
