# Baseline Trading Bot

The starting Python bot for the GIC hackathon simulated exchange. It is
**functional but deliberately incomplete** — you inherit it and enhance it. See
[`HANDOVER.md`](HANDOVER.md) for the outgoing developer's notes and
[`AGENTS.md`](AGENTS.md) for AI-assistant context.

## What it does

- Connects to the exchange over REST (orders) and the **SSE feed** (market data).
- Trades all five instruments: **momentum on NOVA and CRVX**, **mean-reversion on
  ELXR and DRFT** (asymmetric bands on DRFT), **leader-follower on AXON** off NOVA's
  breakouts. Limit prices come from the cached order book when it is fresh, with a
  spread filter on entries only.
- Accumulates its own price history from SSE `price` events (there is no candles
  endpoint) and logs to stdout.
- Provides a strategy-agnostic price-regime classifier in
  `strategies/regime.py` and a regime policy (`config.yaml` → `regime`, off by
  default) that can trim or veto buys by regime, never sells.
- News engine: keyword bias on announcements, refined by the organizer LLM
  service (`config.yaml` → `news`), which can enlarge, generate or suppress a signal.
- Heartbeat every ~5 s (log line + `GET /status`), with a feed-stall watchdog
  that restarts the SSE stream from its cursor when no event has arrived for
  20 s. An order journal (`GET /orders?since=N`) is the bot→dashboard channel
  for order events: every submit, fill, cancel and reconciliation, including
  REJECTED and UNKNOWN submits the exchange's `GET /orders` never lists.
- Order-rate anomaly throttle: more than `risk.max_orders_per_minute` submits in
  a minute pauses new intents for `risk.anomaly_pause_seconds` (BUG-003).
- Entry cooldown (`risk.entry_cooldown_seconds`): after an acknowledged strategy
  buy on a symbol, further strategy buys on it wait. Closes the burst gap where a
  buy at the ask fills synchronously and the next tick re-fires the same signal.
  Sells, stops and the crash guard are never held back.
- Startup config validation: placeholder key, non-positive sizes or lookbacks,
  thresholds outside (0, 1), bad ports or heartbeat timings refuse to start.
- Instrument risk model (`config.yaml` → `risk_model`, design in
  `docs/risk-model-design.md`): per-instrument profiles (NOVA trending, AXON
  follows NOVA, CRVX growth momentum, DRFT thin at a fifth of NOVA's size, ELXR
  range-bound) size every buy by personality, 10-second-bar ATR regime and
  visible book depth; **the bot's only stop-loss**, volatility-scaled from cost
  basis and floored per instrument, sells into the bids after 3 confirming ticks
  and then refuses the re-buy for a cooldown; a 15% drawdown of starting equity
  blocks buys; an NOVA+AXON group cap, per-symbol notional cap and gross cap bound
  exposure; a per-symbol session loss pauses that symbol's buys. Sells are never
  reduced. Everything is reported under `risk` on `GET /status` (regime, ATR, stop
  level, drawdown, exposure, alerts, last sizing decision) and adds no REST calls
  on the tick path.
- Take-profit exits (`config.yaml` → `risk.exits`): trailing for the momentum
  names, a fixed target for DRFT, none for ELXR (mean-reversion's sell leg is its
  take-profit). Checked before the strategy on every tick.
- Crash guard on every discovered instrument: 15% under the window high for 3
  ticks dumps the position into the bids, then cools down.
- Correlated-group cap in notional cents (`risk.correlated_groups`, BUG-008) on top
  of the per-instrument share caps.

## What it deliberately lacks

Structured logging, a manual per-instrument pause/resume route (the model
exposes `pause`/`resume`), regime-based strategy routing (the classifier and the
model both expose a regime), and edge-triggered strategy signals (the entry
cooldown bounds the burst rather than removing its cause). The DCA overlay
(`config.yaml` → `dca`) ships disabled now that every instrument has a strategy.

## Run

```bash
pip install -r requirements.txt
# Keep the team key out of config.yaml and source control.
export EXCHANGE_API_KEY="your-team-key"
# Optional: defaults to https://api.codetoimpact.sg.
export EXCHANGE_BASE_URL="https://api.codetoimpact.sg"
python main.py
```

`main.py` reads the key from the environment only and refuses to start with the
placeholder in `config.yaml`; `.env` in this folder is read by the dashboard's dev
server, so export it first (`set -a; source .env; set +a`).

