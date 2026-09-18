# Implementation Specification

## Requirement Interpretation

There is no clean specification. Requirements were derived from 12+ stakeholder interviews, a cross-functional meeting transcript, a post-meeting note, an email submission, and an informal chat thread. Stakeholders do not agree, and not everything they say is correct.

### How We Read the Stakeholders

| Stakeholder | Role | Key Ask | Our Interpretation |
|-------------|------|---------|-------------------|
| Raymond Chew | CIO | Per-instrument P&L, charts, "Bloomberg feel", trade explainability | Per-instrument P&L is genuine and high priority. Charts are valuable but secondary to safety. "Bloomberg feel" is aspirational polish, not a functional requirement. |
| Priya Sundaram | Deputy CIO | Stop-losses, audit log, alerts, two-step confirmation, information ratio | Stop-losses and audit are genuine risk needs. Two-step confirmation applies to parameter changes only — she explicitly excludes emergency halt. Information ratio is a nice-to-have. |
| Derek Tan | Senior Trader (strategy) | Trade more instruments, AXON pairs off NOVA, CRVX momentum, DRFT caution | Instrument expansion is real alpha. AXON-NOVA correlation is the quant's strongest signal. DRFT warning is load-bearing — "the bot moves its own price." |
| Adeline Koh | Senior Trader (execution) | Charts (1m/5m/15m), VWAP overlay, fill rates, regime detection | Charts and VWAP are genuine execution-quality tools. Regime detection references James Wee's page 3 — real strategy value. |
| Clarissa Ng | Senior Trader (risk) | Kill switch (one click, no confirmation, always visible), per-instrument pause, anomaly detection | Kill switch is the single most agreed-upon requirement across all stakeholders. Per-instrument pause is genuine but secondary. |
| Aaron Yap | Head of Tech | SSE not polling, process isolation, modular strategies | Technical requirements, not opinions. SSE is a must-fix. Process isolation is already the architecture. Modular strategies improve maintainability. |
| Yuhui Tan (Gerald) | Enterprise Architect | Precise API/SSE/MCP/format facts, fixed CSV schema, session counts | Treat as technical specs, not suggestions. His format requirements are exact and testable. |
| Marcus Lee | Engineering Lead | Tests, CI, reproducibility, AI-assisted dev discipline | "Four features done cleanly beat ten half-finished ones." Engineering quality is graded. |
| Sophia Chen | Investment Analyst | LLM-assisted signals, responsible AI, citations and confidence | AI features must use the organizer LLM service. Responsible AI practice (citations, uncertainty labelling) is non-negotiable for any AI output. |
| Linda Goh | Trade Operations | Order lifecycle, SGT timestamps, audit trail | Operational needs — these are the people who reconcile trades. SGT is a must-fix. |
| Farrukh Ahmad | Portfolio Manager | Performance breakdown, correlation, win rate | Analytical depth. Valuable for presentation but secondary to operational safety. |
| Winston Lam | Deputy COO | "Works, doesn't crash, can be stopped" | The executive summary of what matters: reliability > features. |

### Signals vs Noise

**Signal (genuine, load-bearing requirements):**
- Kill switch: immediate, no confirmation — agreed by risk, strategy, and operations
- SSE feed, not 30s polling — technical fact, not opinion
- SGT timestamps — operational requirement from the people who reconcile trades
- Per-instrument P&L — CIO, portfolio manager, and operations all need it
- Fixed CSV export schema — Gerald's spec is exact and testable
- Graceful shutdown with order cancellation — safety-critical
- Session lifecycle awareness — without it, stale state leaks between trading windows

**Noise (personal preference, scope inflation, or misunderstanding):**
- "Bloomberg-like feel" — aspirational aesthetics, not a functional spec
- Market orders — unsupported by the exchange (Yuhui: limit orders only, no `type` field)
- "Just restart a crashed script" — does not satisfy reconnect, replay, or order reconciliation
- DRFT at normal size — contradicted by Derek's direct interview and the final alignment
- Rebalance to target portfolio weights — this is an active intraday trading system, not a portfolio rebalancer
- 70% test coverage target — a fixed number is arbitrary; focused correctness tests are more valuable
- Full forecasting model with months of backtesting — unrealistic for a one-day build

---

## Target User

**Primary: the trader running the team's intraday bot during scored trading windows.**

This person needs:
1. Confidence the bot is running and connected (health, feed status)
2. Ability to stop everything immediately if something goes wrong (kill switch)
3. Visibility into what the bot is doing (positions, P&L, orders, counters)
4. Exportable records for post-session reconciliation (CSV, order lifecycle)

**Secondary: the risk desk (Priya, Clarissa) monitoring across instruments.**

