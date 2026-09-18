# P1 handoff - bot feed and baseline fixes

Branch: `p1/fix`  
Primary implementation commit: `252bc96`

## Done

- ELXR production lookback corrected from 5 to 15 samples (`BUG-012`).
- Missing/untraded portfolio positions return zero instead of raising `KeyError` (`BUG-019`, bot side).
- SSE reconnects with exponential backoff, jitter and `Last-Event-ID` (`BUG-011`).
- SSE response status/content type is checked before parsing.
- Callback exceptions are logged without killing the feed listener.
- Trading callbacks run through a bounded worker queue, not the SSE network reader.
- Public `/events` no longer receives the team API key.
- Feed now exposes `stop()`, `join()` and `is_alive()` for lifecycle work.

## Verified

- Bot tests: `15 passed`.
- Read-only sandbox check passed: sandbox session running, 5 instruments, 5 portfolio positions.
- No orders were submitted during verification.

## Keep this integration shape

```text
SSE reader -> bounded event queue -> worker callback -> strategy/risk/order path
```

Do not move blocking REST or order calls back into `SSEFeed`.

## Still owned by P2/P3

- Pending-order exposure and one-active-order policy.
- 504 reconciliation by idempotency key.
- Honest submit-result logging.
- Session bootstrap/reset.
- Graceful shutdown, halt and cancellation reconciliation.
- Heartbeat/feed-age reporting and replay duplicate suppression.

## Local config warning

`baseline-bot/config.yaml` has a local, unstaged sandbox credential change. It is intentionally excluded from this branch's commits. Do not commit it.

For the full record, see `bugfixes/handoff.md`.

