# GIC Simulated Exchange — API Reference
### Student-facing | Hackathon platform

> Use this as the contract for your bot and dashboard. If anything here disagrees
> with old code comments in the baseline, **this document wins.** The live base URLs
> are in the table below. The exchange's read-only endpoints and the LLM service are
> available from registration; order submission opens when the exchange goes into
> sandbox mode as coding starts. (The MCP read server
> is one you run yourself — see §6.)

---

## Authentication

Every team holds two distinct values — a **public `team_id`** and a **secret
`api_key`**. The organizers provision both before the event; there is no
self-registration endpoint.

Send the **`api_key`** as `X-API-Key` on **every** authenticated REST call:

```
X-API-Key: <api_key>
```

Not `Authorization`, not `X-Team-Token`, and **not your `team_id`.** Your `team_id`
is your public identity — orders, portfolios and the order book all name your
team rather than identifying it. Only the `api_key` authenticates. Keep it
private.

Your identity is not your capital. Cash belongs to a trading window: it is
granted when a window opens, so `GET /portfolio` is where you read your
balance. Teams are provisioned before a window opens — mid-window registration
does not exist.

> The organizers hand you your `team_id` + `api_key` at registration. You can
> also read your team's key on the event site's profile page once the doors
> open — `https://codetoimpact.sg/profile` → ◆ ACCOUNT ◆.

---

## Base URLs

| Channel | URL |
|---------|-----|
| REST (exchange) | `https://api.codetoimpact.sg` |
| SSE feed | `GET /events` on the same host |
| Event site (leaderboard, your team key) | `https://codetoimpact.sg` |
| MCP read server | `http://localhost:8090` (you run this; see §6) — a separate port from the exchange's 8080 |
| LLM service | `https://llm.codetoimpact.sg` (provided; see §7) — a third service, separate from the exchange and MCP |

---

## 1. REST endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/orders` | Place a limit order |
| `DELETE` | `/orders/:id` | Cancel an open order |
| `GET` | `/orders/:id` | Order status by its **order id** (the `id` the exchange assigned) |
| `GET` | `/orders` | Your orders in the current window (see "Trading windows") |
| `GET` | `/portfolio` | Holdings, cash, P&L for the current window (positions are part of this response) |
| `GET` | `/instruments` | All tradeable instruments — **call this at startup** (requires `X-API-Key`) |
| `GET` | `/orderbook/:symbol` | Order book **snapshot** (live updates come via SSE) (requires `X-API-Key`) |
| `GET` | `/health` | Exchange health check (public, no auth) — always 200 while the process is answering |
| `GET` | `/ready` | Is the exchange accepting traffic? (public, no auth) — 200 ready, 503 not yet |
| `GET` | `/session` | Current trading-session status + clock (public). Reports `running`/`paused`/`ended` — the same lifecycle the SSE `session` event reports as `open`/`halted`/`closed`. |

### Session status response

`GET /session` is public and returns the standard envelope. It is how you know
whether the market is open and how long is left — order entry is only accepted
while `status` is `running` (cancels also work while `paused`).

```json
{
  "status": "success",
  "data": {
    "id": "uuid",
    "status": "running",
    "mode": "live",
    "started_at": "2026-08-16T09:00:00Z",
    "elapsed_seconds": 412,
    "duration_seconds": 1800,
    "remaining_seconds": 1388
  }
}
```

| Field | Meaning |
|-------|---------|
| `id` | The window. **When this changes, everything you cached is stale** — see "Trading windows" |
| `status` | `running` · `paused` · `ended`. The SSE `session` event carries the same lifecycle under the names `open` · `halted` · `closed` |
| `mode` | `live` (scored) or `sandbox` (practice) |
| `started_at` | When the window opened |
| `elapsed_seconds` | The **trading clock**, not wall-clock: time spent paused is subtracted. Frozen at the close for an ended window rather than growing forever |
| `duration_seconds` | The planned length. Every window has one: if the organizers don't name a length the exchange applies the default for that mode, so in practice this is always a number |
| `remaining_seconds` | `duration_seconds − elapsed_seconds`, floored at 0 — and **always 0 once `status` is `ended`**, even if the window was ended early |

