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
| AI conversational panel | Sophia, Raymond | Descoped | Must use organizer LLM service (llm.codetoimpact.sg). Rushed AI with poor responsible-AI practice (no citations, hallucinated confidence) would score worse than no AI. The MCP read server and REST facade are ready for integration; the panel itself needs careful prompt engineering and uncertainty handling. |
| ATR regime detection | Adeline, James Wee (notes) | Descoped | Page 3 formula is clear but needs per-instrument threshold tuning and switching hysteresis rules that the notes don't specify. Incorrect regime detection would destabilise working strategies. |
| Price charts (candlestick/VWAP) | Raymond, Adeline | Descoped | No candles endpoint exists — must accumulate from SSE feed. Building a charting library from scratch in vanilla JS without external dependencies is high-effort, medium-value. Per-instrument P&L numbers provide the same analytical signal in less time. |
| Per-instrument stop-losses | Priya | Descoped | The risk engine has position limits and crash guard (15% drop protection). Full stop-losses with dashboard visibility require wiring through bot config, the control endpoint, and the dashboard — three-component change with testing burden. |
| Audit log (immutable) | Priya, Linda | Descoped | Important for production; not critical for a demo. Order lifecycle table and CSV export provide traceability for the scored windows. |
| DRFT trading | Winston (optional), Derek (cautious) | Descoped | Consensus: "tradeable with informed risk management" but the bot moves its own price in a thin market. The risk of adverse self-impact outweighs the marginal alpha in 30-minute scored windows. |
| Dark mode / mobile | Junior traders (chat) | Descoped | Cosmetic preferences with no functional impact. Time spent here is time not spent on safety or strategy. |
| Market-making | Derek | Disabled | Inventory skew bug acknowledged by Derek himself: "don't just uncomment James's module." Skeleton exists but is not safe to enable. |
| Full forecasting model | Sophia | Descoped | Unrealistic for a one-day build. Would require data collection, model training, evaluation, and responsible-AI labelling. |
| Orchestration / monitoring sidecar | Aaron | Descoped | Production infrastructure; unnecessary for demo. Three-process architecture (bot + dashboard + MCP) is sufficient. |

### What we kept that others might not have

| Decision | Why |
|----------|-----|
| Dashboard (despite Derek saying "doesn't matter") | Risk, operations, and executive stakeholders all depend on it. One trader's preference cannot erase the needs of the people who monitor risk and reconcile trades. |
| Fixed CSV export (Gerald's exact spec) | Gerald said "treat these as technical specs, not suggestions." The operations team depends on this format for reconciliation. |
| Session order counters (always visible) | Gerald's requirement — not just in the export, but on the dashboard. Small effort, high operational value. |
| Pairs strategy (AXON following NOVA) | Derek's strongest signal. Low implementation risk since it reuses the momentum framework with a cross-instrument trigger. |
| Crash guard + DCA overlay | Defensive risk feature that protects during scored windows. Better to have it and not need it. |
