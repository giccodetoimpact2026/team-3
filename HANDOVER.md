# Bot Handover

Original author: Alex Chen (Lead Developer, Bot)
Updated: 18 September 2026 (SGT) — P1 and P3 bug-fix branches merged.

---

## What works

- Momentum on the primary instrument (NOVA) trades reasonably in trending markets.
- Mean-reversion on the secondary instrument (ELXR) is connected and placing limit
  orders — lookback corrected to 15 samples (see below).
- The SSE feed connects, accumulates price history, and reconnects on disconnect.
- The REST client is clean; all endpoints are wrapped.
- Bot starts trading immediately if launched mid-session (session bootstrap).
- Ctrl-C / SIGTERM cancels open orders before exiting (graceful shutdown).

---

## Bugs fixed

### P1 feed supervision (`p1/fix` branch, commit `252bc96`)

| # | Issue | Remediation | What changed |
|---|-------|-------------|--------------|
| 1 | ELXR mean-reversion used a 5-sample lookback, causing false signals | P0-10 / BUG-012 | `config.yaml` → 15 samples. Regression test: no signal at 14 samples, signal at 15. |
| 2 | Missing portfolio instrument crashed the bot (`KeyError`) | P0-09 / BUG-019 | `get_positions()` returns `0` for absent symbols. |
| 3 | SSE disconnect permanently stopped market processing | P0-05 / BUG-011 | Reconnect with bounded exponential backoff + jitter, `Last-Event-ID` on reconnect, HTTP status/content-type validation, logged failures. |
| 4 | One callback exception killed the SSE listener | P0-06 | Handler failures logged and isolated; listener continues. |
| 5 | Trading logic blocked the network-reader thread | P0-07 | SSE reader pushes to a bounded `EventWorker` queue; trading callbacks run in a separate worker thread. Backpressure is deliberate (no dropped events). |
| 6 | Secret API key sent to public SSE endpoint | P1-11 | `/events` requests no longer send `X-API-Key`. |
| 7 | Feed had no stop/join/liveness API | P1-12 | `SSEFeed` provides `stop()`, `join()`, `is_alive()` and duplicate-start protection. |

**P1 files changed:** `config.yaml`, `exchange/client.py`, `exchange/feed.py`, `main.py`, `utils/event_worker.py`, `tests/test_mean_reversion.py`, `tests/test_exchange_client.py`, `tests/test_feed.py`, `tests/test_event_worker.py`

### P3 lifecycle & session (`p3-fixes` branch)

| # | Issue | Remediation | What changed |
|---|-------|-------------|--------------|
| 1 | No graceful shutdown — Ctrl-C left resting orders on the exchange | P0-11 / Fix #3 | SIGINT/SIGTERM transitions `RUNNING → QUIESCING → STOPPED`, blocks new intents, cancels all open/partial orders, exits nonzero if any remain unresolved. |
| 2 | Bot started after market open never traded (session_open stuck False) | P0-12 | `GET /session` called at startup; if session is `running`, bot arms immediately. |
| 3 | Strategy price deques leaked across trading windows | P0-13 | Session ID tracked; on ID change, all strategy price histories are cleared. |
| 4 | No emergency halt path | P0-14 | Standalone `halt()` function in `main.py`, reusable by signal handlers and a future kill-switch. |

**P3 files changed:** `exchange/client.py` (added `get_session()`), `main.py`, `tests/test_lifecycle.py`

---

## Remaining known issues

These are **not yet fixed** across either branch:

- Portfolio is read on every processed price event. No longer blocking the SSE reader (P1 fix), but still consumes the shared REST rate-limit allowance. P2 should replace with cached state / order-lifecycle updates.
- Pending orders are not included in exposure limits (P2 scope — OrderManager).
- Persistent signals can still create repeated orders (P2 scope — one-active-order policy).
- 504/timeout reconciliation is not implemented (P2 scope).
- Failed order submissions can still log a misleading success message (P2 scope — typed exchange errors).
- Feed replay sends `Last-Event-ID`, but explicit duplicate-event suppression has not been added.
- Bot heartbeat / feed-age publication is not implemented (Hour 2 feature).

---

## Never got to

- The other three instruments. The config supports per-instrument strategy assignment —
  add entries and pick a strategy type.
- Market-making. The skeleton is in `strategies/market_making.py.DISABLED` — inventory
  skew was wrong (tighten ask when long, bid when short). The desk knows the fix.
- Regime detection. James had a clean ATR-based approach — page 3 of the scan.
- Announcement handling. The exchange fires `announcement` SSE events during trading.
  The bot ignores them today. There's a `# TODO(S1)` hook in the dispatcher.

---

## Architecture note

Bot and dashboard are separate processes. The dashboard reads exchange market data
directly via SSE. For bot-specific state, there's a **read-only MCP server** (separate
repo) the dashboard can consume. Control commands from the dashboard to the bot are not
yet wired — the `halt()` path is ready to receive them.

---

## Integration contract

### Event flow (preserve this after merge)

```
SSE network reader → bounded event queue → worker callback → strategy/risk/order path
```

Do not run blocking exchange REST calls in the SSE reader thread.

### P2 — orders and risk

- Build `OrderManager` behind the worker callback.
- Account for filled plus pending exposure.
- Prevent persistent signals from creating multiple active orders.
- Reconcile 504/timeout outcomes with the original idempotency key.
- Only report submission success after acknowledgement or successful reconciliation.

### P3 integration notes for merge

- Use `SSEFeed.stop()` and `join()` during graceful shutdown.
- Stop or drain `EventWorker` only after new order intents have been blocked.

---

## Recommended integration check

After merging P1, P2 and P3:

1. Run the full bot test suite.
2. Force an SSE disconnect and verify reconnect/replay.
3. Confirm a handler failure does not stop feed ingestion.
4. Start during an already-open sandbox session.
5. Hold a persistent signal and verify only the permitted active order exists.
6. Inject a 504 and verify reconciliation by the original idempotency key.
7. Send SIGINT/SIGTERM and verify zero cancellable orders remain.

---

## Local configuration note

The working tree contains an unstaged sandbox endpoint/key change in `config.yaml`. The credential was deliberately excluded from P1 commit `252bc96`. Preserve or migrate it to environment-based configuration without committing the secret.
