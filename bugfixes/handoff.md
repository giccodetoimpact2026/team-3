# P1 bot bug-fix handoff

Date: 18 September 2026 (SGT)  
Branch: `p1/fix`  
Commit: `252bc96 fix(bot): supervise feed and isolate event processing`

## Summary

P1 feed-supervision work is complete for the initial integration pass. Two baseline defects were fixed first, followed by SSE reconnection, callback isolation and removal of blocking trading work from the network-reader thread.

The bot test suite passes, and read-only connectivity to the organizer sandbox was verified. No orders were placed during verification.

## Bugs fixed

### 1. ELXR mean-reversion uses the wrong lookback

Tracker: `BUG-012`  
Remediation reference: `P0-10`

Before:

- Production configuration used a five-sample lookback.
- This made ELXR interpret a falling trend as repeated mean-reversion opportunities.

After:

- `baseline-bot/config.yaml` uses exactly 15 samples.
- A regression test proves that the strategy produces no signal after only 14 samples and becomes eligible on the fifteenth sample.

### 2. Missing portfolio instruments crash the bot

Tracker: `BUG-019`  
Remediation reference: `P0-09`

Before:

- `ExchangeClient.get_positions()` indexed the portfolio map directly.
- An untraded instrument absent from `positions[]` raised `KeyError` and could terminate price handling.

After:

- An absent instrument returns quantity `0`.
- Tests cover both missing and present positions.

### 3. SSE disconnect permanently stops market processing

Tracker: `BUG-011`  
Remediation reference: `P0-05`

Before:

- The listener caught every exception and silently exited.
- `_last_event_id` was recorded but never used.

After:

- The feed reconnects with bounded exponential backoff and jitter.
- Reconnect requests send `Last-Event-ID` when a cursor exists.
- HTTP status and `Content-Type` are validated before parsing the stream.
- Connection failures are logged instead of swallowed.

### 4. One callback exception kills the SSE listener

Remediation reference: `P0-06`

Before:

- Strategy or REST exceptions escaped `_emit()` into the listener's outer catch.
- The listener thread died while the main process continued sleeping.

After:

- Handler failures are logged and isolated.
- The listener continues reading subsequent events.
- A regression test invokes a failing handler and verifies that the exception does not escape dispatch.

### 5. Trading logic blocks the network-reader thread

Remediation reference: `P0-07`

Before:

- The SSE reader directly ran `on_price()`.
- `on_price()` performs synchronous REST work, so slow portfolio calls prevented the reader from consuming market events.

After:

- The SSE reader submits validated events to a bounded `EventWorker` queue.
- Trading callbacks execute sequentially in a separate worker thread.
- Backpressure is deliberate: events are not silently dropped.
- Tests verify ordered processing and confirm that one handler failure does not kill the worker.

### 6. Secret API key was sent to a public SSE endpoint

Remediation reference: `P1-11`

Before:

- `/events` received `X-API-Key`, although the exchange contract defines it as public.

After:

- SSE requests contain only `Accept: text/event-stream` plus an optional `Last-Event-ID`.
- The constructor still accepts the key temporarily for compatibility with current wiring, but it is not transmitted.

### 7. Feed cannot be controlled or inspected

Remediation reference: `P1-12`

Before:

- The daemon feed thread had no stop, join or liveness API.

After:

- `SSEFeed` provides `stop()`, `join()`, `is_alive()` and duplicate-start protection.
- These methods are ready for P3's graceful-shutdown integration.

## Files changed

- `baseline-bot/config.yaml`
- `baseline-bot/exchange/client.py`
- `baseline-bot/exchange/feed.py`
- `baseline-bot/main.py`
- `baseline-bot/utils/event_worker.py`
- `baseline-bot/tests/test_mean_reversion.py`
- `baseline-bot/tests/test_exchange_client.py`
- `baseline-bot/tests/test_feed.py`
- `baseline-bot/tests/test_event_worker.py`

## Verification evidence

Full bot regression suite:

```text
...............                                                          [100%]
15 passed in 0.23s
```

Read-only sandbox smoke test:

```text
sandbox read smoke: PASS
session sandbox running
instruments 5
portfolio_positions 5
```

No order endpoint was called during the sandbox smoke test.

## Integration contract for teammates

### P2 - orders and risk

- Continue consuming `price` events through the worker; do not move REST/order work back into `SSEFeed`.
- Build `OrderManager` behind the worker callback.
- Account for filled plus pending exposure.
- Prevent persistent signals from creating multiple active orders.
- Reconcile 504/timeout outcomes with the original idempotency key.
- Only report submission success after acknowledgement or successful reconciliation.

### P3 - lifecycle and session

- Use `SSEFeed.stop()` and `join()` during graceful shutdown.
- Stop or drain `EventWorker` only after new order intents have been blocked.
- Add `GET /session` bootstrap so starting during an open window works.
- Reset price history, cached portfolio and pending orders when the session ID changes.
- Implement the bot-owned halt sequence: block intents, cancel/reconcile orders, stop feed/worker, flush logs.

### Merge caution

P1 changed `baseline-bot/main.py` to insert the event worker. P2/P3 should preserve this flow:

```text
SSE network reader -> bounded event queue -> worker callback -> strategy/risk/order path
```

Do not run blocking exchange REST calls in the SSE reader thread.

## Known remaining issues

These were not fixed in the P1 commit:

- Portfolio is still read during each processed price event. It is no longer blocking the SSE reader, but it can still consume the shared REST allowance. P2 should replace this with coherent cached state/order lifecycle updates.
- Pending orders are not included in exposure limits.
- Persistent signals can still create repeated orders.
- 504/timeout reconciliation is not implemented.
- Failed order submissions can still be followed by a misleading success log.
- Graceful shutdown and cancel-all behavior are not implemented.
- Session bootstrap and session-boundary resets are not implemented.
- Feed replay sends `Last-Event-ID`, but explicit duplicate-event suppression has not yet been added.
- Bot heartbeat/feed-age publication is not implemented.

Do not treat the bot as live-safe until P2 and P3 close these items and the combined fault tests pass.

## Local configuration note

The working tree contains an unstaged sandbox endpoint/key change in `baseline-bot/config.yaml`. The credential was deliberately excluded from commit `252bc96` and is not reproduced in this handoff. Preserve or migrate it to environment-based configuration without committing the secret.

## Recommended integration check

After merging P1, P2 and P3:

1. Run the full bot test suite.
2. Force an SSE disconnect and verify reconnect/replay.
3. Confirm a handler failure does not stop feed ingestion.
4. Start during an already-open sandbox session.
5. Hold a persistent signal and verify only the permitted active order exists.
6. Inject a 504 and verify reconciliation by the original idempotency key.
7. Send SIGINT/SIGTERM and verify zero cancellable orders remain.

