# CODETOIMPACT2026 — Execution Plan (Do It In This Order)

A single, ordered checklist. **Top to bottom = the order to build in.**
Phase 1 = fix the broken system. Phase 2 = extend it. Phase 3 = differentiate.
Don't start a phase until the one above is solid.

Legend: ⬜ = to do · **Who** = who asked for it · **Why it's here** = the reason it's at this priority.

---

# PHASE 1 — FIX THE BUGS FIRST (the floor)

These are the **7 must-fix items** everyone agreed on. They're graded-baseline and mostly fast. Do them before any new feature — a shiny feature on a crashing bot scores nothing.

### ⬜ 1.1 — ELXR lookback: change 5 → 15  🔴 *do this first, it's a one-line high-value fix*
- **Who:** James's notes (Page 2) + Adeline. In the bug tracker for 2 months.
- **What:** Mean-reversion lookback is `5`, must be `15`.
- **Why it matters:** With 5 it reads a downtrend as a run of "dips" and buys into a falling market. 15 smooths the average so a real trend is visible.
- **Done when:** ELXR no longer buys every small dip during a clear downtrend.

### ⬜ 1.2 — Missing-instrument crash → default to zero
- **Who:** Alex (bot handover) + Sarah (dashboard).
- **What:** Portfolio call crashes if a symbol hasn't traded yet and is absent from the response; dashboard shows `undefined` cells.
- **Fix:** Initialise all instruments to zero on load, then overlay the response.
- **Why here:** Blocks you from adding any new instrument — you'll hit this the moment you add a 3rd name. Unblock it early.

### ⬜ 1.3 — SSE reconnect (bot): stop dying on disconnect
- **Who:** Alex + Aaron. Caused 2 crashes last month.
- **What:** SSE feed thread dies silently on network disconnect, no reconnect.
- **Fix:** Detect death, restart the stream with the `Last-Event-ID` header (exchange replays what you missed), retry with backoff — "never give up" (Derek).
- **Why here:** Reliability is the whole point; the bot must survive the network.

### ⬜ 1.4 — Graceful shutdown (bot): cancel open orders on exit
- **Who:** Alex (half-built) + Aaron.
- **What:** Ctrl-C leaves live orders on the exchange.
- **Fix:** Finish the SIGTERM handler — cancel open orders, then exit cleanly.
- **Why here:** Safety; also a prerequisite for the kill switch in Phase 2.

### ⬜ 1.5 — Real error handling (bot): no more silent swallowing
- **Who:** Alex + Aaron.
- **What:** Main loop and SSE thread use bare `try/except` with no logging, no classification, no retry.
- **Fix:** Real exception handling — classify errors, log them, retry with backoff where sensible.
- **Why here:** Everything downstream depends on the bot not failing invisibly.

### ⬜ 1.6 — Dashboard: replace 30s REST polling with the SSE live feed
- **Who:** Sarah + Aaron ("if I see `setInterval(fetch, 30000)` I will physically wince").
- **What:** Dashboard polls `/portfolio` every 30s.
- **Fix:** Subscribe to the exchange SSE feed via native `EventSource` (fetch-stream fallback for custom-header quirks).
- **Why here:** Core dashboard requirement; unlocks the live charts later.

### ⬜ 1.7 — Dashboard: stale data on reconnect
- **Who:** Sarah.
- **What:** Old values linger on the screen after a reconnect.
- **Fix:** Clear the DOM at the start of the refresh, before new data resolves.

### ⬜ 1.8 — Timestamps: UTC → SGT, millisecond precision
- **Who:** Linda (emphatic), Gerald, Sarah.
- **What:** Dashboard uses `toUTCString()`.
- **Fix:** `new Date().toLocaleString('en-SG', { timeZone: 'Asia/Singapore' })`; append `" SGT"`; format `YYYY-MM-DD HH:MM:SS.mmm SGT`; period `.` as decimal separator.
- **Why here:** Cheap, and every log/export downstream depends on it being right.

> ✅ **Phase 1 gate:** bot runs without crashing across a disconnect, exits cleanly, ELXR behaves, dashboard is live in SGT with no stale/undefined cells. Only now move on.

---

# PHASE 2 — IMPROVE / EXTEND THE SYSTEM (the substance)

This is where most of the score lives. Ordered by value + dependency. **Marcus's rule governs everything below: 4 features done cleanly (tests, git history, CI) beat 10 half-built.** Don't over-commit.

## 2A — Safety & control (do these first in Phase 2)

