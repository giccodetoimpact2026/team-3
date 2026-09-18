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

**5 active instruments, 3 strategy types:**

| Instrument | Strategy | Key Parameters |
|------------|----------|---------------|
| NOVA | Momentum | lookback=25, threshold=1.2%, qty=600, give-back exit |
| ELXR | Mean-reversion | lookback=15, deviation=0.4%, DCA 3 tranches, trend guard |
| CRVX | Momentum | lookback=15, threshold=0.75%, qty=900 |
| DRFT | Mean-reversion | lookback=15, asymmetric bands (buy=0.6%/sell=1.2%), qty=30 |
| AXON | Pairs-follow | follows NOVA, breakout arm=10, exit drawdown=1.2% |

**Core features:**
- **NOVA momentum strategy** — trades on price trend with configurable lookback and give-back exit
- **ELXR mean-reversion** — corrected 15-sample lookback (was 5; BUG-012), asymmetric deviation bands, trend guard
- **CRVX momentum** — independent momentum with tighter parameters for the more volatile instrument
- **DRFT mean-reversion** — conservative sizing (qty=30) for the thinnest market, asymmetric bands
- **AXON pairs-follow** — buys on NOVA breakout, sells on leader drawdown (energy→freight correlation)
- **Crash-buy strategy** — ready but unassigned; ladders into sharp sell-offs and sells the rebound. 18 dedicated tests. Available via one config change
- **SSE feed with supervised reconnect** — exponential backoff, jitter, Last-Event-ID replay, callback isolation, event worker queue decoupling SSE from trading
- **OrderManager** — per-process nonce idempotency keys, pending exposure tracking, one-active-order-per-symbol policy, anomaly throttle, cooldown
- **Session lifecycle** — GET /session bootstrap at startup, session boundary reset (clears price history on session ID change)
- **Graceful shutdown** — SIGINT/SIGTERM blocks new intents, cancels all resting orders
- **Emergency halt path** — reusable by signal handlers and the kill-switch endpoint (POST /halt on localhost:8092)
- **Order-book-aware sizing** — entry size adjusted by available depth; spread filter gates entry
- **Instrument-strategies framework** — modular BaseStrategy with routing

**Regime detection (enabled):**
- ATR-based price-only classifier producing 8 raw labels (WARMUP, COMPRESSION, RANGE, NEUTRAL, TREND_UP, TREND_DOWN, SHOCK_UP, SHOCK_DOWN)
- Adapter with hysteresis/debounce (`confirm_samples=3`) maps to 6 policy labels
- Policy table maps (strategy family, regime) → size multiplier [0, 1] — only reduces risk, never blocks sells
- Per-symbol overrides (DRFT gets tighter SHOCK handling)
- 71 dedicated regime tests

**Risk management:**
- Per-instrument position caps (NOVA=4000, ELXR=3000, CRVX=3500, AXON=4000, DRFT=400)
- Correlated-group notional cap: energy_freight (NOVA+AXON) capped at $1.2M
- Per-instrument exits: stop-loss on all 5; trailing take-profit on NOVA, CRVX, AXON; fixed take-profit on DRFT
- DCA overlay on all mean-reversion instruments (configurable `max_tranches` and `step_multiple`)
- Crash guard — 15% drop triggers sell into bids
- Order cooldown (1.5s), rate limiting

**News/sentiment engine:**
- Two-tier system: Tier 0 keyword classifier (instant) + Tier 1 async LLM refinement via organizer service
- Per-symbol bias with cooldown and TTL
- Budget-capped (60 LLM calls), memoised
- Dashboard controls for news parameters with two-step confirmation
- 53 dedicated tests

**Backtest engine:**
- Replay engine with configurable fill model
- Parameter sweep for offline strategy tuning
- Recording loader (JSONL feed capture)

**Observation mode** — sandbox-only trading unless live explicitly enabled

**337 bot tests passing** across 30 test files, CI via GitHub Actions

### Monitoring Dashboard (JavaScript)

**7-tab single-page application:**

1. **Overview** — total value/P&L cards, portfolio heat map, session order counters (buy/sell/partial/rejected), positions table, per-instrument P&L (from MCP or exchange fallback), live quotes (resting bids and asks), bot order trail
2. **Strategy** — per-instrument regime/strategy table, live news-strategy parameter controls with two-step confirmation
3. **Execution** — candlestick chart (1m/5m/15m buckets, canvas-rendered) with VWAP overlay, fill rate table, order lifecycle table
4. **Performance** — risk-adjusted analytics: trades, win %, profit factor, avg win/loss, per-trade Sharpe, total P&L
5. **Risk & Control** — net/gross exposure, max/current drawdown, correlation matrix, per-instrument controls
6. **Ops / Logs** — session summary (from MCP), trade log + CSV export, audit log
7. **Assistant** — placeholder for Phase-3 LLM conversational panel over MCP

