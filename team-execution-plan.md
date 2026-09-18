# 2-Hour Execution Plan (5 People)

Source: [must-fixes.md](must-fixes.md) (7 tracker repairs), [bot remediation plan](GIC_Bot_Bug_Remediation_Plan_2026-09-18.md) (P0 defects and failure chains), [feature-backlog.md](feature-backlog.md) (high-priority features). Reference [architecture.md](architecture.md) for component responsibilities.

## Rationale

The bot remediation plan identifies P0 defects — runaway orders (Chain A), silent feed death (Chain B), unknown orders after timeout (Chain C), and session state leaks (Chain D) — that are more dangerous than any missing feature. A kill switch is useless if the bot has already submitted 50 untracked orders or died silently. **Fix the engine first, then add the controls.**

This plan puts 3 people on the bot and 2 on the dashboard in Hour 1. Features move to Hour 2 once the bot is safe.

## Team Assignments

| Person | Focus Area | Primary Codebase |
|--------|-----------|-----------------|
| **P1** | Bot Feed Supervision | `baseline-bot/exchange/feed.py` |
| **P2** | Bot Orders & Risk | `baseline-bot/exchange/client.py`, `risk/` |
| **P3** | Bot Lifecycle & Session | `baseline-bot/main.py` |
| **P4** | Dashboard Live Data | `baseline-dashboard/js/` |
| **P5** | Dashboard Display & Export | `baseline-dashboard/js/` |

---

## Hour 1 (0:00–1:00) — Bot Safety + Dashboard Must-Fixes

### Minute 0 — Coordination (everyone, 5 min)

Agree on these shared interfaces before splitting:

