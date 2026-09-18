# Feature backlog: acceptance checklists

Candidate work, grouped by user need: explicit stakeholder asks, technical requirements and optional enhancements. Inclusion or priority does not make every item mandatory; priorities are our recommendations, not the judging rubric.

The seven agreed bug fixes are in [must-fixes.md](must-fixes.md). Complete that foundation before expanding scope.

## How to read this backlog

**Priority** is the only ranking:

- 🔴 **High:** core safety, correctness or essential operational capability.
- 🟠 **Medium:** useful improvements once the foundation is reliable.
- 🟢 **Low:** optional polish or ambitious work if time permits.

Conditional work applies only when its parent feature is built; engineering practices apply throughout development. Conflicting statements and scope corrections are in section 7; features proposed for deferral are in section 8.

Each source file is linked directly, with the relevant interview, page or handover section noted beside it.

Mark each AC ☐ → ☑ once verified. These are proposed checks derived from the sources. Agree any unresolved policy, threshold, window or metric convention before implementation.

## 1. Risk and human control

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Emergency kill switch | “one button, always visible, immediate, no confirmation dialog” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Clarissa interview; reinforced at 00:02 | 🔴 High | ☐ **1.** One click, without confirmation, blocks new submissions before cancellation begins. ☐ **2.** Outstanding-order cancellation results and failures are displayed. ☐ **3.** Existing positions follow the documented halt policy. |
| Per-instrument stop-losses | “Per-instrument stop-losses, and I want them visible in the dashboard” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Priya interview | 🔴 High | ☐ **1.** Each instrument’s configured loss limit is visible. ☐ **2.** A test loss crossing that limit triggers the documented bot action. ☐ **3.** A loss below it does not. |
| Confirm parameter changes | “One click to propose, one to confirm” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Priya interview | 🔴 High | ☐ **1.** When settings are editable, users see old/new values before confirming. ☐ **2.** Unconfirmed or invalid changes do not take effect. ☐ **3.** Successful changes record actor, time and values. |
| Per-instrument pause/resume | “sometimes NOVA's fine and ELXR is the problem” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Clarissa interview | 🟠 Medium | ☐ **1.** After pause acknowledgement, the selected instrument submits no new orders while other instruments continue. ☐ **2.** Resume restores eligibility. ☐ **3.** Open orders follow the documented pause policy. |
| Risk and silence alerts | “portfolio drawdown passes 15% of starting capital” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Priya interview | 🔴 High | ☐ **1.** Test data crossing the agreed portfolio-loss or instrument-loss threshold raises an alert. ☐ **2.** More than 15 seconds without a heartbeat raises a bot-silence alert. ☐ **3.** Each alert identifies its cause. |
| Abnormal order-rate protection | “auto-pauses and alerts if the bot suddenly fires far more orders than normal” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Clarissa interview | 🟠 Medium | ☐ **1.** Exceeding the configured order-count threshold within its configured window pauses the affected trading scope and raises an alert showing the count, threshold and reason. |
| Correlation and net exposure | “if we're long both we've doubled a position” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Clarissa; Farrukh interviews | 🟠 Medium | ☐ **1.** A known dataset produces the expected net exposure and return correlations. ☐ **2.** The dashboard labels the observation window and shows insufficient data when correlation cannot be calculated. |

## 2. Trading strategy

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Regime detection and strategy routing | “detect the market regime and switch behaviour” | [james-wee-strategy-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/james-wee-strategy-notes.md) — page 3; Adeline interview in [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) | 🟠 Medium | ☐ **1.** Known price sequences produce the expected ATR percentage and regime. ☐ **2.** Low volatility disables momentum. ☐ **3.** High volatility permits it. ☐ **4.** Neutral reduces size. ☐ **5.** Switching follows the configured confirmation rule. |
| Instrument-specific configuration | “Don't run one instrument's parameters on another” | [james-wee-strategy-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/james-wee-strategy-notes.md) — pages 1 and 3 | 🔴 High | ☐ **1.** Each instrument uses its own lookback, thresholds and size limits. ☐ **2.** Changing one instrument’s settings leaves the others unchanged. ☐ **3.** Invalid settings are rejected. |
| Market-making | “tighten the ask when you're long, tighten the bid when you're short” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Derek; [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) — bot handover | 🟠 Medium | ☐ **1.** With other inputs fixed, a long inventory tightens the ask and a short inventory tightens the bid relative to neutral inventory. ☐ **2.** Quotes respect configured price and size limits. |
| AXON following NOVA | “NOVA breaks a new high, go long AXON” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Derek; [james-wee-strategy-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/james-wee-strategy-notes.md) — page 4 | 🟠 Medium | ☐ **1.** A NOVA price above the previous high over the configured lookback generates an AXON buy proposal. ☐ **2.** No breakout generates no proposal. ☐ **3.** Failed risk checks prevent submission. |
| CRVX momentum | “CRVX momentum (growth behaviour)” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Derek interview | 🟠 Medium | ☐ **1.** CRVX uses its configured momentum lookback and threshold. ☐ **2.** Test moves above/below the positive/negative thresholds produce buy/sell proposals. ☐ **3.** Moves inside the thresholds produce neither. |
| Optional DRFT trading | “Tradeable, with informed risk management?” followed by agreement | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — alignment 00:11; Winston interview | 🟢 Low | ☐ **1.** When disabled, no DRFT orders are submitted. ☐ **2.** If enabled, every order respects a documented smaller size cap than NOVA. ☐ **3.** The team records its sizing and market-impact rationale. |

