# GIC #CODETOIMPACT 2026 — Team 3

## Team Members

| Name | Role | Primary Focus |
|------|------|---------------|
| Hoo Di Heng | Bot architecture, dashboard | Feed supervision, SSE, dashboard P4/P5 |
| Athithyaa | Bot orders & risk | Order manager, risk engine, stop-losses |
| Jovita Eliana | Bot lifecycle & session | Shutdown, session bootstrap, kill switch |
| Kalent | Dashboard live data | SSE integration, stale-value handling |
| Michelle Louisa Tanuwijaya | Dashboard display & export | SGT timestamps, CSV export, instruments |
| Ivan Gondo Prastowo | Strategy & features | Pairs strategy, crash guard, DCA overlay |

---

## What Works

### Trading Bot (Python)
- **NOVA momentum strategy** — trades on price trend with configurable lookback
- **ELXR mean-reversion** — corrected 15-sample lookback (was 5; BUG-012)
- **SSE feed with supervised reconnect** — exponential backoff, jitter, Last-Event-ID replay, callback isolation, event worker queue decoupling SSE from trading
- **OrderManager** — per-process nonce idempotency keys, pending exposure tracking, one-active-order-per-symbol policy
- **Session lifecycle** — GET /session bootstrap at startup, session boundary reset (clears price history on session ID change)
- **Graceful shutdown** — SIGINT/SIGTERM blocks new intents, cancels all resting orders
- **Emergency halt path** — reusable by signal handlers and the kill-switch endpoint (POST /halt on localhost:8092)
- **Crash guard** — 15% drop triggers sell into bids; DCA overlay on all instruments
- **Pairs strategy** — AXON following NOVA (energy→freight correlation)
- **Instrument-strategies framework** — modular BaseStrategy with routing
- **Observation mode** — sandbox-only trading unless live explicitly enabled
- **15+ bot tests passing**, CI via GitHub Actions

### Monitoring Dashboard (JavaScript)
- **Real-time SSE feed** — replaces 30-second polling (Must-fix #1)
- **Stale-value handling** — clears to "Reconnecting..." on feed drop (Must-fix #4)
- **SGT timestamps everywhere** — YYYY-MM-DD HH:MM:SS.mmm SGT (Must-fix #7)
- **Dynamic instruments** — from GET /instruments; zero/dash for untraded (Must-fix #6)
- **Per-instrument P&L** — from MCP get_pnl with portfolio fallback and source labels
- **Session order counters** — buy, sell, partially filled, rejected
- **Order lifecycle table** — every status transition with Gerald's codes (OPEN→ACKNOWLEDGED)
- **Fixed-schema CSV export** — Gerald's column order, period decimals, SGT timestamps
- **Kill switch** — HALT BOT button, one click, no confirmation, with bot state pill
- **HTML escaping** — exchange strings sanitised before rendering
- **Key-injecting proxy** — API key never reaches the browser
- **42 Node unit tests passing**, CI via GitHub Actions

### MCP Server (Python)
- Read-only tools: get_portfolio, get_positions, get_pnl, get_session_summary
- Dual surface: MCP protocol (streamable HTTP at /mcp) + REST facade for browser

### Bug Fixes Completed
All 7 must-fix bugs resolved. 14 P0 defects from the remediation plan addressed.

---

## What's Aspirational

| Feature | Status | Why Deferred |
|---------|--------|-------------|
| AI conversational panel (MCP + LLM) | Not started | Must use organizer LLM service; time constraint |
| ATR regime detection & strategy routing | Not started | James Wee's page 3 formula; needs tuning time |
| Price charts (candlestick, VWAP overlay) | Not started | Valuable but not safety-critical |
| Per-instrument stop-losses (visible in dashboard) | Not started | Risk engine partially prepared |
| Heartbeat health dot on dashboard | Partially done | Bot emits heartbeat; dashboard wiring incomplete |
| Per-instrument pause/resume | Not started | Bot halt path exists; per-instrument granularity deferred |
| Portfolio exposure cap | Not started | Position limits exist; portfolio-level cap deferred |
| Market-making strategy | Disabled | Inventory skew bug (Derek's warning); skeleton exists |
| DRFT trading | Off | Consensus: too illiquid; bot moves its own price |
| Dark mode / mobile layout | Not started | Junior request; low demo value |
| Audit log (immutable, attributed) | Not started | Important for production; not critical for demo |
| Announcement impact analysis via LLM | Not started | Requires organizer LLM integration |

---

## Prioritisation Rationale

### Principle: Fix the engine first, then add the controls

The bot remediation plan identified P0 defects — runaway orders, silent feed death, unknown orders after timeout, and session state leaks — that are more dangerous than any missing feature. A kill switch is useless if the bot has already submitted 50 untracked orders or died silently.

### Priority Order

1. **P0 defects and must-fix bugs (Hour 1)** — SSE reconnect, shutdown, session lifecycle, order tracking. Without these, the bot is unsafe to run in scored windows.
2. **Dashboard must-fixes (Hour 1)** — SSE feed, stale values, SGT, dynamic instruments. Judges and stakeholders need a working monitoring surface.
3. **Kill switch + order manager hardening (Hour 2)** — One-click emergency halt satisfies the highest-priority stakeholder ask (Clarissa, Priya, Derek all agree). Order manager prevents runaway submissions.
4. **Dashboard features (Hour 2)** — Per-instrument P&L, order lifecycle, CSV export, session counters. These satisfy Gerald's export spec, Linda's audit needs, and Raymond's visibility requirements.
5. **Strategy expansion (Hour 2)** — Pairs (AXON following NOVA), crash guard, DCA. Incremental alpha without destabilising the core.
6. **AI/LLM features (deferred)** — Conversational panel and announcement analysis require organizer LLM integration and careful responsible-AI practice; deferred to avoid rushed, low-quality AI.

### Stakeholder Conflict Resolution

- **Kill switch UX**: Immediate, no confirmation (Priya and Derek agree; parameter changes get two-step confirmation separately)
- **DRFT**: Left off (Derek: "self-harm"; consensus: optional with informed risk, but not worth the demo risk)
- **Dashboard importance**: Retained despite Derek's dismissal — risk, operations, and executive stakeholders all depend on it
- **Strategy lookback**: Last N prices (feed timing is irregular; count-based is deterministic)
