# Exchange API — Endpoints Guide

A plain-language guide to the GIC simulated exchange endpoints: what each one
does, when to call it, and what to watch out for. This is a companion to the
full contract in [`exchange-api-reference.md`](exchange-api-reference.md) — if
the two ever disagree, the reference wins.

---

## The basics (read this first)

- **Base URL:** `https://api.codetoimpact.sg`
- **Auth:** send your team's secret key as a header on **every** authenticated
  call:
  ```
  X-API-Key: c2i_...your-team-key...
  ```
  Not `Authorization`, not your `team_id` — only the secret `api_key`. The same
  key also authenticates the LLM service.
- **Money is integer cents.** `$42.50` is sent as `4250`. Never send floats — a
  decimal price is rejected.
- **Orders are limit-only and long-only.** No market orders; `side` is lowercase
  `"buy"` / `"sell"`; a sell only reduces/closes a position you already hold (no
  short-selling).
- **Every response uses a standard envelope.** Key your logic on the envelope's
  `status`, not on the exact HTTP code:
  ```json
  { "status": "success", "data": { ... } }        // success
  { "status": "error", "error": { "code": "...", "message": "...", "orderId": null } }  // error
  ```

---

## Quick reference

| Method | Endpoint | Auth | Purpose |
|--------|----------|:----:|---------|
| `GET` | `/health` | no | Is the process alive? |
| `GET` | `/ready` | no | Is the exchange accepting traffic yet? |
| `GET` | `/session` | no | Current trading window: status, clock, mode |
| `GET` | `/events` | no | **SSE feed** — real-time prices, trades, book, news |
| `GET` | `/instruments` | yes | List of tradeable symbols |
| `GET` | `/portfolio` | yes | Cash, positions, P&L, your fills |
| `GET` | `/orderbook/:symbol` | yes | Order book snapshot for one symbol |
| `POST` | `/orders` | yes | Place a limit order |
| `GET` | `/orders` | yes | All your orders this window |
| `GET` | `/orders/:id` | yes | One order's status |
| `DELETE` | `/orders/:id` | yes | Cancel a resting order |

---

## 1. Public endpoints (no key needed)

These carry only market-wide or operational data, so no authentication is
required.

### `GET /health` — is the process up?
Identifies the running exchange process (service name, build revision,
deployment venue). Its HTTP status is **always 200** while the process answers
at all — so **never read readiness off this status**; use `/ready` for that.
Use `/health` mainly to confirm you're pointed at the right server.

### `GET /ready` — is it serving yet?
Answers one question: is the exchange accepting traffic?
- **200** → ready.
- **503** → up but still starting; wait and retry.

Your bot should poll this until it returns 200 **before sending its first
order**, so a cold start doesn't throw.

### `GET /session` — what window are we in?
The single most important status call. Returns the current trading window:

| Field | Meaning |
|-------|---------|
| `id` | The window's id. **When it changes, everything you cached is stale.** |
| `status` | `running` · `paused` · `ended` |
| `mode` | `live` (scored) or `sandbox` (practice) |
| `started_at` | When the window opened |
| `elapsed_seconds` | Trading clock (pause time is subtracted, not wall-clock) |
| `duration_seconds` | Planned window length |
| `remaining_seconds` | Countdown to close, floored at 0 — build "flatten before the bell" logic on this |

Notes:
- Before the very first window of the day exists, `data` is `null` — treat it as
  "no market yet" and wait.
- The SSE `session` event reports the same lifecycle under different names:
  `open` / `halted` / `closed` ↔ `running` / `paused` / `ended`.

### `GET /events` — the real-time feed (SSE)
Server-Sent Events stream of live market data — **this is the correct channel
for live data; REST polling is slow and wasteful.** On connect you get a
snapshot (one `price` per instrument + current `session` state) so you can start
immediately. Event types:

