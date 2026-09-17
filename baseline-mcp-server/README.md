# Baseline MCP Read Server

A working read-only MCP server for the GIC hackathon — **you run it yourself** and
consume it from your panel. The protocol plumbing is already written, so you don't
build it, but hosting it is yours: it exposes your team's portfolio/P&L to a
conversational panel (requirement **M4**). See [`HANDOVER.md`](HANDOVER.md).

**Docs:** the student "how to use" guide is `mcp-server-reference.md`, in your
problem-statement package.

## Dual surface (one port, default 8090)

- **MCP** (streamable HTTP) at `/mcp` — for MCP-native clients (e.g. Copilot).
- **REST facade** `POST /tools/{name}` — for the browser dashboard, which can't
  speak raw MCP. Returns `{ source, as_of, data }`. See
  [`adapter/mcp-adapter.example.js`](adapter/mcp-adapter.example.js).

## Read tools

| Tool | Backing call | Returns (dollars, SGT-stamped) |
|------|--------------|--------------------------------|
| `get_portfolio` | `GET /portfolio` | cash, total value, realised / unrealised / session P&L |
| `get_positions` | `GET /portfolio` | per-symbol quantity, avg entry price, unrealised P&L |
| `get_pnl` | `GET /portfolio` | aggregate + per-instrument P&L |
| `get_session_summary` | `GET /portfolio` + `GET /orders` | total P&L, trade count, best/worst instrument |

Every response carries a `source` (cite it) and an `as_of` SGT timestamp.
Instruments absent from the portfolio are returned as **zeroed** entries, not
omitted (BUG-019 handled server-side).

**Control tools** (`pause_instrument`, `resume_instrument`, `kill_switch`) ship as
inert stubs that return a not-wired notice — activating them is a student bonus.

## Run

```bash
pip install -r requirements.txt
# Edit config.yaml: exchange.base_url (sandbox/live) + your team api_key.
python server.py                 # serves on :8090
# or: uvicorn server:app --host 0.0.0.0 --port 8090
```

Try it:

```bash
curl -s http://localhost:8090/health
curl -s -X POST http://localhost:8090/tools/get_pnl | jq
```

## Test

```bash
pip install -r requirements.txt
pytest -q
```
