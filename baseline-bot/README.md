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

## What it deliberately lacks

Stop-losses, SSE reconnection, graceful shutdown, an `announcement` handler,
structured logging, and any bot-state emission. There are also a handful of
inherited bugs — the handover notes point at them.

## Run

```bash
pip install -r requirements.txt
# Keep the team key out of config.yaml and source control.
export EXCHANGE_API_KEY="your-team-key"
# Optional: defaults to https://api.codetoimpact.sg.
export EXCHANGE_BASE_URL="https://api.codetoimpact.sg"
python main.py
```

The bot also serves a local control endpoint (`config.yaml` → `control`, default
`127.0.0.1:8092`; override with `BOT_CONTROL_PORT`). `GET /status` reports the
lifecycle state; `POST /halt` runs the same path as Ctrl-C (block intents, cancel
open orders, stop). The dashboard's HALT BOT button uses it through its own proxy.

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
├── config.yaml                 # exchange + per-instrument strategy + risk
├── main.py                     # wiring and the run loop
├── exchange/{client.py, feed.py}
├── strategies/{base.py, momentum.py, mean_reversion.py, pairs.py, crash_buy.py, regime.py}
├── risk/limits.py
├── record_feed.py              # capture the SSE price feed to recordings/*.jsonl
├── run_backtest.py             # replay + parameter sweep -> results.json
├── backtest/                   # loader, fill model, replay engine, sweep (reuses the real strategies)
├── utils/logger.py
├── tests/                      # starter strategy tests + backtest tests
└── .github/workflows/ci.yml    # starter CI (lint + tests)
```

## Notes

- Auth is `X-API-Key` on every call.
- Prices/money are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — a sell only reduces/closes a held long.
