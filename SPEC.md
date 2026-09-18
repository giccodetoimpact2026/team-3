# Implementation specification — current increment

Target user: the trader running the team's intraday bot. Immediate priority is bot
startup and reliable NOVA/ELXR operation, per the team's instruction.

Implemented: BUG-001 shutdown cancellation, BUG-011 SSE reconnect/replay, BUG-012
ELXR lookback 15, and the bot portion of BUG-019 (missing position = zero). Supporting
work includes environment credentials, runtime symbol validation, per-symbol evaluation
throttling, no pending-order stacking, unknown submission reconciliation and
session-history reset. There is no observation mode or live/sandbox gate in the run
loop: the bot trades whatever `EXCHANGE_BASE_URL` points at once the session is open.

Bot health and control: a heartbeat every 5 s exposed on `GET /status` with feed state
and age; a feed-stall watchdog restarting the SSE stream after 20 s without an event;
an order journal on `GET /orders?since=N` (every submit, fill, cancel and
reconciliation, with exchange codes); `POST /halt` and SIGINT/SIGTERM block new
intents atomically, cancel through the order manager, report unresolved cancels and
exit non-zero when any remain. A burst above `max_orders_per_minute` pauses new intents
(BUG-003). Startup validation rejects a placeholder key, non-positive sizes or
lookbacks, thresholds outside (0,1), bad ports and heartbeat timings.

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

Dashboard health and order channel: the bot pill is heartbeat-based (more than 15 s
without a beat shows DEAD even when the endpoint answers; feed DEGRADED/DEAD and an
order-rate pause are shown on the pill). The lifecycle table and the rejected counter
are fed from the bot's order journal as well as `GET /orders`, so REJECTED and UNKNOWN
submits appear with their codes and bot-side SGT timestamps. A market-wide `trade`
event no longer triggers an orders re-read while the bot reports nothing resting.

Instrument risk model (`docs/risk-model-design.md`, `baseline-bot/risk/`): the bot
sizes, stops and caps by instrument behaviour using only data it already receives.
Requirements read as genuine and implemented: Priya's per-instrument stop-losses
(volatility-scaled from cost basis, visible on `GET /status`, action is one sell into
the bids), her 15% drawdown alert (baseline is equity at the first portfolio read of
the window; 10% alerts, 15% alerts and blocks new buys while sells and stops continue),
her single-instrument loss alert (2% of starting equity pauses that symbol's buys),
DRFT at a fifth of NOVA's size and a fifth of visible ask depth (Priya, Derek, the
00:11 alignment; the junior note's "normal size" is rejected as contradicted by
Derek's own interview), Clarissa's and Farrukh's correlated-exposure concern
(NOVA+AXON group cap, gross cap, measured correlation shown and alerted), BUG-014
sizing by volatility and liquidity, and James's page-3 ATR regime with hysteresis
(momentum off in LOW, half size in NEUTRAL), computed on 10-second bars because feed
timing is irregular (brief §8 question 3). Thresholds were calibrated on a sandbox
sample recorded in the design doc and remain configuration. Sharpe and information
ratio are deferred as reporting without a defined convention. Regime-based strategy
switching (swapping instances) is not built; the model provides the regime and the size
factor for it.

Deferred from this increment: strategy routing by regime, a manual per-instrument
pause route, spread-aware limit pricing and the AI/MCP conversational panel. These are
unfinished, not removed from the overall hackathon scope. DRFT trades only through the
DCA overlay at the reduced size above. Emergency halt is immediate; parameter changes
need two-step confirmation.

Architecture: Python strategy instances consume public exchange SSE; authenticated REST
reads gate limit-only, integer-cent, long-only orders. The dashboard is a separate
process served by `baseline-dashboard/server.js`, which proxies `/exchange/*` to the
exchange and injects `X-API-Key` so the key never reaches the browser. The MCP read
server is a third process; the dashboard calls its REST facade directly.