- **OrderManager API**: P2 owns it; P3 needs `cancel_all()` and `block_new_intents()` for shutdown; P1 needs it to not run in the SSE thread.
- **Event queue**: P1 produces validated events into a bounded queue; P2/P3 consume from it. Agree on the queue object and overflow policy (block trading, don't drop events).
- **Dashboard data flow**: P4 owns rendering; P5 owns the SSE connection and stale-value handling. Agree on how price data reaches the DOM.

### P1 — Bot Feed Supervision

Addresses: Fix #2 (reconnect), P0-05, P0-06, P0-07, P0-08, Fix #5 (ELXR lookback)

1. **Fix #5: ELXR lookback 5→15** (5 min)
   - Change `lookback_samples: 5` to `15` in `baseline-bot/config.yaml` (line 29).
   - Add test: <15 samples produces no signal; known 15-sample sequence produces expected mean.

2. **Isolate callback failures** (15 min) — P0-06
   - In `feed.py`, `_emit()` calls handlers directly inside the listener thread (line 64). A handler exception kills the feed (Chain B).
   - Wrap handler invocation so exceptions are logged but don't escape to the listener loop.

3. **Decouple SSE from trading logic** (20 min) — P0-07, P0-08
   - `on_price()` in `main.py` calls `client.get_positions()` synchronously in the SSE thread, blocking ingestion and burning 300 calls/30s.
   - Push validated events into a bounded queue. Trading logic consumes from the queue in a separate worker thread.

4. **Supervised reconnect** (20 min) — P0-05, Fix #2
   - Replace the single `try/except: pass` in `_listen()` with a reconnect loop.
   - Exponential backoff with jitter, `Last-Event-ID` header on reconnect.
   - Validate HTTP status and content type before entering the SSE parser.
   - Track processed event IDs to dedup replayed events.

### P2 — Bot Orders & Risk

Addresses: P0-01, P0-02, P0-03, P0-04, P0-09, Fix #6 (bot side)

1. **Fix #6: Missing instrument → zero** (5 min) — P0-09
   - In `exchange/client.py` (lines 42–46), `get_positions()` crashes on an untraded symbol.
   - Return `0` for absent symbols.

2. **Typed exchange errors** (15 min) — P0-04, P1-01
   - In `exchange/client.py` (lines 80–86), all failures collapse to `None` and `main.py` logs "submitted" regardless.
   - Return explicit result objects: `ACKNOWLEDGED`, `REJECTED`, `RATE_LIMITED`, `UNKNOWN_RECONCILING`.
   - Only log/audit success after an acknowledged exchange response.

3. **Order manager with pending tracking** (25 min) — P0-01, P0-02
   - The bot submits a new order on every price tick because signals persist and only filled positions are checked (Chain A).
   - Create `OrderManager`: tracks active orders by symbol/side, calculates pending exposure (filled + pending buys − pending sells).
   - Enforce one-active-order-per-symbol policy or explicit replace.
   - Wire into `RiskLimits` so the 500-share cap accounts for pending orders.

4. **504 reconciliation** (15 min) — P0-03
   - Timeout leaves an unknown order with a discarded idempotency key (Chain C).
   - Preserve the key; query `/orders` to match; return `landed`/`not-landed`/`still-unknown`.
   - Never create a new key for a retry of the same intent.

### P3 — Bot Lifecycle & Session

Addresses: Fix #3 (shutdown), P0-11, P0-12, P0-13, P0-14

1. **Session bootstrap** (15 min) — P0-12
   - `session_open` starts `False` and only changes on SSE events. If the bot starts after market open, it never trades.
   - Call `GET /session` at startup to get current state before arming.

2. **Session boundary reset** (15 min) — P0-13
   - Strategies retain price deques forever; no session ID is tracked.
   - Detect session ID change; clear price history, pending orders, cached positions, and P&L baselines.

3. **Graceful shutdown** (20 min) — P0-11, P0-14, Fix #3
   - Lifecycle: `RUNNING → QUIESCING → CANCELLING → STOPPED`.
   - SIGINT/SIGTERM sets `QUIESCING` — blocks new intents immediately.
   - Enumerate and cancel all open/part-filled orders via `OrderManager` (coordinate with P2).
   - Tolerate already-filled/already-cancelled races.
   - Flush audit/logs; exit nonzero if orders remain unknown.

4. **Emergency halt path** (10 min) — P0-14
   - A bot-owned halt operation reusable by both signal handlers and future kill-switch commands.
   - Atomically rejects new intents before cancellation begins.

### P4 — Dashboard Live Data

Addresses: Fix #1 (SSE), Fix #4 (stale values)

1. **Replace 30s polling with SSE** (45 min) — Fix #1
   - In `baseline-dashboard/js/dashboard.js`, replace `setInterval(this.refresh, CONFIG.POLL_INTERVAL_MS)` with an `EventSource` connection to `/events`.
   - Handle `price` events to update instrument prices in real-time.
   - Keep MCP portfolio call for account values (P&L), but trigger on price events rather than a 30s timer.

2. **Clear stale values on reconnect** (15 min) — Fix #4
   - On `EventSource` error/close, immediately clear or mark all values as "Reconnecting…"
   - Don't show old numbers as current. Show "unavailable" until fresh data arrives.
   - On successful reconnect, restore normal display.

### P5 — Dashboard Display & Export

Addresses: Fix #7 (SGT), Fix #6 (dashboard side)

1. **UTC→SGT timestamps** (30 min) — Fix #7
   - In `dashboard.js` (line 60), replace `new Date().toUTCString()` with `toLocaleString('en-SG', {timeZone: 'Asia/Singapore'})`.
   - Format exports as `YYYY-MM-DD HH:MM:SS.mmm SGT`.

2. **Missing instruments in display** (30 min) — Fix #6 (dashboard side)
   - In `dashboard.js` (lines 46–55), `p.quantity` renders as `undefined` when `bySymbol[symbol]` is missing.
   - Show `0` for missing quantities, `—` for missing prices.
   - Replace hardcoded `INSTRUMENTS_TO_SHOW` in `config.js` (line 9) with dynamic fetch from `GET /instruments`.

---

## Hour 2 (1:00–2:00) — Features + Hardening

### P1 → Bot heartbeat + feed-stall detection

- Finish feed supervision if needed.
- Bot emits heartbeat every ~5s with feed age and lifecycle state.
- Add feed-stall watchdog: reconnect half-open streams, publish degraded/dead states.
- Dashboard shows bot status as ALIVE/DEAD (>15s without heartbeat = dead).

### P2 → Stop-losses + rate limiting

- Finish order manager if needed.
- Add per-instrument stop-loss using session-scoped cost/mark data; enforce in risk engine.
- Add rolling rate limiter for each API tier; keep headroom for cancels and reconciliation.
- Add order-rate anomaly throttle: auto-pause on burst.

### P3 → Kill switch + config validation

- Finish lifecycle if needed.
- **Emergency kill switch** (🔴 High from backlog): dashboard sends halt command → bot's halt path (built in Hour 1) blocks intents and cancels orders. One click, no confirmation.
- Validate runtime config at startup: reject placeholder secrets, invalid thresholds, nonpositive quantities.

### P4 → Per-instrument P&L + session counts

- Finish SSE + stale handling if needed.
- Wire MCP portfolio data to show per-instrument P&L with source and update time labels.
- Add buy/sell/partial/rejected counters visible on the dashboard.
- Track order events from the SSE `trade` stream.

### P5 → Order lifecycle view + CSV export

- Finish display fixes if needed.
- Order lifecycle table: every order's journey with exact status codes and SGT timestamps.
- Fixed-schema CSV export per Gerald's spec: `TradeID,Timestamp_SGT,Instrument,...` with `YYYY-MM-DD HH:MM:SS.mmm SGT` format.

---

## Last 15 Minutes (1:45–2:00) — Everyone

- **Integration test**: Run bot + dashboard + MCP together. Walk through all 7 must-fix ACs and P0 defect checks.
- **Smoke test the failure chains**: simulate a disconnect (Chain B), check reconnect works; submit with the session closed (Chain D), verify rejection.
- **Git hygiene**: Each person's work on a branch, reviewed PRs, meaningful commit messages.
- **Verify**: No hardcoded values, no placeholder secrets, sandbox/live separation intact, no regressions.

---

## Dependency Map

```
P1 (feed) ──produces──▶ event queue ◀──consumes── P2 (orders) + P3 (lifecycle)
P2 (OrderManager) ◀──uses── P3 (shutdown cancel_all / halt)
P3 (halt path) ◀──reused by── P3 Hour 2 (kill switch)
P4 (SSE connection) ──provides data──▶ P5 (rendering)
```

- **Minute 0**: P1, P2, P3 agree on the event queue and OrderManager interface.
- **Minute 0**: P4, P5 agree on how SSE price data flows to the DOM.
- **P1 commits Fix #5 (ELXR lookback) and P2 commits Fix #6 (missing instruments) immediately** — both are 5-minute wins.

## Must-Fix Coverage

| Must-Fix | Owner | Hour |
|----------|-------|------|
| #1 — SSE instead of 30s polling | P4 | 1 |
| #2 — Reconnect with backoff | P1 | 1 |
| #3 — Graceful shutdown | P3 | 1 |
| #4 — Clear stale values | P4 | 1 |
| #5 — ELXR lookback 5→15 | P1 | 1 (first 5 min) |
| #6 — Missing instruments (bot) | P2 | 1 (first 5 min) |
| #6 — Missing instruments (dashboard) | P5 | 1 |
| #7 — UTC→SGT | P5 | 1 |

## P0 Defect Coverage (from remediation plan)

| P0 ID | Issue | Owner | Hour |
|-------|-------|-------|------|
| P0-01 | Persistent signals → runaway orders | P2 | 1 |
| P0-02 | Pending exposure excluded from limits | P2 | 1 |
| P0-03 | 504 timeout → unknown order | P2 | 1 |
| P0-04 | Failed submit logged as success | P2 | 1 |
| P0-05 | Feed does not reconnect | P1 | 1 |
| P0-06 | Handler exception kills feed | P1 | 1 |
| P0-07 | REST blocks SSE ingestion | P1 | 1 |
| P0-08 | Portfolio read per tick exhausts budget | P1 | 1 |
| P0-09 | Missing instrument crashes | P2 | 1 (first 5 min) |
| P0-10 | ELXR uses 5 samples | P1 | 1 (first 5 min) |
| P0-11 | No graceful shutdown | P3 | 1 |
| P0-12 | Startup misses open session | P3 | 1 |
| P0-13 | Session state leaks between windows | P3 | 1 |
| P0-14 | No emergency halt path | P3 | 1 |
