# Repo Handover Notes
### The three developer handovers — one per baseline repo (each also ships as that repo's `HANDOVER.md`).

---

## `baseline-bot/HANDOVER.md` — Alex Chen (Lead Developer, Bot)

Hi — sorry for the short notice. Here's the state of the bot.

**What works**
- Momentum on the primary instrument (NOVA) trades reasonably in trending markets.
- Mean-reversion on the secondary instrument (ELXR) is connected and placing limit
  orders — but I'm not confident in the lookback parameter. Check James's notes.
- The SSE feed connects and accumulates price history.
- The REST client is clean; all endpoints are wrapped.

**Known issues**
- The SSE feed thread dies silently on a network disconnect. No reconnect. You need to
  detect when it's died and restart with the `Last-Event-ID` header.
- If you Ctrl-C the bot, open orders stay on the exchange. I started a SIGTERM handler
  and never finished it.
- The portfolio call crashes if a symbol hasn't traded yet and is missing from the
  response. Default missing symbols to zero.
- Order submission prints then swallows; the main loop and the SSE listener thread
  swallow silently (bare `try/except`, no print) — no classification, no retry.
  That needs to become real error handling.

**Never got to**
- The other three instruments. The config supports per-instrument strategy assignment —
  add entries and pick a strategy type.
- Market-making. Right strategy for the stable, liquid names. The skeleton is in
  `strategies/market_making.py.DISABLED` — I commented it out because the inventory
  skew was wrong (tighten ask when long, bid when short). The desk knows the fix.
- Regime detection. James had a clean ATR-based approach — page 3 of the scan.
- Announcement handling. The exchange fires `announcement` SSE events during trading.
  The bot ignores them today. There's a `# TODO` hook in the dispatcher.

**Architecture note**
Bot and dashboard should be separate processes. The dashboard reads exchange market
data directly via SSE. For your portfolio / P&L state, there's now a **read-only MCP
server** (separate repo) the dashboard can consume — it reads the exchange, not the
bot, so the bot's own liveness/strategy state still needs a direct bot→dashboard
channel. I never built a way for the bot to *act on* control commands — that's open.

— Alex

---

## `baseline-dashboard/HANDOVER.md` — Sarah Lim (Frontend, Dashboard)

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

Good luck. — Sarah

---

## `baseline-mcp-server/HANDOVER.md` — Platform / Engineering

This server is **provided and working** — you **run it yourself** and consume it; the protocol plumbing is written, so you don't have to build it.

**What's live**
- Read tools: `get_portfolio`, `get_positions`, `get_pnl`, `get_session_summary`.
  Each returns `{ source, as_of, data }` — `source` is the backing endpoint and
  `as_of` is an SGT timestamp, so your assistant can cite where an answer came from.
- A client adapter (`adapter/mcp-adapter.example.js`) shows one tool call end-to-end.
  Copy the pattern for the others.

**Stubs (bonus to activate)**
- Control tools — `pause_instrument`, `resume_instrument`, `kill_switch` — exist but
  return a "not wired" notice. Making them act requires a channel to the bot and
  bot-side handling (graceful pause/shutdown). That's a stretch goal, not a requirement.

**Config**
- `MCP_BASE_URL` (default `http://localhost:8090`), plus the exchange base URL and your
  API key in `config.yaml`.

— Platform team
