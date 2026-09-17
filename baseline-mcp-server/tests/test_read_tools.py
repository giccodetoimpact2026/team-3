"""Read-tool contract tests. A fake exchange client stands in for the network,
so these run fully offline."""
import server
import tools


class FakeClient:
    """Portfolio holds NOVA (up) and a flat-but-realised ELXR; CRVX/DRFT/AXON are
    absent from the portfolio (exercises BUG-019 zero-fill). Cents on the wire."""

    def get_portfolio(self):
        return {
            "cash": 100_000_00,
            "reserved_cash": 0,
            "realized_pnl": 500_00,       # $500 aggregate realised
            "unrealized_pnl": 100_00,     # $100 aggregate unrealised
            "total_value": 101_600_00,
            "positions": [
                {"symbol": "NOVA", "quantity": 100, "avg_cost": 150_00,
                 "last_price": 151_00, "market_value": 15_100_00,
                 "unrealized_pnl": 100_00, "realized_pnl": 200_00},
                {"symbol": "ELXR", "quantity": 0, "avg_cost": 0,
                 "last_price": 300_00, "market_value": 0,
                 "unrealized_pnl": 0, "realized_pnl": 300_00},
            ],
        }

    def get_orders(self):
        return [
            {"status": "FILLED"}, {"status": "PARTIALLY_FILLED"},
            {"status": "OPEN"}, {"status": "CANCELLED"},
        ]

    def get_instruments(self):
        return [{"symbol": s} for s in ("NOVA", "AXON", "CRVX", "DRFT", "ELXR")]


FAKE = FakeClient()


def _assert_envelope(out, expected_source):
    assert set(out.keys()) == {"source", "as_of", "data"}
    assert out["source"] == expected_source
    assert out["as_of"].endswith(" SGT")
    assert isinstance(out["data"], dict)


def test_get_portfolio_contract_and_dollars():
    out = tools.get_portfolio(FAKE)
    _assert_envelope(out, "GET /portfolio")
    d = out["data"]
    assert d["cash"] == 100_000.0          # cents -> dollars
    assert d["realized_pnl"] == 500.0
    assert d["unrealized_pnl"] == 100.0
    assert d["session_pnl"] == 600.0       # realized + unrealized (single window)


def test_get_positions_zero_fills_absent_instruments():
    out = tools.get_positions(FAKE)
    _assert_envelope(out, "GET /portfolio")
    pos = out["data"]["positions"]
    # All five instruments present, even the untraded ones (BUG-019 handled).
    assert set(pos.keys()) == {"NOVA", "AXON", "CRVX", "DRFT", "ELXR"}
    assert pos["NOVA"]["quantity"] == 100
    assert pos["NOVA"]["avg_entry_price"] == 150.0
    assert pos["NOVA"]["unrealized_pnl"] == 100.0
    # Untraded symbol -> zeroed entry, not omitted.
    assert pos["CRVX"] == {"quantity": 0, "avg_entry_price": 0.0, "last_price": 0.0,
                           "unrealized_pnl": 0.0, "realized_pnl": 0.0}


def test_get_pnl_aggregate_and_by_instrument():
    out = tools.get_pnl(FAKE)
    _assert_envelope(out, "GET /portfolio")
    d = out["data"]
    assert d["aggregate"]["total_pnl"] == 600.0
    assert d["by_instrument"]["NOVA"]["total_pnl"] == 300.0   # 200 realized + 100 unrealized
    assert d["by_instrument"]["ELXR"]["total_pnl"] == 300.0   # 300 realized, flat
    assert d["by_instrument"]["AXON"]["total_pnl"] == 0.0


def test_get_session_summary_best_worst_and_trade_count():
    out = tools.get_session_summary(FAKE)
    _assert_envelope(out, "GET /portfolio + GET /orders")
    d = out["data"]
    assert d["total_pnl"] == 600.0
    assert d["trade_count"] == 2            # FILLED + PARTIALLY_FILLED only
    assert d["best_instrument"]["pnl"] == 300.0
    assert d["worst_instrument"]["symbol"] in ("AXON", "CRVX", "DRFT")  # zero-P&L untraded
    assert d["worst_instrument"]["pnl"] == 0.0


# --- REST facade (the surface the dashboard adapter uses) -------------------
def _facade_client(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(server, "client", FAKE)
    return TestClient(server.app)


def test_facade_read_tool(monkeypatch):
    tc = _facade_client(monkeypatch)
    r = tc.post("/tools/get_pnl")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "GET /portfolio"
    assert body["data"]["aggregate"]["total_pnl"] == 600.0


def test_facade_control_tool_is_inert(monkeypatch):
    tc = _facade_client(monkeypatch)
    r = tc.post("/tools/kill_switch")
    assert r.status_code == 200
    assert r.json()["status"] == "not_implemented"


def test_facade_unknown_tool_404(monkeypatch):
    tc = _facade_client(monkeypatch)
    assert tc.post("/tools/nope").status_code == 404


def test_facade_health(monkeypatch):
    tc = _facade_client(monkeypatch)
    r = tc.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
