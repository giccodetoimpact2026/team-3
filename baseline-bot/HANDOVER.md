# HANDOVER.md — Alex Chen (Lead Developer, Bot)

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
  The bot ignores them today. There's a `# TODO(S1)` hook in the dispatcher.

**Architecture note**
Bot and dashboard should be separate processes. The dashboard reads exchange market
data directly via SSE. For bot-specific state, there's now a **read-only MCP server**
(separate repo) the dashboard can consume. I never built a way for the bot to *act on*
control commands — that's open.

— Alex