### ⬜ 2.1 — Kill switch  🔴 *highest-value single feature*
- **Who:** Clarissa (#1 ask), agreed by all. "One button, always visible, immediate, NO confirmation."
- **What:** One control that cancels all open orders and halts the bot instantly.
- **Note:** Emergency halt takes **no** confirm dialog. (Contrast with 2.3.)
- **Depends on:** 1.4 (graceful shutdown) + a bot→dashboard control channel.

### ⬜ 2.2 — Per-instrument pause / resume
- **Who:** Clarissa. "Sometimes NOVA's fine and ELXR is the problem."
- **What:** Pause/resume a single instrument without stopping everything.

### ⬜ 2.3 — Two-step confirm for parameter changes
- **Who:** Priya. "One click to propose, one to confirm."
- **What:** Any limit/threshold change is two-step. **Distinct from the kill switch** — stopping is instant; *changing a number* is two-step.

### ⬜ 2.4 — Order-rate anomaly auto-pause
- **Who:** Clarissa. The "March incident" — strategy looped, fired dozens of orders in seconds.
- **What:** If order rate spikes far above normal, auto-pause + alert.

## 2B — Strategy build-out (the trading substance)

> Architecture: `BaseStrategy` + subclasses + a **router** — NOT a giant if-else in `main.py` (James + Aaron).
> Startup: call `GET /instruments` — don't hardcode names (Yuhui).

### ⬜ 2.5 — NOVA momentum, wide lookback
- Already running; confirm it uses a wide lookback and per-instrument config.

### ⬜ 2.6 — CRVX momentum
- **Who:** Derek. Growth name, behaves like NOVA → momentum.
- **Edge:** Reuses the NOVA momentum engine with per-instrument params — a whole new instrument trading for near-zero marginal code. Cheap breadth, and it proves the router/`BaseStrategy` design actually generalises.

### ⬜ 2.7 — AXON pairs off NOVA
- **Who:** Derek + James (Page 4). Energy leads freight: NOVA breaks a new high → go long AXON.
- **Edge:** This is the one strategy that isn't just "momentum on another name" — it trades a *relationship* (energy leads freight). Implementing it shows you read the desk's actual market structure, not just the code. It's also the visible payoff of the correlation work (2.19): the same NOVA↔AXON link is a *signal* here and a *risk* there.

### ⬜ 2.8 — Market-making on the liquid names (ELXR / AXON)
- **Who:** Derek. **Fix the inventory bug** in the disabled module: tighten the **ask when long**, tighten the **bid when short** (skeleton has it backwards). File: `strategies/market_making.py.DISABLED`.
- **Edge:** This is a *diagnosis*, not just a feature — the module was disabled for a real, named reason and you fixed the exact bug Derek called out. Re-enabling it correctly is a stronger signal than writing something new: it proves you can read a broken system, find the root cause, and revive it. Adds a third strategy *type* (beyond momentum/mean-reversion), showing the router handles genuinely different behaviours.

### ⬜ 2.9 — Regime detection (ATR)  🔴 *"where the real edge is"*
- **Who:** James (Page 3), Adeline. Never implemented.
- **What:** Runs *before* strategy selection; uses ATR% as a volatility proxy to suspend momentum in flat markets and reduce size in neutral ones.
- **Formula:**
  ```
  TR_t    = | price_t - price_(t-1) |
  ATR     = average(last M TR values)   (M ~ 14)
  ATR_pct = ATR / price_t
  low-vol  (< ~0.0015) -> suspend momentum / mean-revert / cut size
  high-vol (> ~0.004)  -> momentum active
  neutral               -> hold posture, reduce size
  ```
- Thresholds are **per-instrument**; add **hysteresis** so it doesn't flip-flop on the boundary.
- **Why it wins:** This is the only feature that makes the bot *smarter*, not just bigger — it's the difference between a bot that blindly runs momentum and one that knows *when its own strategy stops working* and stands down. James called it "where the real edge is"; Adeline flagged it too and said the last team ducked it as "too complex." Implementing it well is the clearest way to out-build the field, and it visibly feeds the Strategy tab's live regime state.

### ⬜ 2.10 — DRFT: tiny size + risk guardrails (NOT normal size, NOT hard-blocked)
- **Who:** Resolved live on the call — tradeable with informed risk management.
- **What:** Size ~5% / a fifth of NOVA, with a clear risk warning. Ignore the junior post-meeting note claiming "normal size" — it's the planted error.

## 2C — Dashboard, logging & reporting

### ⬜ 2.11 — One health/summary screen (30-second glance)
- **Who:** Winston (his only personal ask). Did it make money, how much, which instruments, was it stable.
- **Also:** heartbeat-based health (emit ~5s, dead if >15s) — not a REST-ping dot (Yuhui).

### ⬜ 2.12 — Per-instrument P&L + running totals
- **Who:** Raymond, Gerald. Buy/sell orders, partially filled, rejected — always visible, not just in export.

### ⬜ 2.13 — Candlestick charts (build from SSE, no candles endpoint)
- **Who:** Adeline. Session-scoped, 1m/5m/15m selectable, per-instrument, with a **VWAP overlay** (her primary execution-quality metric). Build history from `price` events (circular buffer per symbol).

### ⬜ 2.14 — Fill-rate table
- **Who:** Adeline. Per instrument: orders sent, fully filled, partially filled, rejected, fill-rate %.

### ⬜ 2.15 — Trade lifecycle log + CSV export (exact schema)
- **Who:** Linda + Gerald.
- **Schema (fixed order):**
  `TradeID | Timestamp_SGT | Instrument | Direction | Quantity | LimitPrice | FillPrice | FillQuantity | RemainingQty | Status | PnLImpact | CumulativePnL`
- **Status codes (exact):** `PENDING | ACKNOWLEDGED | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED`

### ⬜ 2.16 — Immutable audit log of human actions
- **Who:** Priya + Linda. Every parameter change / manual action: timestamp, action, who.

### ⬜ 2.17 — Alerts
- **Who:** Priya. Portfolio drawdown > 15% of starting capital; single instrument drops too far; bot goes quiet when it shouldn't.

### ⬜ 2.18 — Risk visibility
- **Who:** Priya. Per-instrument stop-losses, **visible in the dashboard**, not buried in config.

### ⬜ 2.19 — Correlation matrix + net exposure
- **Who:** Clarissa, Farrukh. NOVA/AXON move together; long-both = doubled position. Show a colour-coded matrix + net directional exposure.

### ⬜ 2.20 — Portfolio heat map  🟢 *the CIO's headline visual, unbuilt for 6 months*
- **Who:** Raymond (CIO). "A nightlight, not a dashboard." Wants colour telling him *instantly* which instruments are winning and losing — a "Bloomberg-like feel."
- **What:** A per-instrument grid, cell colour = performance (green→red), size or intensity = magnitude. One glance = the whole book's state.
- **Why it's convincing:** It's the single most-requested visual from the most senior stakeholder, asked for repeatedly and never delivered. Cheap to build on data you already have (per-instrument P&L, 2.12), and it's the emotional centrepiece of the Overview — the thing that makes the dashboard *feel* like a trading desk instead of a table.

### ⬜ 2.21 — Risk-adjusted performance analytics  🟢 *"beating the benchmark, not just making money"*
- **Who:** Raymond ("Sharpe per instrument, not just raw P&L"), Priya ("I want our **information ratio** — beating the benchmark"), Farrukh (per-instrument **performance breakdown**).
- **What (per instrument):** Sharpe ratio · information ratio vs. benchmark · win rate · profit factor · avg profit on winners · avg loss on losers · number of trades.
- **Why it's convincing:** Raw P&L answers "did we make money"; this answers "were we *good*, or just lucky" — the question three senior stakeholders (CIO, Deputy CIO, PM) independently asked for. It's the difference between a hobby dashboard and one a CIO trusts to allocate risk. All computable from the trade log you're already keeping (2.15) — high credibility, low new plumbing.

---

# PHASE 3 — DIFFERENTIATORS (AI / analytics — pick 1–2, do them well)

Only after Phase 2 core is solid. These are the "wow" items — but Sophia's responsible-AI rule is a **hard requirement**, not optional polish.

### ⬜ 3.1 — Conversational assistant panel (over the provided MCP server)
- **Who:** Sarah (starter), Raymond, Sophia.
- **What:** Ask the system what it's doing in plain English. MCP server is provided and working — copy the pattern in `mcp-adapter.example.js`.
- **Hard rule:** every answer shows **citations + confidence** (`source` + `as_of` come back from each MCP tool). No black box.

### ⬜ 3.2 — Per-trade plain-English commentary
- **Who:** Raymond. "When the bot sells DRFT at 14:32, tell me *why* — plain English."

### ⬜ 3.3 — Announcement reasoning across the supply chain
- **Who:** Sophia. On an `announcement` SSE event, don't keyword-match the ticker — **reason across the supply chain** (sanctions on freight hit AXON directly, ripple to NOVA/CRVX). LLM job. There's a `# TODO` hook in the dispatcher.

### ⬜ 3.4 — Forward-looking view with confidence band
- **Who:** Sophia. Short-horizon move probability or a param-tuning suggestion, **labelled as a model estimate, never as fact**, with a confidence band.

---

# ALWAYS-ON (engineering hygiene — do throughout, not at the end)

- **Who:** Marcus + Aaron.
- Meaningful git history (commit as you go, not one dump).
- Unit tests on core logic: strategy signals, P&L maths, announcement parsing.
- Lint + test **CI on every push**.
- Reproducible: pinned dependencies, **one command to run**.
- Environment separation: sandbox = dev, live = prod. **Never test against prod.**
- Bot and dashboard are **separate processes** — if one dies, the other lives.
- Document where you used AI and that a human reviewed it.

---

# DELIBERATELY NOT DOING (defend these in Q&A)

| Ask | Why we're skipping / limiting it | Source |
|-----|----------------------------------|--------|
| Trade DRFT at normal size | Planted error in a hurried junior note; seniors + the call say tiny size | Post-meeting note vs. call |
| Hard-block DRFT at system level | Group settled on "tradeable with risk management," not blocked | Zoom call |
| Kubernetes, ORM, monitoring sidecar, 70% coverage | Aaron conceded these are aspirational for one day | Aaron |
| Internal IaC framework, specific CI vendor, full E2E | Marcus conceded aspirational | Marcus |
| Months-long backtest / walk-forward before shipping | Sophia's ambition ran ahead of the one-day timeline | Sophia |
| Rebalancing to target weights | Retracted — this is an intraday trader, not a portfolio | Farrukh |
| Market orders instead of limit | Every order is a limit order; no `type` field exists | Yuhui |
| "Bot mood", bigger green dot, mobile-first | Preference / fun — only if everything real is done | Junior chat, Raymond |

---

# DASHBOARD LAYOUT — SUGGESTED FEATURES BY TAB

The dashboard is organised into a **global frame** (always visible) plus **seven tabs**.
Each tab below lists the features it should carry (with the item numbers from the
checklist above and a verdict), and the **role(s)** it primarily serves. Roles, not
names — so it maps to *function*, not personalities.

**Verdict legend**

| Symbol | Meaning |
|--------|---------|
| ✅ **Required** | Graded floor / near-universal agreement. Skipping it costs you. |
| 🟢 **Expected** | Genuine, senior-sourced, in-scope. Do several; skipping all is a gap. |
| 🔵 **Optional / differentiator** | Real value, but pick-and-choose. Do 1–2 well. |
| ⚠️ **Conditional** | Allowed only *with* a specific constraint (or it's a trap). |

---

## 🌐 GLOBAL FRAME — always visible on every tab (not a tab)

**Serves:** Senior Trader (risk); Deputy COO; Head of Tech — and everyone.

| Feature | # | Verdict | Notes |
|---------|---|---------|-------|
| **Kill switch** | 2.1 | ✅ | **One button, always visible, immediate, NO confirmation.** Lives in the global chrome so it's reachable from *any* tab in a crisis — **deliberately NOT on the Risk & Control tab.** |
| Live health / liveness dot | (2.11) | ✅ | Driven by the bot **heartbeat** (~5s; dead if >15s) over the direct bot→dashboard channel — not a REST-ping dot. |
| SGT clock / timestamps everywhere | 1.8 | ✅ | `YYYY-MM-DD HH:MM:SS.mmm SGT`; underpins every tab. |

> **Why the kill switch is global, not a Risk tab feature:** an emergency halt must be
> one click away no matter what the trader is looking at. Burying it inside a tab adds a
> navigation step in exactly the moment you can't afford one.

---

## 🏠 TAB: Overview / Home

**Serves:** Deputy COO, CIO
**On it:** the 30-second health + P&L summary, per-instrument P&L tiles, big status.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Health / summary screen (did it make money, how much, is it stable — in 30s) | 2.11 | ✅ |
| **Portfolio heat map** — colour = win/lose at a glance ("Bloomberg-like feel") | 2.20 | 🟢 |
| Per-instrument P&L tiles + running totals (buys, sells, partials, rejects) | 2.12 | 🟢 |

*Depends on Phase-1 fixes:* live SSE feed (1.6), clear-on-reconnect (1.7), no undefined
cells (1.2), SGT timestamps (1.8).

> **Why the heat map is here, not on Performance:** Raymond wants the whole book's
> state *instantly*, on the screen he glances at. The heat map is the emotional
> centrepiece of the 30-second view; the *deep* risk-adjusted numbers live on Performance.

---

## 📈 TAB: Strategy

**Serves:** Senior Trader (strategy), Deputy CIO
**On it:** per-instrument strategy + regime state, params (with 2-step confirm to change),
momentum / pairs / market-making status.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Per-instrument **regime state** (ATR: trending / ranging / neutral) | 2.9 | 🟢→✅ |
| NOVA momentum status | 2.5 | ✅ |
| CRVX momentum status | 2.6 | 🟢 |
| AXON pairs-off-NOVA status | 2.7 | 🟢 |
| Market-making status (ELXR/AXON) | 2.8 | 🔵 |
| Parameter view + **two-step confirm** to change a limit/threshold | 2.3 | 🟢 |

*Engine behind it:* strategy router + regime detector in the bot (all logic lives in the
bot; this tab is display + the confirm control).

---

## 🕯️ TAB: Execution

**Serves:** Senior Trader (execution)
**On it:** candlestick + VWAP charts, fill-rate table, order lifecycle.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Candlestick charts (1m/5m/15m, per instrument) + **VWAP overlay** | 2.13 | 🟢 |
| Fill-rate table (sent / filled / partial / rejected / fill-rate %) | 2.14 | 🟢 |
| Order **lifecycle** view (live status of each order) | 2.15 | ✅ |

*Depends on:* no candles endpoint exists → build history from `price` SSE events
(circular buffer per symbol). The full log + CSV export lives on the **Ops** tab.

---

## 📊 TAB: Performance / Analytics

**Serves:** CIO, Deputy CIO, Portfolio Manager
**On it:** the "are we actually good?" numbers — risk-adjusted, per instrument, benchmark-relative.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| **Sharpe per instrument** ("not just raw P&L") | 2.21 | 🟢 |
| **Information ratio** vs. benchmark ("beating the benchmark, not just making money") | 2.21 | 🟢 |
| Per-instrument **performance breakdown** (win rate, profit factor, avg win, avg loss, # trades) | 2.21 | 🟢 |
| Per-instrument comparison ("which are we good at, which are we losing on") | 2.21 | 🟢 |

> **Why a dedicated tab:** three senior stakeholders (CIO, Deputy CIO, PM) independently
> asked "were we *good* or just lucky?" — a question raw P&L can't answer. It's too much
> depth for the 30-second Overview but too important to omit. All of it derives from the
> trade log (2.15) you already keep, so it's high-credibility, low new plumbing.

---

## 🛡️ TAB: Risk & Control

**Serves:** Senior Trader (risk), Portfolio Manager
**On it:** pause/resume per instrument, stop-losses, correlation matrix, net exposure,
drawdown.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Per-instrument **pause / resume** | 2.2 | 🟢 |
| Per-instrument **stop-losses**, visible (not buried in config) | 2.18 | 🟢 |
| **Correlation matrix** (colour-coded) + net directional exposure | 2.19 | 🟢 |
| Drawdown meter + **alerts** (>15% of starting capital, single-name drop, bot quiet) | 2.17 | 🟢 |
| Order-rate **anomaly auto-pause** (the "March incident") | 2.4 | 🟢 |
| DRFT tiny-size guardrail surfaced as a risk cap | 2.10 | ⚠️ |

> **NOTE:** the **kill switch is NOT here** — it's in the global frame (always visible,
> reachable from any tab). This tab is for the *considered* controls; the emergency halt
> is global.

---

## 🧾 TAB: Ops / Logs

**Serves:** Trade Operations
**On it:** trade log, immutable audit log, CSV export, end-of-session summary.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Trade log + **CSV export** to the fixed schema + exact status codes | 2.15 | ✅ |
| **Immutable audit log** of human actions (timestamp, action, who) | 2.16 | 🟢 |
| End-of-session summary (total P&L, total trades, best/worst, alerts fired) | (2.11/2.12) | 🟢 |

*Depends on:* SGT ms timestamps (1.8); the CSV column order + status codes are fixed and
graded — reproduce them exactly.

---

## 🤖 TAB: Assistant

**Serves:** Investment Analyst, CIO
**On it:** the conversational / research panel with **citations + confidence**.

| Suggested feature | # | Verdict |
|-------------------|---|---------|
| Conversational assistant over the provided MCP server | 3.1 | ⚠️ citations + confidence is a **hard rule** |
| Per-trade plain-English commentary ("why did it sell DRFT at 14:32?") | 3.2 | 🔵 |
| Announcement **supply-chain reasoning** (not keyword-matching the ticker) | 3.3 | 🔵 |
| Forward-looking move probability + **confidence band** | 3.4 | ⚠️ label as a model estimate, never fact |

*Phase 3 — pick 1–2 and do them well.* Every answer must show its `source` + `as_of`
(they come back from each MCP tool). No black box.

---

## What has NO tab (engine-side, powers everything)

These are **bot-side**, not a screen — but the tabs are worthless without them:

| Work | # | Verdict |
|------|---|---------|
| The 7 Phase-1 bug fixes (lookback, reconnect, shutdown, error handling, missing-instrument, polling→SSE, stale-data, SGT) | 1.1–1.8 | ✅ |
| Strategy engine: router + `BaseStrategy` subclasses, regime detector, DRFT sizing cap | 2.5–2.10 | mixed |
| Kill-switch / pause backend (bot control channel + graceful actions) | 2.1/2.2 | ✅ |
| Engineering hygiene: git history, tests, lint+test CI, reproducible run, sandbox≠prod, separate processes, document AI use | Always-on | ⚙️ |

---

## Role → primary tab (quick map)

| Role | Primary tab(s) |
|------|----------------|
| Deputy COO | Overview |
| CIO | Overview (heat map), Performance, Assistant |
| Senior Trader (strategy) | Strategy |
| Deputy CIO | Performance (information ratio), Strategy (params/confirm), Risk & Control |
| Senior Trader (execution) | Execution |
| Senior Trader (risk) | Risk & Control + the **global** kill switch |
| Portfolio Manager | Performance (breakdown), Risk & Control |
| Trade Operations | Ops / Logs |
| Investment Analyst | Assistant |
| Head of Tech / Architect / Eng Lead | No tab — reliability, technical facts & hygiene under all of them |

---

# PHASE 3 DEEP-DIVE — THE AI LAYER (multi-agent deep research + RAG)

> **It does not trade.** Deterministic strategies place orders; this layer *explains and
> advises*. It's a **decision-support and accountability layer** — and it's the feature
> that turns "a bot that acts" into "a system leadership can trust." (Full design in
> `ai-architecture.md`.)

**The thesis, in one line:** the requirements already describe a research system without
naming it — *"reason across the supply chain, not a keyword match"*, *"citations and
confidence, always"*, *"show me you reviewed the AI"*, *"tell me **why** it sold DRFT"*.
A single LLM call is a black box that confidently guesses. A **multi-agent deep-research
system grounded by hybrid RAG** turns every announcement and every trade into a
**verified, cited, confidence-scored explanation.** That's not a bolt-on — it's the
architecture those requirements *imply*.

## Where each AI feature lives, and why it's decisive

| AI feature | # | Tab (platform) | Serves (role) | Why it's extremely useful — tied to the ask |
|------------|---|----------------|---------------|----------------------------------------------|
| **Announcement → supply-chain impact engine** | 3.3 | **Assistant** (signal echoed on **Strategy/Overview**) | Investment Analyst | The killer demo. Orchestrator fans out to *direct / first-ripple / second-ripple / historical-analog* agents; adversarial verifiers try to refute each link. Output is per-instrument *directional call + confidence + citations* — **exactly** "reason across the supply chain, not keyword-match the ticker," satisfied *by construction*. |
| **Per-trade plain-English commentary** | 3.2 | **Execution** (+ **Ops** log) | CIO | After each fill, a light grounded pass over trade + regime + book + any live announcement → *"Sold AXON @14:32: regime flipped low-vol (ATR% 0.0011<0.0015), momentum suspended; NOVA reversed off its high."* Directly answers "tell me **why**, in English." |
| **Conversational assistant** | 3.1 | **Assistant** | CIO, Investment Analyst | "Are we over-exposed?" → mini fan-out (correlation + net-exposure + drawdown) → cited synthesis over the **MCP** tools. Every answer carries `source` + `as_of`. **No black box.** |
| **Forward-looking view + confidence band** | 3.4 | **Strategy** (param suggestion) / **Assistant** | Investment Analyst, Deputy CIO | Research over fill-rate + P&L proposes a tweak *as a labelled estimate with a confidence band* — **never auto-applied** (respects the two-step confirm on the Strategy tab). |
| **Risk / correlation reasoning** | (2.19) | **Risk & Control** | Senior Trader (risk), Portfolio Manager | "Long NOVA *and* AXON — one bet or two?" is a *reasoning* question. An agent reasons over the correlation matrix + positions in plain English — interrogable risk, not a static grid. |

## What each agent actually does

Each agent is just an LLM given **a narrow job, a specific slice of retrieved data, and a
required output shape.** For the flagship announcement engine, one **orchestrator** wakes
on the event and spawns a team:

| Agent | Its one job | What it's fed (RAG) | What it returns |
|-------|-------------|---------------------|-----------------|
| **Orchestrator** | Read the announcement, decide which instruments could be affected, dispatch one agent per hypothesis, then collect + synthesize | Raw announcement text + instrument list | The final per-instrument verdict |
| **Direct-impact agent** | "Is any instrument *named or obviously targeted*?" | Announcement + the named symbol's recent price/book | *"AXON: freight named directly → negative."* |
| **First-ripple agent** | "What's the *second-order* effect one hop down the chain?" | Supply-chain notes + NOVA price window | *"NOVA: freight rerouting shifts energy demand → mildly negative."* |
| **Second-ripple agent** | "What's the effect *two hops* out, for a different reason?" | CRVX context + supply-chain notes | *"CRVX: input-cost exposure → uncertain."* |
| **Historical-analog agent** | "When something *like this* happened before, what did these names do?" | RAG over the past-announcement + trade archive | *"Last 3 freight shocks → AXON fell ~2%, NOVA flat."* |
| **Verifier agents** (panel) | **Try to REFUTE** each claim; retrieve counter-evidence | Each claim + price data that might contradict it | *"NOVA link weak — price didn't move last time. Refuted."* |

**The chain:** orchestrator → parallel lens agents (blind to each other, so they surface
different effects) → verifier panel attacks each claim → orchestrator synthesizes what
survived. Confidence = *how many verifiers a claim survived* (3/3 = high, 2/3 refute =
dropped) — **measured, not invented.**

The simpler features use **one** agent, no team:
- **Per-trade commentary agent** — retrieves the trade + regime + book + any live
  announcement, writes the plain-English *why*.
- **Assistant agent** — calls the MCP tools for exact figures, reasons, answers *with* the
  `source`/`as_of` citations (can itself fan out for a hard question).

> **The rule every agent obeys:** an agent may only state a number that came from a
> **tool** (MCP / structured query) — never one it "remembers." Narrative reasoning can be
> fuzzy (that's what vector RAG is for); *figures must be exact and cited.*

## When each agent runs, and which persona is involved

Two trigger types: **event-driven** (the *system* runs them, no human) and
**user-initiated** (a *person* asks). The persona is usually the **consumer, not the
trigger.**

| AI feature | *When* it runs — trigger | Who triggers it | Who consumes it (persona) |
|------------|--------------------------|-----------------|---------------------------|
| Announcement supply-chain engine | **Automatic** — the instant an `announcement` SSE event fires | The exchange event (nobody clicks) | **Investment Analyst** |
| Per-trade commentary | **Automatic** — on every fill | The bot's own fill event | **CIO** |
| Conversational assistant | **On-demand** — when someone types a question | **A human** | CIO, Investment Analyst |
| Forward-looking / param suggestion | **Either** — periodic or on-demand | System schedule *or* Deputy CIO | Investment Analyst, Deputy CIO |
| Risk / correlation reasoning | **On-demand** — when asked | **A human** | Senior Trader (risk), PM |

**The key insight:** the two flagship features run *without* a persona pressing "go." A
trader can't watch for every announcement — the system reasons the instant it happens and
leaves a cited memo waiting. Raymond doesn't ask "why did it sell?" after the fact — the
commentary is *already attached to the trade* when he opens it. **No agent can place a
trade** — every one is read-only; deterministic strategies act, agents explain.

## Where the output surfaces (routing — NOT every tab sees everything)

Full detail lands on the **one tab whose persona owns it**; at most a thin signal is
echoed elsewhere; the rest show nothing. Broadcasting to all tabs recreates the exact
"nightlight, not a dashboard" clutter Raymond complained about.

| AI output | Full detail on | Thin echo on | Shown nowhere else |
|-----------|----------------|--------------|--------------------|
| Announcement memo | **Assistant** (full report) | one-line **signal badge** on Overview + Strategy | Execution, Ops, Performance, Risk |
| Per-trade commentary | **Execution** (next to the fill) | the trade row in **Ops** log | Overview, Strategy, Performance, Assistant |
| Assistant answers | **Assistant** only | — | everywhere else |
| Risk / correlation reasoning | **Risk & Control** | — | everywhere else |
| Param suggestion | **Strategy** (as a proposal) | — | everywhere else |

> **Rule of thumb:** *full detail on the owning tab; at most one compact badge echoed
> where awareness is needed.* Same agent run, **different depth surfaced per tab** — the
> analyst gets the deep memo, the Deputy COO on Overview gets a one-line "is something
> wrong?" and nothing more.

## Why *multi-agent* beats one big prompt (the part that wins the room)

- **Confidence is *measured*, not invented.** A panel of adversarial verifiers ("refute
  this link, retrieve counter-evidence") kills weak claims and reports survival rate →
  the confidence score is *derived from how many verifiers a claim survived.*
- **Diverse lenses catch more.** Direct / ripple / analog agents are blind to each other,
  so they surface different effects. One prompt cannot be four perspectives.
- **Auditable by construction.** Every agent returns its retrieved sources, so the
  Engineering Lead's *"show me you reviewed what the AI wrote"* and the Portfolio
  Manager's *"justify everything"* become automatic — **the justification *is* the
  artifact**, not an afterthought.
- **Numbers stay exact.** Hybrid retrieval routes **exact figures to the MCP server**
  (already cited `source`/`as_of`) and **narrative to the vector store**. Agents may only
  state numbers that came from a tool — never invented ones.

## The deep-research **workbench** — how these features get built (and stay honest)

Rather than hard-coding one assistant, build a **configurable multi-agent workbench**:
agents are **data, not code**, so new capabilities are configuration, not a rewrite. This
is *why* the AI layer is credible to the technical stakeholders — it's a reviewable
system, not a prompt in a text box.

| Workbench capability | What it is | Why it maps to the problem statement |
|----------------------|-----------|--------------------------------------|
| **Config-as-data agents** | An `AgentConfig` (name, system-prompt, model, `kind: single \| deep`, granted skill-ids, subagent roster) with CRUD + preset seeder | Users configure agents from the UI; ships with working presets. Adding a capability = one config row, not new code. |
| **Two runtime shapes** | `single` → a normal tool-using agent; `deep` → a lead that plans, delegates to specialist subagents, synthesizes a cited answer | *Single* powers per-trade commentary and the assistant; *deep* powers the supply-chain engine. Same platform, two depths. |
| **Skills registry (data ≠ implementation)** | A pure-data catalog (id, label, group, description) mapped to tool factories; the **data tools are the MCP/structured tools** | Enforces the hard rule: **agents may only state numbers that come from a tool.** That single constraint *is* the Investment Analyst's "citations + confidence" and sidesteps the black-box objection. |
| **Trace sink + visualiser** | Every tool call appends a step (owner, args, latency, result preview, ranked hits) → rendered as a **linear timeline** *and* an **orchestration graph** (agent nodes, tool nodes, delegation/call edges) | **This is the audit trail the Engineering Lead and Portfolio Manager asked for — visualised.** Subagent tool calls never appear in the lead's message log; the trace sink is the only place that sees them, so it becomes the source of truth. |
| **Run persistence & memory** | Every run (agent, question, output, trace, latency, error) stored with list + detail views | Aligns with the **immutable audit log** ask (Ops) and *"justify everything"* — every AI answer is replayable with its evidence. |

**Design pass first.** Before any code: read the codebase and propose the data model,
module boundaries, and framework choice (e.g. a single-agent library for `single`, a
deep-agent library for `deep`), call out the trade-offs, and get sign-off. *That* is the
"human owned the decisions" the Engineering Lead wants — baked into how the layer is
built, not claimed after the fact.

## Guardrails — say these out loud (judges reward them)

- **It never places orders.** Strategies trade; research explains. Kills the
  "black-box trading" objection entirely.
- **Off the hot path.** Runs async in seconds — fine for commentary/signals, never in the
  tick loop.
- **Respects rate limits.** Reads cached SSE history + MCP, not fresh exchange hammering.
- **Exact where it matters.** Numbers come from MCP, not the vector store.

## One-day realism (this is a Phase-3 differentiator — bugs and core first)

In impact-per-effort order: **(1)** minimal announcement engine — orchestrator + 3 lens
agents + 1 verifier + synthesis (keyword+recency retrieval over the small corpus is
enough; the *verification + citation shape* is what impresses, not the vector DB); **(2)**
per-trade commentary (one grounded call per fill, reuses the retrieval); **(3)** assistant
over MCP with citations wired from the `source`/`as_of` you already get back. The
config-as-data + trace visualiser is the frame that makes all three look like one system.

> **The pitch:** *"The requirements already describe a research system — supply-chain
> reasoning, citations, confidence, reviewable AI. We built the architecture those
> requirements imply: a multi-agent deep-research layer, grounded by hybrid RAG, that
> turns every announcement and every trade into a verified, cited explanation. It doesn't
> trade — it makes the trading accountable. That's the difference between a bot that acts
> and a system a CIO can trust."* — hits the Investment Analyst, CIO, Engineering Lead,
> risk desk, and Deputy COO in one feature.

---

*Order = priority. Fix (Phase 1) → Extend (Phase 2) → Differentiate (Phase 3), with hygiene throughout. When in doubt: fewer features, done cleanly, every decision defensible.*