- `price` — latest price for an instrument
- `trade` — a trade printed on the market (anyone's)
- `book_update` — order book change
- `session` — window lifecycle (`open` / `halted` / `closed`)
- `announcement` — market news events (can ripple across several instruments)

There is **no candles/OHLCV endpoint** — accumulate your own price history from
`price` events. Supports `Last-Event-ID` for replay after a disconnect.

---

## 2. Authenticated read endpoints

All require `X-API-Key`. They always answer for the **current window** (or, in
the gap between windows, the window that just closed).

### `GET /instruments` — what can I trade?
Returns every tradeable instrument (symbol, name, sector, starting/last price).
**Call this at startup and resolve symbols from it — never hardcode the
universe.** The list is stable for the whole event.

### `GET /portfolio` — my money and positions
Your full state for the current window: cash, reserved cash, realized /
unrealized P&L, total value, per-symbol positions, and your recent fills.

Key fields:
- `session_id` — the window these figures belong to. **When it changes, re-read;
  your cached position is stale.**
- `cash` / `reserved_cash` — free cash vs. cash locked by open buy orders.
- `positions[]` — one per held instrument: `quantity`, `reserved_qty` (locked by
  open sells), `avg_cost`, `last_price`, `market_value`, `unrealized_pnl`,
  `realized_pnl`.
- `trades[]` — your fills this window, newest first, from your side.

Served from a ~1s per-team cache when polled hard, so polling faster than once a
second just returns the same answer — use the SSE feed for live prices instead.

### `GET /orderbook/:symbol` — the book for one symbol
A point-in-time snapshot of resting bids and asks for one instrument. Use it for
an initial picture; **live updates come through the SSE `book_update` event**,
not by re-polling this.

---

## 3. Order endpoints (the trading surface)

### `POST /orders` — place a limit order
The core write call. Body:

```json
{
  "symbol":          "NOVA",
  "side":            "buy",        // "buy" or "sell", lowercase
  "quantity":        100,          // whole shares, 1..100000000
  "price":           4250,         // integer cents ($42.50), 1..100000000
  "idempotency_key": "uuid-per-order"
}
```

- **Limit only** — there is no `type` field.
- **`idempotency_key` is required** and must be unique per order (a UUID is the
  usual choice). Reusing one on purpose returns `409 ORDER_DUPLICATE`; omitting
  it is a `400`.
- **Matching is synchronous.** The response already tells you the outcome — a
  resting order comes back `OPEN`; one that crosses comes back `FILLED` /
  `PARTIALLY_FILLED` with fills in `data.trades`. You won't see `PENDING`.

Response is **`201 Created`**; the assigned order id is at **`data.order.id`** —
save it so you can cancel or check the order later.

```json
{ "status": "success",
  "data": {
    "order":  { "id": "uuid", "status": "OPEN", "symbol": "NOVA", "side": "buy",
                "price": 4250, "quantity": 100, "remaining_qty": 100,
                "team_name": "TEAM 03" },
    "trades": []
  } }
```

### `GET /orders` — all my orders this window
Returns your order history for the current window as a list of order objects.
Each entry carries its `idempotency_key` alongside `id`, `status`, and
`remaining_qty` — which is how you **recover from a timeout**: if a `POST
/orders` never returned, call this and find the order whose `idempotency_key`
matches the one you sent, rather than blindly resubmitting.

### `GET /orders/:id` — one order's status
Look up a single order by the **order id** the exchange assigned (not the
idempotency key). Same shape as the entries in `GET /orders`. A key sent here is
an unknown order (`404`).

### `DELETE /orders/:id` — cancel a resting order
Removes the remaining quantity of an open order from the book, by order id.
Returns `200`. Cancelling an order that's already filled or cancelled returns
`400 ORDER_NOT_CANCELLABLE`.

> The baseline bot has **no graceful shutdown** — if you kill it, its resting
> orders stay on the exchange. Wiring a cancel-on-exit handler around this
> endpoint is one of the known gaps to fix.

---

## Order lifecycle

```
PENDING → OPEN → PARTIALLY_FILLED → FILLED / CANCELLED / REJECTED
```

`PENDING` is a transient internal state you won't observe on responses (matching
is synchronous).

---

## Errors worth handling

Every error uses the envelope `{ "status": "error", "error": { code, message,
orderId } }`. The most common trading rejections:

| HTTP | Code | What it means / do |
|------|------|--------------------|
| 400 | `ORDER_INVALID` | Bad body, missing/blank `idempotency_key`, non-positive or decimal price. Fix, don't retry as-is |
| 400 | `ORDER_SIDE_INVALID` | `side` must be lowercase `"buy"`/`"sell"` |
| 400 | `ORDER_QUANTITY_INVALID` | `quantity` must be a positive whole number |
| 402 | `INSUFFICIENT_FUNDS` | Not enough cash — reduce size or cancel open buys |
| 400 | `INSUFFICIENT_POSITION` | Selling more than you hold (long-only) — buy first |
| 409 | `ORDER_DUPLICATE` | Idempotency key already used — do **not** resubmit |
| 409 | `SELF_MATCH` | Would trade against your own resting order — cancel it first, use a new key |
| 422 | `PRICE_OUT_OF_BAND` | Priced too far from market — reprice closer, don't retry as-is |
| 429 | `RATE_LIMIT_EXCEEDED` | Back off; read `Retry-After` |
| 503 | `MARKET_HALTED` / `SESSION_CLOSED` | Wait for the session to open |
| 504 | `GATEWAY_TIMEOUT` | Treat as unknown — recover via `GET /orders` on your key |
| 400 | `UNKNOWN_SYMBOL` | Symbol isn't tradeable — resolve via `GET /instruments` |
| 400 | `ORDER_NOT_CANCELLABLE` | Cancelling an already filled/cancelled order |
| 401 | `UNAUTHORIZED` | Missing/wrong `X-API-Key` |
| 404 | `NOT_FOUND` | Unknown order id, or not your order |

---

## Rate limits (per team, rolling 30s windows)

Three independent tiers; every call counts, accepted or refused:

| Tier | Endpoints | Limit |
|------|-----------|-------|
| Orders | `POST /orders` | **100** / 30s |
| Order book | `GET /orderbook/:symbol` | **600** / 30s |
| Everything else | `/instruments`, `/orders`, `/orders/:id`, `DELETE /orders/:id`, `/portfolio` (shared) | **300** / 30s |

- Read `X-RateLimit-Remaining` on each response to slow down before you're
  refused; a `429` adds `Retry-After` (seconds until **one** slot frees).
- Public endpoints (`/health`, `/ready`, `/session`, `/events`) are **not**
  limited — but don't lean on polling reads for live data; that's what the SSE
  feed is for.

---

## Trading windows — the one gotcha

The day runs as **three separate windows**, each its own world. When the session
`id` changes, your cash, positions, resting orders, fills, and prices all reset
and anything you cached is stale. Re-read `GET /portfolio` on a boundary, and
reset any "since the start" metric (drawdown, session P&L) on the new
`session_id` — not on process start. Idempotency keys are scoped per window too,
so reusing a key in the next window places a new order.