Before the first window of the day has ever been created, `data` is
explicitly **`null`** (not an error, not an empty object) — treat it as "no
market yet" and wait.

`remaining_seconds` is the countdown to build "flatten before the bell" logic
on. Because it is pause-aware, a window paused for ten minutes gives your bot
those ten minutes back rather than silently eating them.

Both clock fields are nullable in the wire format even though the exchange
always populates them, so a defensive client should tolerate `null` rather
than assume a number.

The response may also carry operator fields (such as `site_lock`) that are not
part of the trading surface; ignore anything not listed above.

### Health and readiness responses

Both endpoints are public and return the standard envelope.

`GET /health` identifies the process. `deployment_role` identifies the venue
(`local`, `canary`, or `production`), and `build_revision` identifies the
deployed source revision. Its HTTP status is **always 200** while the process
is answering at all — never read readiness off this status code.

```json
{
  "status": "success",
  "data": {
    "status": "healthy",
    "service": "exchange-platform",
    "deployment_role": "production",
    "build_revision": "0123456789abcdef0123456789abcdef01234567",
    "ready": true,
    "components": { }
  }
}
```

`GET /ready` answers one question: is this exchange accepting traffic yet?
**200** once it is, **503** while it is still starting up. Both endpoints carry
the same two readiness fields:

- `ready` — the boolean form of the same answer.
- `components` — internal subsystem identifiers, each reporting `"ok"` or
  `"failed"`. These are opaque server-side labels for the operations team. They
  are not part of the API you build against, they may change without notice,
  and nothing in them reflects trading state.

A 503 (or `"ready": false`) means the exchange is up but not serving yet: wait
and retry rather than treating it as an outage. Your bot should tolerate this
at startup — poll `/ready` until it returns 200 before sending your first
order.

```json
{
  "status": "success",
  "data": {
    "ready": false,
    "components": {
      "subsystem_a": "ok",
      "subsystem_b": "failed"
    }
  }
}
```

### Trading windows — what belongs to a window

The day runs as **three separate trading windows**, and each one is its own
world. Everything you can trade with belongs to the window it happened in, so a
new window opens with a clean slate — nothing is carried over, and nothing you
did in the last window is deleted either.

| Belongs to a window (starts fresh in the next one) | Yours for the whole event |
|---|---|
| Your **cash** — every window opens at the starting balance | Your **`team_id` and `api_key`** — one identity, all day |
| Your **positions** — each window opens with the organizer-set starting positions (identical for every team; possibly none) | The **instrument list** (`GET /instruments`) |
| Your **realised P&L** | |
| Your **orders**, including anything left resting | |
| Your **fills** | |
| Every instrument's **price** — each window opens at `starting_price` | |
| The **market event announcements** — each window runs its own news; nothing from the last one still applies | |

Two consequences worth designing for:

- **P&L is per window.** Each window is ranked on its own (1st = 10 points …
  10th = 1, 11th and below = 0) and the final leaderboard averages your three placings, so a bad
  window costs one bad placing rather than compounding — and a good one
  doesn't carry.
  Anything you measure "since the start" (a drawdown limit, a session P&L
  figure) should reset when the session `id` changes, not when your process
  starts.
- **Your own state goes stale at the boundary.** Cached positions, cash,
  resting order ids and last prices from the previous window are all wrong the
  instant the next one opens. Re-read `GET /portfolio` when the session `id`
  changes, and treat the `price` events published at the open as your new
  reference — do not carry the previous window's closing price into the next.

What the read endpoints answer:

- **While a window is running**, `GET /portfolio`, `GET /orders` and
  `GET /orderbook/:symbol` are all about that window.
- **Between windows**, they answer for the window that just closed, so you can
  review how you finished. Order entry is rejected with `SESSION_CLOSED` until
  the next one opens.
- **Idempotency keys are scoped to a window too** — reusing `"open-nova-1"` in
  the next window places a new order rather than replaying the last one.
