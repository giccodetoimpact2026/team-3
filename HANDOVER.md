# Bot Handover

Original author: Alex Chen (Lead Developer, Bot)
Updated: 18 September 2026 (SGT) — P1 and P3 bug-fix branches merged; dashboard P4/P5 branch added (see the dashboard section at the end).

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

### Heartbeat, journal and lifecycle hardening (`heartbeat-lifecycle` branch)

| # | Issue | Remediation | What changed |
|---|-------|-------------|--------------|
| 1 | No heartbeat; dashboard health was a REST ping | Backlog "true bot health", stakeholder note 7 | `utils/heartbeat.py` beats every 5 s (log + `GET /status` fields `heartbeat_seq`, `heartbeat_age_s`, `feed_state`, `feed_age_s`, …). Dashboard shows `BOT DEAD` after 15 s without a beat. |
| 2 | Half-open SSE socket only caught by the 30 s read timeout | Plan P1 H2 (feed-stall watchdog) | Heartbeat restarts the stream (`SSEFeed.restart()`, cursor kept) when no event for 20 s; DEGRADED between 10 and 20 s. |
| 3 | Dashboard could not see REJECTED/UNKNOWN submits or its own fills from the SSE `trade` event | Plan P4 H2 | `OrderManager` journals every submit/fill/cancel/reconciliation; `GET /orders?since=N` serves it; the dashboard feeds its lifecycle table and rejected counter from it. |
| 4 | `halt()` counted a timed-out cancel as done and did not block intents atomically | P0-11 / P0-14 | `halt()` now calls `orders.block_new_intents()` then `orders.cancel_all()`; UNKNOWN cancels are reported as unresolved and the process exits 1. Feed and worker are stopped too. |
| 5 | Session boundary left order state and idempotency keys from the old window | P0-13 | `on_session` calls `orders.reset_for_session(new_id)`; startup calls it with the bootstrapped id. |
| 6 | No order-rate anomaly throttle | BUG-003 | `risk.max_orders_per_minute` (20) / `anomaly_pause_seconds` (60): a burst pauses new intents, reported on `/status` and the dashboard pill. |
| 7 | Config validation only rejected the placeholder key | Plan P3 H2 | `validate_config()` rejects non-positive sizes/lookbacks, thresholds outside (0,1), unknown strategy types, negative caps, bad ports and heartbeat timings. |
| 8 | Dead `Bot` class in `main.py` (P2 merge leftover) and tests that only exercised it | hygiene | Removed. The "observation mode / live gate" those tests described never existed in the real run loop; see SPEC. |

### Instrument risk model (working tree, 18 September)

| # | Issue | Remediation | What changed |
|---|-------|-------------|--------------|
| 1 | No per-position stop-loss | BUG-009, Priya | `risk/model.py`: stop level = cost basis minus clamp(4 × ATR, min, max pct) per instrument profile; `confirm_ticks` breaches; sells into the bids through `risk/exits.py` (shared with the crash guard); cooldown blocks re-entry. |
| 2 | No portfolio-level exposure cap | BUG-008, Clarissa, Farrukh | Gross cap (90% of equity), per-symbol notional cap (35%), NOVA+AXON group cap (45%); measured 5-minute return correlation reported and alerted above 0.7. |
| 3 | Fixed trade size | BUG-014, James p.4, Priya (DRFT a fifth) | `adjust_intent` in `OrderManager.submit_intent`: quantity × profile `size_multiplier` × regime factor, capped by risk budget / stop distance, by a fraction of visible ask depth (`book_update` now consumed), and by the exposure caps. Sells untouched. |
| 4 | No drawdown alert | Priya | Baseline equity at the first portfolio read of the window; 10% alert, 15% alert plus `DEFENSIVE` mode (buys blocked; `alert_only` and `halt` available). |
| 5 | No regime detection | James p.3, Adeline | `risk/stats.py`: 10 s bars, true range, ATR(14), per-instrument thresholds with 3-bar hysteresis; momentum suspended in LOW, halved in NEUTRAL via `regime_factors`. |
| 6 | Risk parameters not visible | Priya | `GET /status` → `risk` (mode, drawdown, exposure, per-instrument regime/ATR/stop/loss/pause/last decision, alerts, share caps). |

Files: `risk/stats.py`, `risk/profiles.py`, `risk/model.py`, `risk/exits.py` (new); `risk/crash_guard.py` (uses the shared exit), `risk/limits.py` (no-op sizing hooks), `orders/manager.py` (`on_portfolio`, `adjust_intent`, `source`), `strategies/dca.py` (`source="dca"`), `exchange/client.py` (`realized_pnl` parsed), `main.py` (validation, wiring, `book_update`), `config.yaml` (`risk_model`), tests `test_market_stats.py`, `test_risk_model.py`, `test_exits.py` plus additions to `test_orders.py` and `test_config.py`. Design and calibration: `docs/risk-model-design.md`.

## Remaining known issues

- No announcement handler (S1); LLM work. No manual per-instrument pause/resume route (the model exposes `pause`/`resume`). No strategy switching by regime (the model exposes `regime(symbol)`).
- The strategies' 5 bps limit offset does not cross the 70 to 125 bps spreads seen on AXON, ELXR and DRFT in the sandbox, so those orders rest; the model exposes `best_bid`/`best_ask` for a spread-aware offset.
- Risk thresholds (stop floors 4 to 6%, regime bands, caps) are sandbox-calibrated starting points and need a human decision before a live window; `stop_loss.enabled: false` keeps sizing and caps without cost-basis stops.
- `origin/main` commit 79e7901 (extra instruments) still contains the duplicate unmanaged submit in `on_price` that 91ac303 removed; keep the deletion when merging.
- Feed age counts SSE *events* only (sseclient drops `: keep-alive` comments), so a market with no ticks for 20 s triggers one harmless reconnect.

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

---

## Dashboard (P4/P5 — `p4-p5-dashboard` branch)

Details in `bugfixes/dashboard-handoff.md` and `baseline-dashboard/README.md`.

Done: SSE feed replaces the 30 s timer (must-fix #1); stale values cleared on feed
error (must-fix #4); SGT everywhere (must-fix #7); dynamic instruments with zero/dash
rendering for untraded symbols (must-fix #6); session order counters; per-instrument
P&L from MCP `get_pnl` with `/portfolio` fallback; order lifecycle table; Gerald-schema
CSV export; HTML escaping of exchange strings; 42 Node unit tests; repo-root
`dashboard-ci.yml`.

Run: `cd baseline-dashboard && npm start` (reads the key from `EXCHANGE_API_KEY` or
`../baseline-bot/.env`), and the MCP server on port 8091 (see the dashboard README).

Done since: kill switch, heartbeat health pill, bot order journal feeding the lifecycle
table. Open: MCP conversational panel, price chart, dark mode.
