# Baseline Trading Dashboard

The starting dashboard for the GIC hackathon — visually coherent but
**functionally limited and deliberately incomplete**. You inherit it and enhance
it. See [`HANDOVER.md`](HANDOVER.md) for Sarah's notes and [`AGENTS.md`](AGENTS.md)
for AI-assistant context.

Vanilla HTML/CSS/JS + jQuery (pre-framework — you may refactor to React).

## What it does

- Polls the exchange `GET /portfolio` every 30s and renders three P&L cards, a
  positions table, a connectivity dot, and a footer clock. Money is integer cents
  on the wire, shown as dollars.

## What it deliberately lacks

Live SSE data, heartbeat health, a kill switch, the conversational MCP panel, a
predictive view, charts, order book, alerts, export, dark mode, and instrument
coverage beyond two — all student work. There are also a few inherited bugs; the
handover notes point at them.

## Run

Open `index.html` in a browser, or serve the folder:

```bash
npm start                      # node server.js -> http://localhost:5500 (static + /exchange proxy, key from EXCHANGE_API_KEY or ../baseline-bot/.env)
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
├── js/{config.js, format.js, api.js, dashboard.js}
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