- **Before the first window opens**, `GET /portfolio` returns the same
  shape with every figure `0`, `session_id` `""` and empty `positions` /
  `trades`: there is no portfolio until a window opens and funds you (with the
  $10,000,000 above). Do not size anything off those zeros.

`GET /portfolio` names its window, so you don't have to poll `/session` to
notice a boundary — when `session_id` changes, everything you cached about
your position is stale:

```json
{ "status": "success", "data": {
    "session_id": "uuid",        // the window these figures belong to
    "team_name": "TEAM DELTA",   // your team's NAME — team_id is not returned
    "cash": 1000000000, "reserved_cash": 0,
    "realized_pnl": 0, "unrealized_pnl": 0, "total_value": 1000000000,
    "positions": [
      { "symbol": "NOVA", "quantity": 100, "reserved_qty": 0, "avg_cost": 14200,
        "last_price": 14250, "market_value": 1425000,
        "unrealized_pnl": 5000, "realized_pnl": 0 }
    ],
    "trades": [
      { "trade_id": "uuid", "order_id": "uuid", "symbol": "NOVA",
        "side": "buy", "price": 14200, "quantity": 100,
        "buyer_team": "TEAM DELTA", "seller_team": "BOT",
        "timestamp": "2026-08-14T09:31:07Z" }
    ] } }
```

Each `trades[]` element is one of **your** fills. `side` and `order_id` are your
side of it; `buyer_team` and `seller_team` name both parties, with every house
bot shown as **`BOT`**. A self-cross — you on both sides — prints two entries
sharing one `trade_id`, one per side.

Each `positions[]` element — one per instrument you hold this window; all money in **integer cents**:

| Field | Meaning |
|-------|---------|
| `symbol` | Instrument ticker |
| `quantity` | Shares held (`0` = flat) |
| `reserved_qty` | Shares locked by your open **sell** orders |
| `avg_cost` | Volume-weighted cost basis (`0` when flat) |
| `last_price` | Current mark (last trade price) |
| `market_value` | `quantity × last_price` |
| `unrealized_pnl` | `quantity × (last_price − avg_cost)` |
| `realized_pnl` | Cumulative realized P&L booked on this symbol |

`trades[]` is every fill you executed **in this window**, newest first, from
**your side** of the trade: `side` is what you did, `order_id` is your own
order, and `buyer_team` / `seller_team` name both parties (a house bot shows as
`BOT`). `[]` until your first fill.

### Placing an order

All prices and amounts are **integer cents** — never floats. `4250` = **S$42.50**.
Trading cash, prices, and P&L are denominated in **SGD** — separate from the LLM
service's **USD** credit budget.

```json
POST /orders
{
  "symbol":          "NOVA",
  "side":            "buy",          // "buy" or "sell" — lowercase
  "quantity":        100,            // whole shares, 1 to 100000000
  "price":           4250,           // integer cents ($42.50), 1 to 100000000
  "idempotency_key": "team-uuid-per-order"
}
```

Every order is a **limit order** — there is no `type` field. (A body that still
sends one is accepted; unknown fields are ignored, so an old bot sending
`"type": "market"` gets a limit order, not an error.)

A **decimal** `price` or `quantity` — the classic dollars-for-cents mistake,
`"price": 42.50` — is rejected with a 400 that restates the cents rule.

Both sizes must be positive: a zero or negative `price` is `400 ORDER_INVALID`, and a
zero or negative `quantity` is `400 ORDER_QUANTITY_INVALID` — so a signal that sizes an
order at 0 shares gets an error, not a phantom fill.

`idempotency_key` is **required**: a body without one, or with a blank one, is
`400 ORDER_INVALID`. It is not treated as a repeat submission — a missing key is a
malformed request, not a duplicate order. Send a value unique to each order (a UUID
per order is the usual choice); reusing one deliberately is what gets you
`409 ORDER_DUPLICATE`.

