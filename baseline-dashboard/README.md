# Baseline Trading Dashboard

The starting dashboard for the GIC hackathon — visually coherent but
**functionally limited and deliberately incomplete**. You inherit it and enhance
it. See [`HANDOVER.md`](HANDOVER.md) for Sarah's notes and [`AGENTS.md`](AGENTS.md)
for AI-assistant context.

Vanilla HTML/CSS/JS + jQuery (pre-framework — you may refactor to React).

## What it does

- Subscribes to the exchange SSE feed (`/events`) and re-reads `GET /portfolio`
  and `GET /orders` when the market moves (coalesced, never on a timer).
- Renders three P&L cards, a positions table for every instrument from
  `GET /instruments`, session order counters (buy / sell / partially filled /
  rejected), per-instrument P&L from the MCP read server (falls back to
  `GET /portfolio` with a source label when MCP is down), an order lifecycle
  table with exact status codes, a session badge, announcements, a connectivity
  dot, and an SGT clock.
- Exports the session's order lifecycle as CSV in Gerald's fixed schema
  (`TradeID,Timestamp_SGT,...`, `YYYY-MM-DD HH:MM:SS.mmm SGT`, period decimals).
- On a feed drop every figure is replaced with "Reconnecting…" until fresh data
  arrives; a session id change resets all session-scoped state.
- Money is integer cents on the wire, shown as dollars.
- Kill switch: a `HALT BOT` button in the top bar (one click, no confirmation)
  sends `POST /bot/halt`; the bot blocks new orders, cancels every open order
  and stops. A pill next to it shows the bot state from `GET /bot/status`
  (RUNNING / HALTING / HALTED / OFFLINE), polled every 3 s. `server.js`
  proxies `/bot/*` to the bot's local control port (`BOT_CONTROL_URL`, default
  `http://127.0.0.1:8092`, matching `control.port` in the bot's `config.yaml`).

## What it deliberately lacks

Heartbeat health, the conversational MCP panel, a predictive view, charts,
order book, alerts, and dark mode — still open.

## Run

Open `index.html` in a browser, or serve the folder:

```bash
npm start                      # node server.js -> http://localhost:5500 (static + /exchange proxy, key from EXCHANGE_API_KEY or ../baseline-bot/.env)
```

The per-instrument P&L section reads the MCP server at `CONFIG.MCP_BASE_URL`
(default `http://localhost:8091`). Start it from `../baseline-mcp-server`:

```bash
MCP_PORT=8091 MCP_EXCHANGE_BASE_URL=https://api.codetoimpact.sg \
MCP_EXCHANGE_API_KEY=<api_key> python server.py
```

Edit [`js/config.js`](js/config.js): `API_BASE_URL` (sandbox/live), `API_KEY`, and
`MCP_BASE_URL` (the provided read-only MCP server on `:8090`).

## Test

Requires **Node ≥ 18** (`node --test` / `--check`); CI runs Node 20. On older Node you'll
see an opaque `bad option: --test`.

```bash
npm run check   # node --check (syntax)
npm test        # node --test (unit tests)
```

## Layout

```
baseline-dashboard/
├── index.html
├── css/dashboard.css              # light theme; dark-mode vars stubbed
├── js/{config.js, format.js, view.js, trades.js, mcp.js, api.js, dashboard.js}
│                                  # format/view/trades are pure + unit-tested
├── assets/gic-logo-placeholder.svg
├── mcp-adapter.example.js         # M4 starting point (one MCP read-tool call)
├── tests/                         # starter unit tests
└── .github/workflows/ci.yml       # starter CI (check + tests)
```

## Notes

- Auth is `X-API-Key` on every call.
- Prices/money are **integer cents** on the wire ($42.50 → `4250`).
- The assistant panel (M4) consumes the provided MCP read server — see
  `mcp-adapter.example.js` and `mcp-server-reference.md`.
