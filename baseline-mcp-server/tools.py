"""Read-tool logic, kept independent of the MCP/HTTP wiring so it is directly
testable.

Every tool returns the contract object `{ source, as_of, data }`:
  - `source`  names the backing exchange endpoint, so the panel can CITE it;
  - `as_of`   is an SGT timestamp;
  - `data`    is a small JSON object.

Money is INTEGER CENTS on the exchange wire; tools return DOLLARS (the panel is
human-facing — see the spec's example output).
"""
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))

# Control-tool stub payload (bonus for students to wire — see HANDOVER.md).
NOT_WIRED = {
    "status": "not_implemented",
    "note": "Control channel to bot not wired. See M4 bonus / B-list.",
}


def _now_sgt() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " SGT"


def _dollars(cents) -> float:
    return round((cents or 0) / 100, 2)


def _envelope(source: str, data: dict) -> dict:
    return {"source": source, "as_of": _now_sgt(), "data": data}


def _positions_by_symbol(client) -> dict:
    """Per-symbol view over the FULL instrument universe. BUG-019 is handled
    here: an instrument absent from the portfolio is returned as a zeroed entry,
    never omitted."""
    portfolio = client.get_portfolio()
    present = {p["symbol"]: p for p in portfolio.get("positions", [])}
    symbols = [i["symbol"] for i in client.get_instruments()]

    out = {}
    for sym in symbols:
        p = present.get(sym)
        if p:
            out[sym] = {
                "quantity": p.get("quantity", 0),
                "avg_entry_price": _dollars(p.get("avg_cost")),
                "last_price": _dollars(p.get("last_price")),
                "unrealized_pnl": _dollars(p.get("unrealized_pnl")),
                "realized_pnl": _dollars(p.get("realized_pnl")),
            }
        else:
            out[sym] = {
                "quantity": 0,
                "avg_entry_price": 0.0,
                "last_price": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
            }
    return out


def get_portfolio(client) -> dict:
    p = client.get_portfolio()
    realized = p.get("realized_pnl", 0)
    unrealized = p.get("unrealized_pnl", 0)
    return _envelope("GET /portfolio", {
        "cash": _dollars(p.get("cash")),
        "reserved_cash": _dollars(p.get("reserved_cash")),
        "total_value": _dollars(p.get("total_value")),
        "realized_pnl": _dollars(realized),
        "unrealized_pnl": _dollars(unrealized),
        # Single-window model: session P&L is realized + unrealized. A per-window
        # baseline (three-window day) is a later exchange change.
        "session_pnl": _dollars(realized + unrealized),
    })


def get_positions(client) -> dict:
    return _envelope("GET /portfolio", {"positions": _positions_by_symbol(client)})


def get_pnl(client) -> dict:
    p = client.get_portfolio()
    by_symbol = _positions_by_symbol(client)
    by_instrument = {
        sym: {
            "realized_pnl": v["realized_pnl"],
            "unrealized_pnl": v["unrealized_pnl"],
            "total_pnl": round(v["realized_pnl"] + v["unrealized_pnl"], 2),
        }
        for sym, v in by_symbol.items()
    }
    realized = p.get("realized_pnl", 0)
    unrealized = p.get("unrealized_pnl", 0)
    return _envelope("GET /portfolio", {
        "aggregate": {
            "realized_pnl": _dollars(realized),
            "unrealized_pnl": _dollars(unrealized),
            "total_pnl": _dollars(realized + unrealized),
        },
        "by_instrument": by_instrument,
    })


def get_session_summary(client) -> dict:
    p = client.get_portfolio()
    orders = client.get_orders()
    realized = p.get("realized_pnl", 0)
    unrealized = p.get("unrealized_pnl", 0)

    trade_count = sum(
        1 for o in orders if o.get("status") in ("FILLED", "PARTIALLY_FILLED")
    )
    totals = {
        sym: round(v["realized_pnl"] + v["unrealized_pnl"], 2)
        for sym, v in _positions_by_symbol(client).items()
    }
    best = max(totals, key=totals.get) if totals else None
    worst = min(totals, key=totals.get) if totals else None

    return _envelope("GET /portfolio + GET /orders", {
        "total_pnl": _dollars(realized + unrealized),
        "trade_count": trade_count,
        "best_instrument": {"symbol": best, "pnl": totals[best]} if best else None,
        "worst_instrument": {"symbol": worst, "pnl": totals[worst]} if worst else None,
    })


# Registry consumed by both the MCP tool wrappers and the REST facade.
READ_TOOLS = {
    "get_portfolio": get_portfolio,
    "get_positions": get_positions,
    "get_pnl": get_pnl,
    "get_session_summary": get_session_summary,
}
CONTROL_TOOLS = ("pause_instrument", "resume_instrument", "kill_switch")