Both are also capped at **100,000,000** — a price of $1,000,000 per share, or 100
million shares in one order. Nothing reachable in a real window comes near either
ceiling (you start with $10,000,000 and instruments open between $60 and $300), so
in practice this only catches a runaway number: an uninitialised variable, a
unit mix-up, or a signal that has gone to infinity. Over the cap, a `price` is
`400 ORDER_INVALID` and a `quantity` is `400 ORDER_QUANTITY_INVALID`.

Response — HTTP **`201 Created`** with the standard `{status, data}` envelope; the order id is `data.order.id`:
```json
{ "status": "success",
  "data": {
    "order":  { "id": "uuid", "status": "OPEN", "symbol": "NOVA", "side": "buy",
                "price": 4250, "quantity": 100, "remaining_qty": 100,
                "team_name": "TEAM DELTA" },
    "trades": []                     // fills, if the order crossed immediately
  } }
```

An order names its owner with **`team_name`**; `team_id` is not returned. Any
`trades[]` here carry `buyer_team` and `seller_team` — both parties by name,
with every house bot shown as **`BOT`** — plus `buy_order_id` and
`sell_order_id`, which identify orders rather than teams:

```json
"trades": [
  { "id": "uuid", "symbol": "NOVA", "price": 4250, "quantity": 100,
    "buy_order_id": "uuid", "sell_order_id": "uuid",
    "buyer_team": "TEAM DELTA", "seller_team": "BOT",
    "timestamp": "2026-08-14T09:31:07Z", "session_id": "uuid" }
]
```

`GET /orders` and `GET /orders/:id` return the same order shape, and only ever
your own orders.

> Matching runs **synchronously**, so the submit response is already resolved: a resting
> order comes back `OPEN`, and one that crosses comes back `FILLED` / `PARTIALLY_FILLED`
> with `trades` populated. You won't observe `PENDING` on the `POST /orders` response — it
> is only a transient internal state before matching.

> Own-team responses may carry extra bookkeeping fields beyond those shown here —
> `created_at`, `updated_at`, `idempotency_key` on the order —
> which are safe to ignore.

**Order lifecycle:** `PENDING → OPEN → PARTIALLY_FILLED → FILLED / CANCELLED / REJECTED`

`GET /orders` returns your window's history as a **list of these order objects**; `GET
/orders/:id` returns a single one. The shape and `status` values above are the contract
for reading orders too, not just for the submit response.

> **Long-only — no short-selling.** You can only sell an instrument you currently hold.
> A sell for more than your position is rejected with `INSUFFICIENT_POSITION`; build a
> position with a buy first. Positions run long-flat — buy to open, sell to close — you
> cannot go net short. (A SELL signal in a strategy therefore *reduces or closes a held
> long*, it never opens a short.)

> Self-trades are rejected: an order whose price crosses your own resting order returns
> `SELF_MATCH` — cancel the resting order first.

> **Price band.** An order priced too far from the current market is refused
> with `422 PRICE_OUT_OF_BAND` and the message `Price outside band not allowed`.
> The band is wide and never touches an
> order priced at or inside the current quotes. Reprice closer to the market
> and resubmit with a new key; do **not** retry as-is. Right after an
> announcement the market moves, so a rejection in that second clears itself:
> wait a moment and try again.

> The `idempotency_key` is a client-generated unique value per order. Keys are
> scoped to **your team** — another team using the same string never collides
> with yours, and never sees your orders.
>
> **If a submission times out**, you hold the key you generated but not the
> order id, which was in the response you never received. Call `GET /orders`
> and find the entry whose `idempotency_key` matches the one you sent: every
> order in the list carries the key it was placed with, alongside its `id`,
> `status` and `remaining_qty`. That tells you whether it landed and what it
> did — do that instead of blindly resubmitting, which is how you avoid
> duplicate orders.
>
> `/orders/:id` takes the **order id only**, on `GET` and `DELETE` alike. One
> identifier, one meaning; a key sent there is simply an unknown order (404).

### Success responses

Success uses standard HTTP codes: **`201 Created`** for the create endpoint
(`POST /orders`) and **`200 OK`** for reads and `DELETE
/orders/:id`. Treat **any `2xx`** as success — key your response handling on the
envelope's `"status": "success"`, not on an exact `200`. (Matching is synchronous,
so a `201` on `POST /orders` already carries the fill in `data.trades`.)

### Error envelope

```json
{ "status": "error",
  "error": { "code": "RATE_LIMIT_EXCEEDED", "message": "...", "orderId": null } }
