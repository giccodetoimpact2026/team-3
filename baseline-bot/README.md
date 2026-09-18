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

- Heartbeat every ~5 s (log line + `GET /status`), with a feed-stall watchdog
  that restarts the SSE stream from its cursor when no event has arrived for
  20 s. An order journal (`GET /orders?since=N`) is the bot→dashboard channel
  for order events: every submit, fill, cancel and reconciliation, including
  REJECTED and UNKNOWN submits the exchange's `GET /orders` never lists.
- Order-rate anomaly throttle: more than `risk.max_orders_per_minute` submits in
  a minute pauses new intents for `risk.anomaly_pause_seconds` (BUG-003).
- Startup config validation: placeholder key, non-positive sizes or lookbacks,
  thresholds outside (0, 1), bad ports or heartbeat timings refuse to start.
- Instrument risk model (`config.yaml` → `risk_model`, design in
  `docs/risk-model-design.md`): per-instrument profiles (NOVA trending, AXON
  follows NOVA, CRVX growth momentum, DRFT thin at a fifth of NOVA's size, ELXR
  range-bound) size every buy by personality, 10-second-bar ATR regime and
  visible book depth; a volatility-scaled stop-loss from cost basis sells into
  the bids; a 15% drawdown of starting equity blocks buys; an NOVA+AXON group
  cap, per-symbol notional cap and gross cap bound exposure; a per-symbol
  session loss pauses that symbol's buys. Sells are never reduced. Everything
  is reported under `risk` on `GET /status` (regime, ATR, stop level, drawdown,
  exposure, alerts, last sizing decision) and adds no REST calls on the tick
  path.

## What it deliberately lacks

An `announcement` handler, structured logging, a manual per-instrument
pause/resume route (the model exposes `pause`/`resume`), regime-based strategy
routing (the model exposes `regime(symbol)`), and spread-aware limit pricing.
The other three instruments are wired on `origin/main` (commit 79e7901) in the
instrument-centric config shape.

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
| `GET /status` | `state` (RUNNING / QUIESCING / STOPPED), `session_id`, `session_open`, `unresolved`, `heartbeat_seq`, `heartbeat_age_s`, `feed_state` (ALIVE / DEGRADED / DEAD), `feed_age_s`, `feed_connected`, `feed_reconnects`, `feed_restarts`, `worker_alive`, `orders: {active_orders, active_symbols, unknown_orders, blocked, paused, pause_reason, pause_remaining_s, journal_seq}`, `crash_guard`, `dca`, `risk: {mode, equity, baseline_equity, drawdown_start_pct, gross_exposure_pct, risk_at_stops_pct, groups, instruments: {SYMBOL: {regime, atr_pct, stop_level, stop_pct, loss_pct, session_pnl, paused, last_decision}}, correlation, alerts, max_position, exits}` |
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
├── exchange/{client.py, feed.py, results.py, ratelimit.py}
├── orders/manager.py           # single owner of order state + journal + burst throttle
├── strategies/{base.py, momentum.py, mean_reversion.py}  # market_making.py.DISABLED (bonus)
├── risk/{limits.py, crash_guard.py, exits.py, stats.py, profiles.py, model.py}
│                               # share caps, 15% crash guard, shared sell-into-bids exit,
│                               # bar/ATR/regime/depth/correlation stats, instrument profiles,
│                               # the risk model (sizing, stop-loss, drawdown, exposure caps)
├── utils/{logger.py, event_worker.py, control.py, heartbeat.py}
├── tests/                      # starter strategy tests
└── .github/workflows/ci.yml    # starter CI (lint + tests)
```

## Notes

- Auth is `X-API-Key` on every call.
- Prices/money are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — a sell only reduces/closes a held long.
