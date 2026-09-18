# Implementation specification — current increment

Target user: the trader running the team's intraday bot. Immediate priority is bot
startup and reliable NOVA/ELXR operation, per the team's instruction.

Implemented: BUG-001 shutdown cancellation, BUG-011 SSE reconnect/replay, BUG-012
ELXR lookback 15, and the bot portion of BUG-019 (missing position = zero). Supporting
work includes environment credentials, runtime symbol validation, per-symbol evaluation
throttling, no pending-order stacking, unknown submission reconciliation, session-history
reset, observation mode and sandbox-only trading unless live is explicitly enabled. A
price-only regime classifier now emits warmup, compression, range, neutral, directional
trend and directional shock results from ATR-like volatility and directional efficiency;
it also reports regime transitions and explanatory metrics. It is not yet wired to select
or size strategies.

Acceptance: local strategy/runtime tests and lint pass; an authenticated sandbox check
and trading smoke test remain pending a locally configured team key. No profitability
claim is made. SIGKILL or network loss can prevent shutdown cancellation; failures must
be visible. Shutdown cancels all resting team orders; use one order-writing bot.

Dashboard increment (P4/P5): the dashboard subscribes to the exchange SSE feed and
re-reads `GET /portfolio` and `GET /orders` only when the market moves, coalesced to
respect the shared 300-per-30s read tier (must-fix #1). On a feed drop every figure is
replaced with "Reconnecting…" and the session badge is cleared until fresh data
arrives (must-fix #4). All displayed and exported times are `YYYY-MM-DD HH:MM:SS.mmm
SGT` (must-fix #7). Instruments come from `GET /instruments`; an untraded symbol renders
quantity `0` and dashes, never `undefined` (must-fix #6, dashboard side of BUG-019).
Session counters (buy, sell, partially filled, rejected) are always visible. Per-
instrument P&L is read from the MCP `get_pnl` tool with its `source` and `as_of`
labels; when the MCP server is unreachable the same table is filled from
`GET /portfolio` and the label says so. An order lifecycle table records every status
transition the dashboard observed, using Gerald's codes (`OPEN` shown as
`ACKNOWLEDGED`), and the CSV export uses his fixed column order with period decimals.
Values from the exchange are HTML-escaped before rendering. Pure logic (formatting,
view rows, SSE cursor, lifecycle, cost basis, CSV) is unit-tested in Node and run by
repo-root CI.

Dashboard acceptance: `npm run check` and `npm test` pass (42 tests); the page was
exercised against the sandbox exchange with a running MCP server, including a forced
feed error and a forced MCP outage. The lifecycle table only contains transitions the
dashboard saw while open; a fill that happened while the page was closed appears as a
single `FILLED` row. Per-fill realized P&L in the export is reconstructed from the
session's fills and the current volume-weighted cost basis.

Deferred from this increment: kill-switch UI, heartbeat health dot, regime routing and
confirmation/hysteresis above the implemented classifier,
additional instruments, stop-losses, portfolio caps and the AI/MCP conversational panel.
These are unfinished, not removed from the overall hackathon scope. DRFT remains off.
Emergency halt should be immediate; parameter changes need two-step confirmation.

Architecture: Python strategy instances consume public exchange SSE; authenticated REST
reads gate limit-only, integer-cent, long-only orders. The dashboard is a separate
process served by `baseline-dashboard/server.js`, which proxies `/exchange/*` to the
exchange and injects `X-API-Key` so the key never reaches the browser. The MCP read
server is a third process; the dashboard calls its REST facade directly.