### Market condition detection

Source: [james-wee-strategy-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/james-wee-strategy-notes.md), page 3.

```text
TR = abs(current_price - previous_price)
ATR = average(last 14 TR values)
ATR_pct = ATR / current_price

ATR_pct < low threshold  -> suspend momentum; switch strategy or reduce size
ATR_pct > high threshold -> allow momentum
Otherwise               -> retain posture, reduce size
```

Starting thresholds: low `0.0015`, high `0.004`, tuned per instrument. Fourteen changes need fifteen prices. Add switching confirmation/hysteresis; the notes do not specify an exact rule. This measures volatility, not trend direction.

Instrument expansion order: stabilise NOVA/ELXR first, consider AXON/CRVX next, DRFT optional — [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md), Winston interview.

## 3. Dashboard and trading visibility

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Per-instrument profit/loss and positions | “per-instrument P&L, live, at a glance” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Raymond interview | 🔴 High | ☐ **1.** The dashboard represents every discovered instrument and matches the latest MCP account result. ☐ **2.** Source and update time are visible. ☐ **3.** Unavailable or stale account data is labelled. |
| True bot health | “emit every ~5s, treat >15s as dead” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Yuhui submission; dashboard handover in [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) | 🔴 High | ☐ **1.** The bot emits a heartbeat approximately every 5 seconds. ☐ **2.** More than 15 seconds without one changes its displayed health to dead, even if the exchange remains reachable. |
| Candles and trade markers | “1m/5m/15m selectable, switchable per instrument” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Adeline; Raymond asks for trade markers | 🟠 Medium | ☐ **1.** Users can select an instrument and 1m/5m/15m intervals. ☐ **2.** Known price events produce correct session candle values. ☐ **3.** Recorded trades appear at their timestamps and prices. ☐ **4.** Gaps/incomplete candles are labelled. |
| VWAP overlay | “my primary execution-quality metric” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Adeline interview | 🟠 Medium | ☐ **1.** Known trade prices and volumes produce sum(price × volume) / sum(volume) for the labelled window. ☐ **2.** Missing or zero total volume shows unavailable rather than an invented value. |
| Fill-rate table | “orders sent, fully filled, partially filled, rejected, fill rate %” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Adeline interview | 🟠 Medium | ☐ **1.** Per-instrument submitted, fully filled, partially filled and rejected counts match test order records. ☐ **2.** Fill-rate percentage matches the displayed formula and handles a zero denominator. |
| Portfolio heat map | “colour telling him instantly which instruments are winning and losing” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Raymond interview | 🟠 Medium | ☐ **1.** Each instrument’s displayed P&L matches the account view. ☐ **2.** Gains, losses and zero values are distinguishable by text/numbers as well as colour. |
| Dark mode and mobile layout | “dark mode” and “it should work on mobile” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — informal junior chat; dark-mode stub in [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) | 🟢 Low | ☐ **1.** The theme toggle changes the dashboard palette. ☐ **2.** At an agreed mobile viewport, key values and controls remain readable and usable without clipping. ☐ **3.** The kill switch stays accessible. |