**Core features:**
- **Real-time SSE feed** — replaces 30-second polling (Must-fix #1)
- **Stale-value handling** — clears to "Reconnecting..." on feed drop (Must-fix #4)
- **SGT timestamps everywhere** — YYYY-MM-DD HH:MM:SS.mmm SGT (Must-fix #7)
- **Dynamic instruments** — from GET /instruments; zero/dash for untraded (Must-fix #6)
- **Per-instrument P&L** — from MCP get_pnl with portfolio fallback and source labels
- **Session order counters** — buy, sell, partially filled, rejected
- **Order lifecycle table** — every status transition with Gerald's codes (OPEN→ACKNOWLEDGED)
- **Fixed-schema CSV export** — Gerald's column order, period decimals, SGT timestamps
- **Kill switch** — HALT BOT button, one click, no confirmation, with bot state pill
- **Candlestick + VWAP chart** — client-side from SSE feed, 1m/5m/15m bucket selection
- **Performance analytics** — win rate, profit factor, per-trade Sharpe, avg win/loss
- **Risk metrics** — net/gross exposure, max and current drawdown, correlation matrix
- **Live quotes** — resting bids and asks from order book
- **News parameter controls** — dashboard-driven strategy tuning with two-step confirmation
- **HTML escaping** — exchange strings sanitised before rendering
- **Key-injecting proxy** — API key never reaches the browser

**67 Node unit tests passing** across 5 test files, CI via GitHub Actions

### MCP Server (Python)
- Read-only tools: get_portfolio, get_positions, get_pnl, get_session_summary
- Dual surface: MCP protocol (streamable HTTP at /mcp) + REST facade for browser
- Health endpoint, CORS enabled

### Bug Fixes Completed
All 7 must-fix bugs resolved. 14 P0 defects from the remediation plan addressed.

### Test Coverage
- **Bot:** 337 tests across 30 test files (strategies, risk, orders, regime, news, lifecycle, backtest)
- **Dashboard:** 67 tests across 5 test files (analytics, backtest, format, trades, view)
- **MCP server:** dedicated test file
- **Total: 405+ tests**, CI on every push/PR

---

## What's Aspirational

| Feature | Status | Why Deferred |
|---------|--------|-------------|
| AI conversational panel (MCP + LLM) | Scaffold (tab 7) | Must use organizer LLM service; needs careful prompt engineering and responsible-AI practice |
| Per-instrument pause/resume | Not started | Bot halt path exists; per-instrument granularity deferred |
| Portfolio-level exposure cap | Not started | Position caps and correlated groups exist; portfolio-level cap adds marginal safety |
| Heartbeat health dot on dashboard | Partially done | Bot emits heartbeat; dashboard wiring incomplete |
| Announcement impact analysis via LLM | Not started | Requires organizer LLM integration and supply-chain reasoning |
| Dark mode / mobile layout | Not started | Junior request; low demo value |
| Market-making strategy | Disabled | Inventory skew bug (Derek's warning); skeleton exists |
| Audit log (immutable, attributed) | Not started | Important for production; order lifecycle table and CSV export cover demo needs |

---

## Prioritisation Rationale

### Principle: Fix the engine first, then add the controls

The bot remediation plan identified P0 defects — runaway orders, silent feed death, unknown orders after timeout, and session state leaks — that are more dangerous than any missing feature. A kill switch is useless if the bot has already submitted 50 untracked orders or died silently.

### Priority Order

1. **P0 defects and must-fix bugs (Hour 1)** — SSE reconnect, shutdown, session lifecycle, order tracking. Without these, the bot is unsafe to run in scored windows.
2. **Dashboard must-fixes (Hour 1)** — SSE feed, stale values, SGT, dynamic instruments. Judges and stakeholders need a working monitoring surface.
3. **Kill switch + order manager hardening (Hour 2)** — One-click emergency halt satisfies the highest-priority stakeholder ask (Clarissa, Priya, Derek all agree). Order manager prevents runaway submissions.
4. **Dashboard features (Hour 2)** — Per-instrument P&L, order lifecycle, CSV export, session counters, candlestick charts, performance analytics, risk metrics. These satisfy Gerald's export spec, Linda's audit needs, Raymond's visibility requirements, and Adeline's execution-quality tools.
5. **Regime detection + risk hardening (Hour 2–3)** — ATR regime classifier with hysteresis, policy-driven sizing, per-instrument exits (stop-loss, trailing take-profit), correlated-group caps. Reduces risk without destabilising strategies.
6. **Strategy expansion (Hour 3)** — Pairs (AXON following NOVA), crash guard, DCA, news/sentiment engine. Incremental alpha with defensive risk features.
7. **AI/LLM features (deferred)** — Conversational panel and announcement analysis require organizer LLM integration and careful responsible-AI practice; deferred to avoid rushed, low-quality AI. News sentiment engine uses the organizer LLM for Tier 1 refinement.

### Stakeholder Conflict Resolution

- **Kill switch UX**: Immediate, no confirmation (Priya and Derek agree; parameter changes get two-step confirmation separately)
- **DRFT**: Included with conservative sizing (qty=30, tightest position cap at 400) — addresses Derek's self-impact concern while capturing marginal alpha
- **Dashboard importance**: Retained despite Derek's dismissal — risk, operations, and executive stakeholders all depend on it
- **Strategy lookback**: Last N prices (feed timing is irregular; count-based is deterministic)
- **Regime detection**: Implemented with hysteresis to prevent rapid switching — addresses Adeline's request without the instability risk that initially caused descoping
- **News engine**: Two-tier approach lets keyword classifier run instantly while LLM refinement happens async — avoids blocking trading on LLM latency
