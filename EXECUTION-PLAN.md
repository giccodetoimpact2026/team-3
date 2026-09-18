# Execution Plan — GIC #CODETOIMPACT 2026 (Team 3)

## Strategy in one line
Get a **safe, non-crashing, stoppable bot** trading *something* first (Challenge 1 floor),
then buy alpha and dashboard polish with the remaining time. A bot that crashes or can't be
stopped scores zero and embarrasses you in the demo — that risk is retired first.

## Ground truth (the lens for every priority call)
- **Deputy COO's bar:** *"works, doesn't crash, can be stopped."*
- **Platform facts:** never hardcode instruments (`GET /instruments`), everything user-facing is
  **SGT**, the feed is **SSE** (not polling), orders are **limit-only / long-only**, money is
  **integer cents**.
- **Guardrails (constraints, not fixes):** `X-API-Key` on every authed REST call (BUG-005);
  period decimal separator in all formatting/exports (BUG-016).
- **Scored on both** trading P&L *and* the analytics build; the day runs in **three fixed 30-min
  live windows** (scored on **average placing**), everything else in sandbox.

---

## Phase 0 — Foundation (do FIRST, before order submission opens)
Everything here is free while submission is closed.

1. **Get all three baselines running locally** and confirm each hits the exchange read endpoints
   with `X-API-Key`:
   - `baseline-bot` — `python main.py`
   - `baseline-dashboard` — open `index.html` / `npm run check`
   - `baseline-mcp-server` — via Docker
   *If it doesn't run, nothing else matters.*
2. **Read the ground-truth docs:** each `HANDOVER.md` and `docs/stakeholder-interview-records.md`
   (the latter holds real bug severities and the genuine-vs-noise requirement calls — graded).
3. **Set up scored repo discipline:** feature branches, CI (`.github/workflows` in each baseline),
   and living stubs for the 4 deliverables — `README.md`, `SPEC.md`, `CLAUDE.md`,
   `presentation.html`. Update these *as you go*.
4. **Skim `docs/james-wee-strategy-notes.md` page 3** (ATR regime-detection formula) — feeds Phase 2.

---

## Bot bug prioritisation (fix in this order)
The bot's bugs ranked into tiers. Work top-down: **P0 → P1** must land before Window 1;
**P2** is where P&L comes from; **P3** is support. Add a **test** per fix (tests + CI are graded).
Guardrail **BUG-005** (`X-API-Key` on every authed call) applies throughout — never regress it.

### P0 — Survival (blockers; a fail here scores zero)
1. **BUG-019** — crash on missing instrument → guard portfolio lookups (also fixes dashboard)
2. **BUG-011** — silent death after a disconnect → SSE reconnect with `Last-Event-ID` replay
3. **BUG-001** — cancel resting orders on shutdown (+ wire the kill-switch)
4. **BUG-003** — order-rate anomaly throttle (runaway-order protection; the "March incident")

### P1 — Protect capital in live windows (risk + reliability)
5. **BUG-009** — per-position stop-loss (unbounded losing positions)
6. **BUG-008** — portfolio-level exposure cap (correlated names all at full size)
7. **BUG-017** — classify/retry errors instead of swallowing (underpins detecting P0 failures)
8. **BUG-020** — reconcile orders left unknown on a 504 gateway timeout

### P2 — Alpha (Challenge 1 P&L)
9. **BUG-012** — mean-reversion buys into downtrends → ATR regime detection (notes page 3)
10. **BUG-014** — volatility/liquidity-scaled sizing (replace fixed size)
11. **BUG-013** — re-enable market-making (stretch — only after P0/P1 solid)

### P3 — Support
12. **BUG-002** — structured, queryable logging (replace stdout prints)

> Cross-cutting decisions to make + defend: **DRFT** (trade it? size + safeguards) and
> **strategy lookback** (last N prices vs N seconds — feed timing is irregular).

---

## Team split — 5 owners (bucketed by file boundary to avoid conflicts)
Each person owns a coherent slice of the codebase. Priority tags (P0/P1/P2) show what to do first
*within* each bucket — everyone lands their P0 before Window 1.

### 👤 1 — Bot: Connectivity & Resilience
Owns `exchange/client.py`, `exchange/feed.py`, `utils/logger.py`, the main-loop error paths.
- **P0** BUG-011 (SSE reconnect + `Last-Event-ID`), bot-side of **BUG-019** (crash guard)
- **P1** BUG-017 (classify/retry errors), BUG-020 (504 reconcile)
- **P3** BUG-002 (structured logging)

### 👤 2 — Bot: Risk & Kill-switch
Owns `risk/limits.py` and the shutdown/kill path in `main.py`. Holds guardrail **BUG-005**.
- **P0** BUG-001 (cancel resting orders on shutdown + kill-switch), BUG-003 (order-rate throttle)
- **P1** BUG-009 (per-position stop-loss), BUG-008 (portfolio exposure cap)

### 👤 3 — Bot: Strategy & Alpha
Owns `strategies/` end-to-end (isolated folder — low conflict risk).
- **P2** BUG-012 (ATR regime detection, notes page 3), BUG-014 (dynamic sizing), BUG-013 (re-enable MM — stretch)
- Owns the **DRFT** and **lookback-window** decisions

