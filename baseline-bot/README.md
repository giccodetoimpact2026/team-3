# Baseline Trading Bot

The starting Python bot for the GIC hackathon simulated exchange. It is
**functional but deliberately incomplete** — you inherit it and enhance it. See
[`HANDOVER.md`](HANDOVER.md) for the outgoing developer's notes and
[`AGENTS.md`](AGENTS.md) for AI-assistant context.

## What it does

- Connects to the exchange over REST (orders) and the **SSE feed** (market data).
- Runs **momentum on NOVA** and **mean-reversion on ELXR** (Combo 1). AXON / CRVX /
  DRFT are not traded by the baseline — adding them is your work.
- Accumulates its own price history from SSE `price` events (there is no candles
  endpoint) and logs to stdout.
- Provides a strategy-agnostic price-regime classifier in
  `strategies/regime.py`. It reports warmup, compression, range, neutral,
  up/down trend and up/down shock using ATR-like volatility plus directional
  efficiency. It is tested but not yet connected to strategy routing.

- Heartbeat every ~5 s (log line + `GET /status`), with a feed-stall watchdog
  that restarts the SSE stream from its cursor when no event has arrived for
  20 s. An order journal (`GET /orders?since=N`) is the bot→dashboard channel
  for order events: every submit, fill, cancel and reconciliation, including
  REJECTED and UNKNOWN submits the exchange's `GET /orders` never lists.
- Order-rate anomaly throttle: more than `risk.max_orders_per_minute` submits in
  a minute pauses new intents for `risk.anomaly_pause_seconds` (BUG-003).
- Startup config validation: placeholder key, non-positive sizes or lookbacks,
  thresholds outside (0, 1), bad ports or heartbeat timings refuse to start.

## What it deliberately lacks

Stop-losses, a portfolio-level exposure cap, an `announcement` handler and
structured logging. The other three instruments are wired on `origin/main`
(commit 79e7901) in the instrument-centric config shape.

## Run

```bash
pip install -r requirements.txt
# Keep the team key out of config.yaml and source control.
export EXCHANGE_API_KEY="your-team-key"
# Optional: defaults to https://api.codetoimpact.sg.
export EXCHANGE_BASE_URL="https://api.codetoimpact.sg"
python main.py
```

`main.py` reads the key from the environment only; `.env` in this folder is read by
the dashboard's dev server, so export it first (`set -a; source .env; set +a`).

The bot also serves a local control endpoint (`config.yaml` → `control`, default
`127.0.0.1:8092`; override with `BOT_CONTROL_PORT`):

| Route | Answer |
|---|---|
| `GET /status` | `state` (RUNNING / QUIESCING / STOPPED), `session_id`, `session_open`, `unresolved`, `heartbeat_seq`, `heartbeat_age_s`, `feed_state` (ALIVE / DEGRADED / DEAD), `feed_age_s`, `feed_connected`, `feed_reconnects`, `feed_restarts`, `worker_alive`, `orders: {active_orders, active_symbols, unknown_orders, blocked, paused, pause_reason, pause_remaining_s, journal_seq}` |
| `GET /orders?since=N` | `{seq, events}`: journal entries after `N`, each `{seq, at, kind, status, order_id, idempotency_key, symbol, side, quantity, price, remaining, filled, code, session_id}` |
| `POST /halt` | Same path as Ctrl-C: block new intents, `cancel_all()`, stop; answers the status plus `unresolved` |

Exit code is 0 only when the halt confirmed every cancel; an unresolved cancel
(timeout) exits 1 so a supervisor can tell.

Treat **sandbox as dev** and **live as prod** — switch via `config.yaml`; don't
develop against the live session.

## Test

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Layout

```
baseline-bot/
├── config.yaml                 # exchange + per-instrument strategy + risk
├── main.py                     # wiring and the run loop
├── exchange/{client.py, feed.py, orderbook.py, results.py, ratelimit.py}
├── orders/manager.py           # single owner of order state + journal + burst throttle
├── strategies/{base.py, momentum.py, mean_reversion.py, pairs.py, regime.py, dca.py}  # market_making.py.DISABLED (bonus)
├── risk/limits.py
├── utils/{logger.py, event_worker.py, control.py, heartbeat.py}
├── tests/                      # starter strategy tests
└── .github/workflows/ci.yml    # starter CI (lint + tests)
```

## Notes

- Auth is `X-API-Key` on every call.
- Prices/money are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — a sell only reduces/closes a held long.