```

Every response carries a top-level `status` (`"success"` or `"error"`). On error the
`error` object holds a machine-readable `code`, a human `message`, and `orderId` — the
order the error concerns (e.g. a duplicate submission), or `null`.

| HTTP | Code | What to do |
|------|------|-----------|
| 400 | `ORDER_INVALID` | Malformed body, a missing or blank `idempotency_key`, non-positive price, a price above 100000000, or a **decimal** price (cents are integers). Fix the request; don't retry as-is |
| 400 | `ORDER_SIDE_INVALID` | `side` must be lowercase `"buy"`/`"sell"` |
| 400 | `ORDER_QUANTITY_INVALID` | `quantity` must be a positive whole number of shares, at most 100000000 |
| 402 | `INSUFFICIENT_FUNDS` | Reduce quantity or cancel open orders |
| 400 | `INSUFFICIENT_POSITION` | Selling more than you hold — long-only, no short-selling. Buy first, or reduce quantity |
| 409 | `ORDER_DUPLICATE` | Idempotency key already used — do **not** resubmit |
| 409 | `SELF_MATCH` | Order would execute against your own resting order — cancel it first and resubmit with a **new** idempotency key (no self-trading) |
| 422 | `PRICE_OUT_OF_BAND` | Priced too far from the current market. Reprice closer to the quotes; don't retry as-is |
| 423 | `CANARY_LOCKED` | Staging is reserved for automated canary checks |
| 429 | `RATE_LIMIT_EXCEEDED` | You spent that endpoint's window — the message names the limit. Back off; capacity returns as your oldest calls age past 30s |
| 503 | `MARKET_HALTED` | Wait for an SSE `session` event with `status: "open"` |
| 503 | `SESSION_CLOSED` | Session not active; no action |
| 504 | `GATEWAY_TIMEOUT` | Treat as UNKNOWN; recover via `GET /orders`, matching on your `idempotency_key` |
| 400 | `UNKNOWN_SYMBOL` | `symbol` isn't a tradeable instrument — resolve names via `GET /instruments` |
| 400 | `ORDER_NOT_CANCELLABLE` | `DELETE /orders/:id` on an order that's already filled or cancelled |
| 401 | `UNAUTHORIZED` | Missing or unknown `X-API-Key` — send it on **every** call |
| 404 | `NOT_FOUND` | Unknown order id, or an order that isn't yours |
| 400 | `BAD_REQUEST` | Malformed body on a non-order endpoint |
| 500 | `INTERNAL_ERROR` | Unexpected server error — safe to retry after a short backoff |

The first block are order-placement rejections; the rest can surface on any REST call
(auth, lookups, cancels). All use the error envelope shown above.

### Rate limits

Every authenticated call is rate limited **per team**, in three separate tiers. Each tier
is its own **rolling 30-second window**, and the whole allowance is burstable:

| Tier | Endpoints | Limit per team |
|---|---|---|
| Orders | `POST /orders` | **100** per rolling 30 s |
| Order book | `GET /orderbook/:symbol` | **600** per rolling 30 s |
| Everything else | `GET /instruments`, `GET /orders`, `GET /orders/:id`, `DELETE /orders/:id`, `GET /portfolio` — **one shared window** | **300** per rolling 30 s |

Fire all 100 orders in the first ten seconds if you like, but then every further
`POST /orders` is refused with 429 until your earliest orders age past the 30-second
window — capacity drains back call by call, not as a fresh batch, and quiet time never
banks more than the window holds. The same rule applies to the other two tiers. The
tiers are independent: a spent order window does not stop you reading the book, and a
spent read window does not stop you placing orders. Every call counts, accepted or
refused — a request that comes back 400 still spent a slot. Cancels draw on the shared
window like any other call. The 429 body names the limit of the tier you hit.

**Read the headers and you never have to guess.** Every authenticated response carries
`X-RateLimit-Limit` (that tier's limit) and `X-RateLimit-Remaining` (calls you have left
in that tier's window at the instant of the response), so a bot can slow down before it
is refused. A 429 adds `Retry-After`: whole seconds until **one** slot returns — the
moment your oldest call in the window ages past 30 s — not until the whole window
clears. Sleep that long and retry once; don't retry as-is in a loop.

```
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
```

The public endpoints (`/health`, `/ready`, `/session`, `/events`) are not limited. Don't
lean on polling the reads for live data; that's what the SSE feed is for. `GET /portfolio`
in particular is served from a short (≈1 s) per-team cache when polled hard: your own
orders, fills and cancels always show up on the very next read, and marks are always
live, so nothing you trade off is ever stale — polling it faster than once a second just
returns the same answer.

---

## 2. SSE market-data feed

The exchange publishes real-time data over **Server-Sent Events** at `GET /events`.
The feed is **public — no `X-API-Key` needed** (it carries only market-wide
data: prices, book aggregates, trades, announcements, session state; browser
`EventSource` clients can't send custom headers anyway). This is the correct channel for live data — REST polling is slow
and wasteful.

**On connect** you get a snapshot to start from: one `price` per instrument plus the
current `session` state, so you can begin immediately without waiting for the next
trade. These snapshot frames carry **no `id:`** — they are not replayable events, and
they must not move your reconnect cursor.

**On reconnect**, hand back the id of the last event you processed and the exchange
replays what you missed, in order, before going live. Two ways to send it, and they
behave identically:

- the **`Last-Event-ID` header** — what a browser `EventSource` sends by itself when
  it auto-reconnects after a dropped connection, so you get this for free;
- **`GET /events?lastEventId=<id>`** — the same cursor as a query parameter.

The query form matters more than it looks. A browser `EventSource` only sends the
header on *its own* reconnect; a **fresh** `new EventSource(url)` — after a page
reload, a reopened tab, or a bot restarting from a cursor it saved — sends no header
at all, and cannot be made to (that is the same limitation that makes this feed
keyless). If you persist your cursor and want to resume from it on a cold start, the
query parameter is the only way to do it.

If your cursor can no longer be served — it's older than the retained window (last
10k events), or it predates an exchange restart (ids restart at 1) — the exchange
**resyncs you with a fresh snapshot** instead of a silently incomplete stream.
Handle both: a replay, or a resnapshot.

Idle connections are kept alive with SSE comment heartbeats (`: keep-alive`) — your
client should ignore them.

| Event | Payload | Use |
|-------|---------|-----|
| `price` | `{ symbol, last_price, timestamp }` | Build your own price history |
| `trade` | `{ symbol, price, qty, timestamp }` | Market activity — read *your own* fills from `GET /orders` |
| `book_update` | `{ symbol, bids, asks }` | Best bid/ask for limit pricing |
| `announcement` | `{ message, affected_symbol, timestamp, announcement_id }` | Market events — react if you handle them. `announcement_id` is a stable integer naming the broadcast; log it against the orders you place in response and you can measure, afterwards, which news your strategy actually traded on |
| `session` | `{ status: "open" \| "halted" \| "closed" }` | Gate trading on session state |

> **There is no candles / OHLCV endpoint.** Strategies that need a lookback window
> work over the last *N* `price` samples you've accumulated, not over pre-built bars.
> (An admin-only candles endpoint exists for the organizers' internal dashboards; it is
> not part of the student surface. Its series is scoped to a **single trading window** —
> by default the one running, or between windows the one that just closed, and the
> organizers may also name a past window to redraw it during a debrief. Never a run of
> several concatenated: each window re-anchors the price engine, so a series spanning
> two would draw a jump that happened in neither.)

---

## 3. Instruments

Five instruments today. Names can change — **resolve them at runtime** (call this at
startup). Each entry is an object: the ticker, its company identity, and prices in
integer cents.

```json
GET /instruments
{ "status": "success", "data": [
  { "symbol": "NOVA", "name": "Nova Energy", "sector": "Oil & Gas",
    "starting_price": 15000, "last_price": 17264 }
] }
```

> **Use this call for the *static* instrument list only — `symbol`, `name`, `sector`.**
> The `starting_price` and `last_price` here are a startup convenience and **may be
> withdrawn at any time during the trading day** — do not build your bot or dashboard
> on them. Source live prices from the SSE `price` event (the intended channel), and
> take an instrument's **starting price** as the first `price` you observe when the
> trading session opens.

---

## 4. Order book

`GET /orderbook/:symbol` returns a point-in-time snapshot. Entries are **objects**,
not `[price, qty]` pairs, and there is **one entry per resting order** — not one
per price. Bids are highest-first, asks lowest-first; prices are integer cents:
```json
{ "status": "success", "data": {
  "symbol": "NOVA",
  "bids": [ { "price": 14250, "quantity": 60,  "team_name": "TEAM DELTA", "created_at": "2026-08-16T09:00:01Z" },
            { "price": 14250, "quantity": 40,  "team_name": "BOT",        "created_at": "2026-08-16T09:00:02Z" },
            { "price": 14200, "quantity": 100, "team_name": "TEAM ECHO",  "created_at": "2026-08-16T09:00:03Z" } ],
  "asks": [ { "price": 14300, "quantity": 200, "team_name": "BOT",        "created_at": "2026-08-16T09:00:04Z" } ]
}}
```
`quantity` is what is **still resting**, not the order's original size.

**Two orders at the same price appear in the order they will fill.** The exchange
matches on strict price-time priority, so within a price the earlier `created_at`
is ahead in the queue — the list is that queue, top to bottom. In the example
above, TEAM DELTA's 60 at 14250 fills before the 40 behind it.

`team_name` is who is resting the order. Every house bot shows as **`BOT`** —
one label for all of them, so a market maker cannot be told apart from a trend
follower or a random trader.

For a live book, consume SSE `book_update` events: same entry shape, top 5
orders per side. It fires on **any** book change — a fill, an order coming to
rest, or a cancellation — not only on trades.

---

## 5. Time and number formats

- The exchange timestamps internally in UTC. **Anything you show a user or export must
  be SGT (UTC+8)**, millisecond precision where it matters.
- Use a period `.` as the decimal separator in any export.

---

## 6. MCP read server (you run this)

You run a read-only MCP server (code provided in `baseline-mcp-server`). It exposes tools that report system state,
each returning a `source` (the backing endpoint) and an `as_of` SGT timestamp so your
assistant can cite where an answer came from.

| Tool | Returns |
|------|---------|
| `get_portfolio` | cash, total value, realised / unrealised / session P&L |
| `get_positions` | per-symbol quantity, average entry, unrealised P&L |
| `get_pnl` | aggregate + per-instrument P&L |
| `get_session_summary` | total P&L, trade count, best / worst instrument |

A `mcp-adapter.example.js` shows one call end-to-end — copy the pattern for the rest.
Control tools (`pause_instrument`, `resume_instrument`, `kill_switch`) are present but
**inert**; making them act on the bot is a stretch goal, not a requirement.

**Full usage** — the dual surface (REST facade `POST /tools/{name}` vs the `/mcp`
endpoint), the response contract, auth, and quickstart — is in
[`mcp-server-reference.md`](mcp-server-reference.md).

---

## 7. LLM service (provided)

A wrapper that speaks the Anthropic Messages API (`POST /v1/messages`) so you can call
Claude without holding a provider key — authenticated with your exchange `X-API-Key`. It
is available from registration, alongside the exchange's read-only endpoints (the MCP read server is code you run yourself — see §6).

**Full usage** — model aliases, the `/v1/messages` contract, tool-calling, and mock
mode — is in [`llm-api-reference.md`](llm-api-reference.md).

---

*Exchange API Reference | Hackathon platform | Live URLs in "Base URLs" above*