They need:
1. Per-instrument P&L at a glance
2. Emergency halt capability
3. Awareness of bot state (alive/halted/reconnecting)

**Tertiary: judges and mentors evaluating the submission.**

They need:
1. Clean, version-controlled code with meaningful commits
2. Working CI with passing tests
3. Evidence of thoughtful requirement interpretation and prioritisation
4. Defensible descoping decisions

---

## Descoping Decisions

### Descoped with rationale

| Feature | Stakeholder | Decision | Rationale |
|---------|------------|----------|-----------|
| AI conversational panel | Sophia, Raymond | Descoped | Must use organizer LLM service (llm.codetoimpact.sg). Rushed AI with poor responsible-AI practice (no citations, hallucinated confidence) would score worse than no AI. The MCP read server and REST facade are ready for integration; the panel itself needs careful prompt engineering and uncertainty handling. Dashboard has an Assistant tab placeholder. |
| Per-instrument pause/resume | Clarissa | Descoped | Bot halt path exists; per-instrument granularity requires wiring per-symbol state through the control server and dashboard — deferred in favour of the all-instruments kill switch. |
| Portfolio-level exposure cap | Priya | Descoped | Position caps and correlated-group notional limits exist. A single portfolio-level hard cap adds marginal safety over the existing layered limits. |
| Dark mode / mobile | Junior traders (chat) | Descoped | Cosmetic preferences with no functional impact. Time spent here is time not spent on safety or strategy. |
| Market-making | Derek | Disabled | Inventory skew bug acknowledged by Derek himself: "don't just uncomment James's module." Skeleton exists (`market_making.py.DISABLED`) but is not safe to enable. |
| Full forecasting model | Sophia | Descoped | Unrealistic for a one-day build. Would require data collection, model training, evaluation, and responsible-AI labelling. |
| Orchestration / monitoring sidecar | Aaron | Descoped | Production infrastructure; unnecessary for demo. Three-process architecture (bot + dashboard + MCP) is sufficient. |

### Originally descoped, then delivered

| Feature | Stakeholder | What we shipped |
|---------|------------|----------------|
| ATR regime detection | Adeline, James Wee | Fully implemented with three modules: raw 8-label classifier (`regime.py`), adapter with hysteresis/debounce (`regime_adapter.py`), and policy table mapping (family, regime) → size multiplier (`regime_policy.py`). Enabled in config with `confirm_samples=3`. 71 dedicated tests. |
| Price charts (candlestick/VWAP) | Raymond, Adeline | Client-side candlestick chart built from SSE feed in vanilla JS canvas. 1m/5m/15m bucket selection. VWAP overlay calculated from trade data. Available in the Execution tab. |
| Per-instrument stop-losses | Priya | Risk engine `exits.py` implements per-instrument stop-loss, fixed take-profit, and trailing take-profit (arm + trail). Configured per instrument in `config.yaml`. |
| DRFT trading | Winston, Derek | DRFT is assigned a mean-reversion strategy with conservative parameters (qty=30, asymmetric buy=0.006/sell=0.012 deviation bands). Regime policy reduces DRFT sizing in SHOCK conditions. Position cap set at 400 — the tightest of all instruments. |
| News/sentiment analysis | Sophia | Two-tier news engine: Tier 0 keyword classifier (instant, no LLM) + Tier 1 async LLM refinement via organizer service. Per-symbol bias with cooldown and TTL. Dashboard controls for news parameters with two-step confirmation. 53 dedicated tests. |

### What we kept that others might not have

| Decision | Why |
|----------|-----|
| Dashboard (despite Derek saying "doesn't matter") | Risk, operations, and executive stakeholders all depend on it. One trader's preference cannot erase the needs of the people who monitor risk and reconcile trades. |
| Fixed CSV export (Gerald's exact spec) | Gerald said "treat these as technical specs, not suggestions." The operations team depends on this format for reconciliation. |
| Session order counters (always visible) | Gerald's requirement — not just in the export, but on the dashboard. Small effort, high operational value. |
| Pairs strategy (AXON following NOVA) | Derek's strongest signal. Low implementation risk since it reuses the momentum framework with a cross-instrument trigger. |
| Crash guard + DCA overlay | Defensive risk feature that protects during scored windows. Better to have it and not need it. DCA implemented on all mean-reversion instruments with configurable `max_tranches` and `step_multiple`. |
| Crash-buy strategy (ready, unassigned) | Built and tested (18 tests) for V-recovery scenarios like the Team 11 CRVX crash. Deliberately left unassigned in config — available to enable with one config change if the market warrants it. |
| Backtest engine | Parameter sweep capability with replay engine, configurable fill model, and recording loader. Allows offline validation of strategy parameters before live windows. |