## 4. Operations, audit and reporting

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Order lifecycle view | “every order's journey, not just its outcome” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Linda interview | 🔴 High | ☐ **1.** Test order events appear with the exact status codes specified below and SGT timestamps. ☐ **2.** Partial fills update filled/remaining quantity without being shown as full fills. |
| Human-action audit log | “timestamped, attributed, immutable” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Priya; Linda interviews | 🔴 High | ☐ **1.** Every parameter change and manual action records actor, timestamp, action and result, plus old/new values where applicable. ☐ **2.** Records survive restart and cannot be edited/deleted through application interfaces. ☐ **3.** Storage guarantees are documented. |
| Fixed-schema CSV export | “Treat these as technical specs, not suggestions” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Gerald email | 🔴 High | ☐ **1.** A downloaded CSV matches the column order, status codes, timestamp format and decimal separator specified below. ☐ **2.** Exported values reconcile with the same session’s order records. |
| Session counts | “always visible on the dashboard, not just in the export” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Gerald email | 🔴 High | ☐ **1.** Buy-order, sell-order, partially-filled and rejected counts remain visible and match the current session’s records. ☐ **2.** Replayed updates do not double-count an order. |
| Session summary | “did the bot make money, how much, which instruments, was it stable” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Winston; Linda interviews | 🔴 High | ☐ **1.** One screen shows session P&L, trade count, best/worst instrument, triggered alerts and health incidents. ☐ **2.** Values reconcile with underlying session records. ☐ **3.** No-trade sessions display explicitly. |
| Performance breakdown | “win rate, average profit on winners, average loss on losers, profit factor” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Farrukh interview | 🟠 Medium | ☐ **1.** A known set of matched trades produces the expected P&L, trade count, win rate, average winner/loser and profit factor. ☐ **2.** Trade-matching rules are documented. ☐ **3.** Undefined ratios show unavailable. |
| Sharpe and information ratio | “give me a Sharpe per instrument”; “beating the benchmark” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Raymond; Priya interviews | 🟢 Low | ☐ **1.** Results match a known return dataset under documented sampling, risk-free-reference and benchmark conventions. ☐ **2.** Missing inputs, insufficient observations or zero denominators show unavailable. |

### Fixed export specification

Column order:

```text
TradeID,Timestamp_SGT,Instrument,Direction,Quantity,LimitPrice,FillPrice,FillQuantity,RemainingQty,Status,PnLImpact,CumulativePnL
```

Allowed statuses:

```text
PENDING | ACKNOWLEDGED | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED
```

Timestamp: `YYYY-MM-DD HH:MM:SS.mmm SGT`. Numeric decimal separator: period (`.`).

Confirm identifier mapping, row granularity and P&L accounting against the supplied API before implementing the export.

## 5. AI assistance and research

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Account Q&A through MCP | “read-only MCP server provided” and “one working tool call” | [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) — dashboard and MCP handovers | 🟠 Medium | ☐ **1.** Supported account questions invoke the relevant supplied MCP read tool. ☐ **2.** Answers match returned values and display source and as_of. ☐ **3.** Tool failures produce an explicit unavailable response. |
| Explain a trade | “When the bot sells DRFT at 14:32, I want to know why” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Raymond interview | 🟠 Medium | ☐ **1.** A question about a recorded trade identifies its actual strategy, signal, threshold and risk decision from bot records. ☐ **2.** If those records are missing, the answer states that the reason is unavailable. |
| Sources and uncertainty in AI answers | “Citations and confidence, always” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Sophia; alignment 00:41 | 🔴 High | ☐ **1.** Factual account figures cite tool results and their timestamps. ☐ **2.** Estimates are labelled with their uncertainty basis. ☐ **3.** Stale/missing data is disclosed and unsupported confidence percentages are not presented. |
| Announcement impact analysis | “reason across the supply chain” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Sophia; announcement TODO in [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) | 🟠 Medium | ☐ **1.** A test announcement produces affected instruments, direct/indirect impact reasoning, source and uncertainty. ☐ **2.** Advisory mode places no orders. ☐ **3.** Any enabled trading path passes ordinary bot risk checks. |
| Short-horizon forecast or tuning suggestion | “clearly labelled as a model estimate, never as fact” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Sophia interview | 🟢 Low | ☐ **1.** Each output is labelled as an estimate with horizon, inputs, method and uncertainty. ☐ **2.** An evaluation report compares predictions with held-out outcomes. ☐ **3.** Unavailable evidence is disclosed. |
| AI-triggered pause/resume/kill | “Control tools ... exist but return a 'not wired' notice” | [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) — MCP handover | 🟢 Low | ☐ **1.** Each enabled control tool reaches the bot and reports its acknowledged result or failure. ☐ **2.** Paused/killed scopes stop new submissions. ☐ **3.** The ordinary dashboard kill switch works without an LLM. |