### 👤 4 — Dashboard (Challenge 2 UI)
Owns all of `baseline-dashboard/`. Holds guardrail **BUG-016**.
- **P0/critical** BUG-ARCH (polling → SSE, show halts/announcements), dashboard-side of **BUG-019**
- **must-fix** BUG-021 (SGT), BUG-004 (stop hardcoding instruments)
- **then** BUG-018 (price chart), BUG-007 (stale P&L), BUG-010 (honest status dot)

### 👤 5 — AI Panel & MCP (Challenge 2 intelligence)
Owns `baseline-mcp-server/` and the conversational control panel; integrates via
`https://llm.codetoimpact.sg` (the only AI that scores). Pairs with 👤4 for panel-in-dashboard wiring.
- Run + extend the read-only MCP server; build the conversational panel consuming it
- Owns the AI-related deliverable sections in `CLAUDE.md` / `SPEC.md`

> **Shared first task:** **BUG-019** spans bot + dashboard — 👤1 guards the bot side, 👤4 the
> dashboard side; both land it before anything else.
> **Coordination flag:** 👤1 and 👤2 both touch `main.py` (loop vs shutdown/kill) — agree the
> hook points up front to avoid conflicts.

---

## Phase 1 — Survival ("works, doesn't crash, can be stopped")
Ship the bot's **P0** tier (above) before any strategy work — BUG-019 → BUG-011 → BUG-001 → BUG-003.

---

## Phase 2 — Split the team, parallelize the two tracks

### Track A — Bot: P1 then P2 (Challenge 1)
- First land the bot's **P1** risk/reliability tier (BUG-009, BUG-008, BUG-017, BUG-020)
- Then **P2** alpha: BUG-012 (regime detection, page 3), BUG-014 (dynamic sizing), BUG-013 (stretch)
- Decide **DRFT** (thin liquidity — size + safeguards) and the strategy lookback window
- Practice in sandbox against the 15 resident bots

### Track B — Analytics (Challenge 2)
- **BUG-ARCH** — polling (~30s) → SSE; surface halts/announcements
- **BUG-018** — price chart (accumulate history from the feed; no candles endpoint)
- **BUG-021** — SGT timestamps · **BUG-007** stale P&L · **BUG-010** honest status dot
- **BUG-004** — stop hardcoding instruments (dashboard)
- **AI conversational panel** consuming the MCP server — the *only* AI that scores must use
  `https://llm.codetoimpact.sg`

---

## Phase 3 — Compete & present
- Freeze the bot before Window 1. Each window starts fresh capital; scored on **average placing**
  across three — don't over-tune to a single window.
- Finalize the 4 deliverable files; zip as `<TeamName>.zip` (exactly those 4 files at root).
- Rehearse the 10-min demo + 5-min Q&A — judges probe your **prioritization and descoping**
  rationale.

---

## Bug triage reference

### Likely must-fix 7 (unflagged in the tracker — this is our defensible call)
| ID | Bug | Why |
|----|-----|-----|
| BUG-019 | Crash when instrument absent from portfolio (Both) | Literal crash → violates "doesn't crash"; hits bot + dashboard |
| BUG-001 | Open orders rest live after shutdown (Bot) | Violates "can be stopped"; ties to kill-switch |
| BUG-011 | Bot silently stops reacting after disconnect (Bot) | Violates "works"; silent death in a live window |
| BUG-003 | No order-rate anomaly throttle (Bot) | The "March incident"; risk desk ask |
| BUG-009 | No per-position stop-loss (Bot) | Core risk control; unbounded losing positions |
| BUG-021 | Dashboard timestamps in UTC (Dashboard) | Platform rule = SGT; already caused ops reconciliation errors |
| BUG-ARCH | Dashboard polls ~30s, misses announcements/halts (Dashboard) | Head of Tech ask; missing a market-halt is a real hazard |

> Swappable candidates depending on how you weight compliance/risk/observability:
> **BUG-004** (hardcoded instruments), **BUG-008** (portfolio exposure cap).

### Guardrails (preserve, don't "fix")
- **BUG-005** — `X-API-Key` on every authed REST call; never `Authorization`/`X-Team-Token`; SSE `/events` is public
- **BUG-016** — force period decimal separator (en-US); comma-decimal corrupts exports

### Secondary (fix if time)
BUG-008 (exposure cap), BUG-020 (order unknown on 504), BUG-017 (swallowed errors),
BUG-010 (misleading status dot), BUG-007 (stale P&L), BUG-004 (hardcoded instruments)

### Strategy / feature (alpha or polish, not survival)
BUG-012 (regime detection), BUG-014 (dynamic sizing), BUG-013 (market-making disabled),
BUG-018 (price chart), BUG-002 (stdout-only logging)

---

## Do this right now — first three moves
1. **Confirm all three baselines run** and hit the exchange read endpoints.
2. **Read `docs/stakeholder-interview-records.md`** to lock requirement/severity calls (validate the must-fix 7).
3. **Fix BUG-019** — the shared crasher — first commit on a feature branch with CI green.
