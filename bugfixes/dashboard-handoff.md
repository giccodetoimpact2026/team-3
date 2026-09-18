# P4/P5 dashboard handoff

Date: 18 September 2026 (SGT)
Branch: `p4-p5-dashboard`

## Summary

The dashboard now runs on the exchange SSE feed instead of a 30-second timer, clears
stale values on a feed drop, shows SGT everywhere, discovers instruments at runtime, and
adds the operations views Gerald and Linda asked for: session order counters, per-
instrument P&L with its source, an order lifecycle table and a fixed-schema CSV export.

`npm run check` and `npm test` pass (42 tests). The page was verified against the
sandbox exchange with the MCP server running, including a forced feed error and a
forced MCP outage.

## Must-fixes

| Must-fix | Tracker | What changed |
|---|---|---|
| #1 SSE instead of polling | BUG-ARCH | `EventSource` on `/exchange/events`. `price` events trigger a portfolio re-read coalesced to one per second; `trade` and `session` events trigger a full re-read (portfolio, orders, MCP P&L) coalesced to one per five seconds. Replayed event ids are ignored. Session badge and announcements rendered from the feed. |
| #4 Stale values on reconnect | BUG-007 | On feed error every figure becomes "Reconnecting…" with the `stale` style and the dot goes OFFLINE; on the next successful read normal display returns. |
| #6 Missing instruments | BUG-019, BUG-004 | Symbols come from `GET /instruments` (re-read on session change). An untraded symbol renders quantity `0` and dashes for prices. |
| #7 SGT | BUG-021 | `formatSgt` renders `YYYY-MM-DD HH:MM:SS.mmm SGT` by fixed UTC+8 offset so output is identical across browsers and Node; used for the clock, announcements, lifecycle rows and the export. |

## Hour 2 features

- Session counters (buy, sell, partially filled, rejected) from `GET /orders`; "partially filled" counts orders that were ever observed in that state.
- Per-instrument P&L from MCP `get_pnl` with `source` and `as_of` shown; falls back to `GET /portfolio` per-position figures with the label "GET /portfolio (direct; MCP unavailable)".
- Order lifecycle table: one row per observed status transition, newest first, Gerald's codes (`OPEN` shown as `ACKNOWLEDGED`), persisted per session id in `localStorage`.
- CSV export: `TradeID,Timestamp_SGT,Instrument,Direction,Quantity,LimitPrice,FillPrice,FillQuantity,RemainingQty,Status,PnLImpact,CumulativePnL`, CRLF lines, period decimals, no grouping. Sell fills book (fill − basis) × qty; the session-open basis is reconstructed by walking fills backwards from the current volume-weighted cost.

## Files

`baseline-dashboard/`: `server.js` (dev server + key-injecting proxy), `index.html`,
`css/dashboard.css`, `js/{config,format,view,trades,mcp,api,dashboard}.js`,
`tests/{format,view,trades}.test.js`, `README.md`, `AGENTS.md`.
Root: `.github/workflows/dashboard-ci.yml`.

## Known limits

- The lifecycle table only holds transitions the dashboard saw while open; a fill that happened while the page was closed appears as a single `FILLED` row.
- `trade` SSE events are market-wide, so a re-read may find nothing changed.
- `CONFIG.MCP_BASE_URL` defaults to port 8091; the MCP server is started with `MCP_PORT=8091`.

## Open

Heartbeat health dot, kill switch (bot `halt()` is ready to receive a command), MCP
conversational panel, price chart, dark mode.