## 6. Engineering quality

| Feature | Source statement | Source file | Priority | Acceptance criteria (ACs) |
|---|---|---|---|---|
| Classified errors and safe retries | “no classification, no retry” | [repo-handover-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/repo-handover-notes.md) — bot handover | 🔴 High | ☐ **1.** Simulated network failures produce classified logs and configured backoff retries. ☐ **2.** Uncertain order submissions are reconciled before retry. ☐ **3.** The test produces no duplicate order or silently dead listener. |
| Modular strategies | “a BaseStrategy with subclasses, selected by a router” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Aaron; [james-wee-strategy-notes.md](https://github.com/giccodetoimpact2026/welcome/blob/master/james-wee-strategy-notes.md) — page 3 | 🔴 High | ☐ **1.** Each enabled strategy conforms to BaseStrategy and can be tested independently. ☐ **2.** Routing selects the configured strategy/regime. ☐ **3.** Adding a strategy does not require embedding its formula in the main loop. |
| Tests, CI and reproducibility | “four features done cleanly” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Marcus interview | 🔴 High | ☐ **1.** A clean checkout installs pinned dependencies and starts through the documented command. ☐ **2.** Pushes run lint and relevant core-logic tests. ☐ **3.** Failures fail CI. ☐ **4.** Commits identify the changes they contain. |
| Reviewed AI-generated code | “Use it spec-first and review what it gives you” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Marcus; alignment 00:41 | 🔴 High | ☐ **1.** For each AI-assisted change, the repository records the intended behaviour, human review and relevant verification results. ☐ **2.** The team can explain and trace the accepted implementation. |
| Sandbox/live separation | “Don't test against prod” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Marcus interview | 🔴 High | ☐ **1.** Sandbox and live runs use distinct configuration/credentials and visibly identify their environment. ☐ **2.** Automated tests select sandbox settings and cannot submit orders to the live endpoint. |

## 7. Conflicts and corrections

These need interpretation or correction, not postponement. Some are conflicting opinions; others are technical errors or scope misunderstandings.

| Statement or idea | Source file | Our resolution |
|---|---|---|
| DRFT is fine at normal size | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — junior post-meeting note | Contradicted by Derek's direct interview and final alignment. Do not adopt this summary. |
| DRFT must be hard-blocked | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Clarissa interview | Superseded by agreement at 00:11: optional trading with informed risk management. |
| Confirmation for parameter changes versus emergency halt | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Priya interview | Scope clarification, not a stakeholder contradiction: parameter changes require confirmation; emergency halt explicitly does not. |
| The dashboard does not matter | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Derek interview | One user's preference cannot erase risk, operations and executive requirements. |
| Use market orders | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — junior chat | Unsupported: Yuhui specifies limit orders only, with no `type` field. |
| Just restart a crashed script | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — junior chat | Does not satisfy reconnect, replay, shutdown or order-reconciliation needs. |
| Rebalance to target portfolio weights | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Farrukh interview | Initial misunderstanding; this is an active intraday trading system. |

## 8. Deferred features

Proposed exclusions from the initial one-day build, based on effort or limited demo value. These ideas are not necessarily wrong; the team can revisit them if time and the final brief justify it.

| Feature or request | Source file | Our resolution |
|---|---|---|
| Full forecasting model and months of backtesting | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Sophia interview | Defer the full research programme; consider only a smaller, evaluated estimate if suitable data and time are available. |
| Orchestration, ORM, monitoring sidecar, fixed 70% coverage | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Aaron interview | Defer the production infrastructure package and fixed coverage target; retain essential tests and only infrastructure needed to run the demo. |
| Internal infrastructure-as-code framework and full end-to-end coverage | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Marcus interview | Defer the internal framework and full coverage goal; retain reproducible setup and focused checks. |
| Bloomberg styling, bigger green dot, “bot mood” | [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md) — Raymond interview and junior chat | Defer cosmetic imitation and novelty features; retain readable information and genuine bot health. |

## Decisions to settle after receiving the team package

1. What does the actual rubric reward, and how much time/team capacity is available?
2. How should account data refresh through MCP, and what event fields are available for candles/VWAP?
3. What exactly do stop-loss, pause, kill and resume do to outstanding orders and existing positions?
4. What definitions apply to P&L, drawdown, trade count, fill rate and the benchmark?
5. Which bot-state/control channel fits the supplied repositories?
6. Which two or three enhancements will the team finish and demonstrate after the core repairs?