The bot also serves a local control endpoint (`config.yaml` → `control`, default
`127.0.0.1:8092`; override with `BOT_CONTROL_PORT`):

| Route | Answer |
|---|---|
| `GET /status` | `state` (RUNNING / QUIESCING / STOPPED), `session_id`, `session_open`, `unresolved`, `heartbeat_seq`, `heartbeat_age_s`, `feed_state` (ALIVE / DEGRADED / DEAD), `feed_age_s`, `feed_connected`, `feed_reconnects`, `feed_restarts`, `worker_alive`, `orders: {active_orders, active_symbols, unknown_orders, blocked, paused, pause_reason, pause_remaining_s, journal_seq}`, `crash_guard`, `dca`, `limits`, `take_profit`, `risk: {mode, equity, baseline_equity, drawdown_start_pct, gross_exposure_pct, risk_at_stops_pct, groups, instruments: {SYMBOL: {regime, atr_pct, stop_level, stop_pct, loss_pct, session_pnl, paused, last_decision}}, correlation, alerts, max_position, exits}` |
| `GET /orders?since=N` | `{seq, events}`: journal entries after `N`, each `{seq, at, kind, status, order_id, idempotency_key, symbol, side, quantity, price, remaining, filled, code, session_id}` |
| `GET /news/config`, `POST /news/config` | Read or patch the news engine's live configuration |
| `POST /halt` | Same path as Ctrl-C: block new intents, `cancel_all()`, stop; answers the status plus `unresolved` |

Exit code is 0 only when the halt confirmed every cancel; an unresolved cancel
(timeout) exits 1 so a supervisor can tell.

Treat **sandbox as dev** and **live as prod** — switch via `config.yaml`; don't
develop against the live session.

## Backtest (parameter sweep)

Replay a recorded price series through the **real** strategy classes across a
grid of parameters per instrument and see which would have performed best. It's
hypothesis generation, not an auto-tuner — results are never written back to
`config.yaml`.

```bash
# 1. Capture a session (read-only; writes recordings/feed-<UTC>.jsonl).
export EXCHANGE_API_KEY="your-team-key"
python record_feed.py            # Ctrl-C to stop, or it stops when the window ends

# 2. Run the sweep. With no --input it replays the committed sample.
python run_backtest.py --input recordings/feed-<UTC>.jsonl
#    -> writes recordings/results.json

# 3. Surface it on the dashboard.
cp recordings/results.json ../baseline-dashboard/data/backtest.json
```

The dashboard's **Backtest** tab (`backtest.html`, linked from the top nav) reads
that JSON: a ranked grid per instrument, current-vs-best comparison, and an
equity curve. The fill model is a next-tick marketable fill over a price-only
series (no order book) — optimistic by construction; the tab shows the caveats
and a `slippage_bps` knob (`--slippage-bps`).

## Test

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Layout

```
baseline-bot/
├── config.yaml                 # exchange + per-instrument strategy + risk + risk_model
├── main.py                     # wiring, the price path and the run loop
├── exchange/{client.py, feed.py, orderbook.py, results.py, ratelimit.py}
├── orders/manager.py           # single owner of order state + journal + burst throttle
├── strategies/{base.py, momentum.py, mean_reversion.py, pairs.py, dca.py,
│               regime.py, regime_adapter.py, regime_policy.py}
├── news/{engine.py, sentiment.py}   # announcement bias + organizer-LLM sentiment
├── risk/{limits.py, exits.py, flatten.py, crash_guard.py, stats.py, profiles.py, model.py}
│                               # share + group caps, take-profit legs, sell-into-bids
│                               # flatten path, 15% crash guard, bar/ATR/regime/depth
│                               # stats, instrument profiles, the risk model (sizing,
│                               # stop-loss, drawdown, exposure caps)
├── utils/{logger.py, event_worker.py, control.py, heartbeat.py}
├── record_feed.py              # capture the SSE price feed to recordings/*.jsonl
├── run_backtest.py             # replay + parameter sweep -> results.json
├── backtest/                   # loader, fill model, replay engine, sweep
├── tests/
└── .github/workflows/ci.yml    # starter CI (lint + tests)
```

## Notes

- Auth is `X-API-Key` on every call.
- Prices/money are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — a sell only reduces/closes a held long.
