# HANDOVER.md — Sarah Lim (Frontend, Dashboard)

Hi, sorry to be leaving mid-project. Where things stand:

**What works**
- P&L display polls `/portfolio` every 30 seconds. Yes, 30s is too slow — the exchange
  publishes a live SSE feed; subscribe to that instead. I ran out of time. `EventSource`
  is native; no library needed (though custom headers on `EventSource` are flaky in
  some browsers — a fetch-based stream is the fallback).
- The positions table works for the two active instruments. Add more via
  `INSTRUMENTS_TO_SHOW`, but you'll hit the missing-symbol bug until you initialise
  instruments to zero.

**Started, not finished**
- Price chart: there's a commented-out `<canvas>`. No candles endpoint exists — build
  history from SSE `price` events yourself (a circular buffer per symbol).
- Dark mode: CSS variables are stubbed. Add `[data-theme="dark"]` overrides and a
  toggle. ~30 minutes.

**Known bugs**
- Stale values on reconnect — clear the DOM at the start of the refresh before new data
  resolves.
- UTC not SGT — replace `toUTCString()` with
  `new Date().toLocaleString('en-SG', { timeZone: 'Asia/Singapore' })`; append `" SGT"`
  on exports.
- `undefined` cells — initialise all instruments to zero on load, then update from the
  response.

**Never started**
- Kill switch. Important. Wire a control to cancel open orders and stop the bot.
- Alerts, audit log, export. The risk and ops folks care about these.
- The conversational assistant panel. There's a read-only MCP server provided and an
  `mcp-adapter.example.js` with one working tool call — that's the starting point.
- Bot health heartbeat — the bot needs to emit one before the dashboard can show real
  health rather than a REST-ping dot.

Bot-specific state and controls now come from the **provided read-only MCP server** —
the dashboard's job is to *consume* it via the conversational panel.

Good luck. — Sarah
