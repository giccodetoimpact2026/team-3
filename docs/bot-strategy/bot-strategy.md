# Bot Strategy — Per-Instrument Plan

Optimised trading strategies for each instrument, reconciled across **James Wee's strategy
notes**, the **stakeholder records**, the **execution plan**, and the **live bot behaviour**
observed against the sandbox exchange.

> **Sources:** `docs/james-wee-strategy-notes.md`, `docs/stakeholder-interview-records.md`,
> `docs/execution-plan.md`, and a live run of `baseline-bot` against the sandbox.

---

## Where the bot is now

- Only **NOVA (momentum)** and **ELXR (mean-reversion)** trade. AXON / CRVX / DRFT are untuned.
- **No regime detection**, **fixed trade size**, and mean-reversion still buys *every* dip below
  the mean (no trend guard).
- ✅ The **ELXR lookback fix (5 → 15) is already committed** in `config.yaml` — the #1 must-fix
  is done. The deeper work (regime gate, trend guard) is not.
- Live P&L reflects the gaps: **NOVA +0.95M, AXON +0.58M** (winners) vs
  **ELXR −7.0M, CRVX −3.4M, DRFT −0.5M** (bleeders) — exactly the "no regime filter / no
  stop-loss" problem playing out.

---

## Per-instrument strategy

| Instrument | Sector / Personality | Strategy | Key params & rationale |
|---|---|---|---|
| **NOVA** | Oil & Gas — trending, event-sensitive | **Momentum**, wide lookback | James p1: trending names "move slow and far" → **widen lookback** (try 20–30 vs current 10), keep threshold ~0.003. Gate on regime: run only when ATR% says *trending*; stand down in flat markets (where momentum bleeds). Event-sensitive → most responsive to announcements. |
| **ELXR** | Pharma — defensive, range-bound | **Mean-reversion + trend guard** (+ market-making candidate) | Lookback **15** (done), threshold ~0.004. James p2/p3: the **cleanest reversion candidate** (least correlated) *but* must be suspended in a real downtrend — add the ATR regime gate so it stops catching the falling knife. Liquid → also a **market-making** target. |
| **AXON** | Logistics — **correlated with NOVA** | **Pairs (lead-lag off NOVA)** | James p4 + Derek: energy leads freight. **NOVA breaks a new high → go long AXON.** Trades the *relationship*, not just momentum on another name. Also liquid → market-making candidate. ⚠️ The NOVA↔AXON link is also a **risk** (net exposure) — cap it. |
| **CRVX** | Semiconductors — growth momentum | **Momentum** (reuse NOVA engine) | Derek + James p4: growth/momentum behaviour. Reuse the momentum class with **its own per-instrument params** (James: never share params across names). Cheap breadth once the router exists — but regime-gate it (currently −3.4M running momentum into a decline). |
| **DRFT** | Defense — **thin liquidity, event-driven** | **Tiny-size, event-driven / fade — with guardrails** | **Resolved on the Zoom call: tradeable with informed risk management — NOT normal size, NOT hard-blocked.** Size ~**1/5 of NOVA (~5%)**. Moves on announcements, not the others → handle separately. Own orders move its price → passive limits, watch market impact. *(The "normal size" post-meeting note is the planted error — ignore it.)* |

---

## Cross-cutting layers (where the real edge is)

Per-instrument strategies aren't enough. These four layers sit *around* the strategies and are
where James and the desk say the alpha actually lives.

### 1. Regime detection (ATR) — "where the real edge is" (James p3)
A router that runs *before* strategy selection. Approximate ATR from the live price series
(there is no candles endpoint):

```
TR_t    = | price_t - price_(t-1) |
ATR     = average(last ~14 TR values)
ATR_pct = ATR / price_t

low-vol  (< ~0.0015) -> suspend momentum / cut size (it bleeds in flat markets)
high-vol (> ~0.004)  -> momentum active
neutral               -> hold posture, reduce size
```

- Thresholds are **per-instrument** (the thin name looks more volatile per sample even when
  quiet — don't share thresholds).
- Add **hysteresis** so it doesn't flip-flop on the boundary.
- This is what makes the bot *know when its own strategy stops working* — the single biggest
  differentiator, and it directly fixes the ELXR / CRVX bleed.

### 2. Dynamic position sizing
Scale size by ATR / liquidity instead of a fixed `trade_quantity`. Adeline's tell:
*lots of partial fills = sizing too big for the book.* Size down thin names (DRFT), size to the
book on liquid ones.

### 3. Market-making on the liquid names (ELXR / AXON)
Re-enable the disabled module, but **fix the inventory bug Derek named**: tighten the *ask* when
long, tighten the *bid* when short (the skeleton has it backwards). Adds a third strategy *type*.

### 4. Announcement → supply-chain LLM signal
The `announcement` SSE handler is currently `pass`. Derive a *directional view across the
supply-chain web* (a freight shock hits AXON directly, ripples to NOVA / CRVX differently), gate
on confidence, and feed it to the strategies. Sophia's ask and the AI-scoring centrepiece.

### 5. Order-book awareness (execution prerequisite)
**The bot is currently blind to the order book** — it never reads it. `get_orderbook()` exists in
`exchange/client.py` but is never called, and the SSE `book_update` event is dispatched by
`feed.py` but `main.py` only wires `session` + `price`. Every decision — including the limit price
(`last_price ± 5bps`) — is made off `last_price` alone, with no view of the real best bid/ask or
depth.

This is a **prerequisite**, not a strategy: dynamic sizing (layer 2) and market-making (layer 3)
are impossible without it, and it improves fills for *every* existing strategy immediately.

Two data sources (see `exchange-api-reference.md` §4):
- **REST `GET /orderbook/:symbol`** — full snapshot, every resting order (`price`, `quantity`,
  `team_name`, `created_at`), sorted in fill order. Rate limit 600 reads / 30s — **bootstrap +
  occasional resync only, never per-tick.**
- **SSE `book_update`** — `{ symbol, bids, asks }`, top 5 per side, pushed on *any* book change
  (fill / rest / cancel). Free; rides the feed you already have.

> Note: every house bot shows as the single label **`BOT`** — you can tell teams apart from house
> bots, but not one house-bot type from another.

**The fixes to make the bot see the book:**

| # | Fix | File | Type |
|---|-----|------|------|
| 1 | Add a `books` cache + `on_book_update` handler | `main.py` | new code |
| 2 | Register `book_update` with the feed + `EventWorker` | `main.py` (~185–188) | 2-line wiring |
| 3 | Bootstrap a REST snapshot per symbol at startup | `main.py` (~111) | new code |
| 4 | Add `best_bid` / `best_ask` / spread helpers (return `None` on empty side) | `main.py` / util | new code |
| 5 | **Make limit pricing book-aware** (price off the touch, not `last_price ± bps`) | `strategies/base.py` | modify |
| 6 | Clear the `books` cache on session-ID change | `main.py` `on_session` (~146) | 1-line |

Guardrails: don't poll `/orderbook` per tick (let SSE drive it); handle empty sides before pricing;
`X-API-Key` stays on the REST call, SSE stays keyless. Fixes 1–4 are low-risk additions;
**Fix 5 is the behavioural change** that actually improves fills and is the point of the exercise.
Tests: `on_book_update` updates the cache; `best_bid/ask` handle empty sides; `_limit_price` prices
off a mocked book and falls back safely.

---

## Phase 2 build priority

Ordered by **leverage ÷ effort**, respecting dependencies. **P1 = build first** (foundations that
everything else needs and that stop the current bleed); **P2 = the substance** (breadth + quality);
**P3 = higher effort / higher risk, do once P1–P2 are solid.** Per Marcus's rule: *four features
done cleanly beat ten half-built.*

| Priority | Feature | Value | Effort | Depends on | Why here |
|----------|---------|-------|--------|-----------|----------|
| **P1** | **Regime-detection router (ATR)** | 🔴 highest | Med | — | The edge *and* the foundation; stops the ELXR/CRVX bleed. Nothing else should run without it. |
| **P1** | **Order-book awareness** (fixes 1–6) | 🔴 high | Low | — | Prerequisite for sizing + market-making; improves fills for existing strategies day one. Cheap, low-risk. |
| **P2** | **CRVX momentum** | 🟢 med | Low | regime router | Near-zero new code once the router exists; proves the design generalises. |
| **P2** | **AXON pairs-off-NOVA** | 🟢 med-high | Med | reliable NOVA signal | The distinctive, non-momentum trade; shows you read the market structure. |
| **P2** | **Dynamic position sizing** | 🟢 med-high | Med | ATR + order book | Improves fills across *all* strategies at once; fixes the partial-fill problem. |
| **P3** | **Announcement → LLM signal** | 🟢 high (AI score) | High | LLM service + announcement handler | Big scoring value, but larger build; gates DRFT. Often owned by the AI track in parallel. |
| **P3** | **Market-making (ELXR / AXON)** | 🔵 med | High | order book + sizing | A third strategy *type*; higher risk. Re-enable with the inventory bug fixed. |
| **P3** | **DRFT tiny-size event strategy** | 🔵 low | Med | announcement signal + hard size cap | Last, smallest, most guarded — "don't break NOVA chasing DRFT" (Winston). |

**Recommended sequence:** ship the two **P1** foundations first (regime router + order-book
awareness) — together they stop the bleed and fix execution quality. Then **P2** for breadth and
quality (CRVX → AXON → dynamic sizing). Treat **P3** as differentiators: run the announcement/LLM
signal on the AI track in parallel, and only add market-making and DRFT once the core is stable and
tested.

> **Not in this doc but must land alongside (risk track):** kill switch, order-rate anomaly
> auto-pause, per-position stop-loss, portfolio exposure cap. Trading harder without those is how
> the current −9M unrealized happened — see `EXECUTION-PLAN.md` for the risk-bucket ownership.

---

## How each strategy actually decides to trade (plain English)

**Momentum (NOVA, CRVX).** Look at the price now vs the price N samples ago.
`momentum = (latest − oldest) / oldest`. If it's risen more than the threshold, the trend is up →
**buy**. If it's fallen past the threshold *and we hold a position*, the trend has turned →
**sell to close** (long-only, so we never go short). The lookback should be **wide** for these —
they trend slowly, and a short window just reacts to noise.

**Mean-reversion (ELXR).** Assume the price oscillates around a rolling average. Compute the mean
of the last 15 samples. If price is *below* the mean by more than the threshold, expect it to
bounce back up → **buy**. If it's *above* the mean and we hold → **sell to close**. The trap: in a
sustained downtrend every new low looks like "another dip," so the bot keeps buying a falling
market. That is why it needs a **trend guard** (see the regime layer) — mean-reversion is only
valid in a *ranging* market.

**Pairs / lead-lag (AXON off NOVA).** AXON doesn't trade on its own signal. It watches NOVA:
energy costs lead freight, so when **NOVA breaks a new high**, AXON tends to follow → **buy AXON**.
The signal comes from a *different instrument* than the one being traded — that's what makes it a
relationship trade, not just momentum on another name.

**Market-making (ELXR / AXON, liquid names).** Instead of predicting direction, quote *both* a bid
and an ask and earn the spread as others trade against you. The inventory rule: when you're
**long**, tighten (lower) your **ask** so you sell down; when you're **short**, tighten (raise)
your **bid** so you buy back. The disabled module has this backwards — fix it before enabling.

**Event-driven / fade (DRFT).** DRFT barely moves except on announcements, and it's so thin your
own order moves the price. Trade it **tiny** (~1/5 of NOVA), react to *announcements* rather than a
price signal, use passive limit prices, and treat market impact as a first-class risk.

---

## Recommended build order — and *why* this order

The order is driven by two things: **dependency** (what has to exist before the next thing works)
and **leverage** (biggest P&L / demo impact per hour). Do them top to bottom.

### 1. Regime-detection router (ATR)  — *do this first*
- **What:** a router that, on each tick, measures volatility (ATR%) and decides *which* strategy
  runs for that instrument — or that it should stand down / cut size.
- **Why first:** it's the **highest-edge** piece *and* the foundation everything else plugs into.
  Every strategy below registers with the router rather than being hard-wired in `main.py`.
  It also directly stops the current bleed — ELXR and CRVX are losing money precisely because
  they run their strategy in the wrong regime.
- **Depends on:** nothing new — price history already accumulates from the SSE feed.
- **Done when:** in a flat market NOVA/CRVX momentum stops firing; in a clear downtrend ELXR stops
  buying dips; the chosen regime per instrument is observable (log/emit it).

### 2. CRVX momentum  — *cheap breadth*
- **What:** run the existing momentum logic on CRVX with its *own* parameters.
- **Why here:** once the router exists this is **near-zero new code** — a whole extra instrument
  trading, and it proves the router/`BaseStrategy` design actually generalises to a new name.
- **Depends on:** step 1 (so it's regime-gated from birth, not repeating the current CRVX bleed).
- **Done when:** CRVX trades on its own params and is suspended when its regime is flat.

### 3. AXON pairs-off-NOVA  — *the relationship trade*
- **What:** buy AXON when NOVA breaks a new high (energy leads freight).
- **Why here:** it's the first *non-momentum* idea and the most distinctive strategy on the desk —
  it shows you read the market structure, not just the code. It needs NOVA's signal to already be
  reliable, which it is by now.
- **Depends on:** a working NOVA signal (step 1) + tracking NOVA's rolling high.
- **Done when:** an AXON long is opened in response to a NOVA new-high, and net NOVA+AXON exposure
  is capped (they move together — that's a doubled bet).

### 4. Dynamic position sizing  — *quality layer, helps everything already built*
- **What:** replace the fixed `trade_quantity` with a size that scales to volatility / liquidity.
- **Why here:** every strategy above immediately benefits, and it fixes Adeline's tell —
  *lots of partial fills means the size is too big for the book.* Doing it now means you tune it
  once across all live strategies rather than per-strategy later.
- **Depends on:** ATR from step 1 (reuse it for sizing) + order-book / fill visibility.
- **Done when:** thin names trade smaller, liquid names size to the book, partial-fill rate drops.

### 5. Market-making on ELXR / AXON  — *adds a third strategy type*
- **What:** re-enable the disabled module with the inventory rule fixed (tighten ask when long,
  tighten bid when short).
- **Why here:** it's a genuinely different behaviour (earning spread, not predicting direction),
  so it's more work and higher risk — worth doing only once the directional strategies and sizing
  are solid. Re-enabling it *correctly* (fixing the named bug) is a stronger signal than writing
  something new.
- **Depends on:** dynamic sizing (step 4) for inventory control + reliable fills.
- **Done when:** the module quotes both sides, holds inventory near flat, and the ask/bid tighten
  in the correct direction.

### 6. DRFT tiny-size event strategy  — *last, smallest, most guarded*
- **What:** a tiny, announcement-driven position with hard size caps and market-impact awareness.
- **Why last:** highest risk, lowest capacity, and it depends on the announcement signal being in
  place. Winston's warning applies: *"don't spend two days adding DRFT and breaking NOVA."*
- **Depends on:** the announcement → supply-chain signal, and a hard per-instrument size cap.
- **Done when:** DRFT never trades above its cap, reacts to announcements not noise, and its own
  orders don't visibly walk the price.

> **Sequencing note:** the **ATR regime layer (step 1)** and the **announcement → LLM signal**
> (cross-cutting layer 4) are the two that unlock the most — step 1 gates steps 2/3, and the
> announcement signal gates step 6. Build step 1 first; slot the announcement signal in before
> DRFT.

**Architecture:** build all of this as `BaseStrategy` subclasses + a **router**
(James + Aaron: "not a giant if-else in `main.py`"). The code already has the `BaseStrategy`
shape to extend.

---

## Guardrails to respect throughout

- **Long-only, limit-only**; `side` lowercase; prices are **integer cents**.
- Discover instruments via `GET /instruments` — never hardcode names.
- `X-API-Key` on every authenticated REST call; the SSE `/events` feed is public (no key).
- Sandbox = dev, live = prod — never tune against a live window.
- Per-instrument params only — never run one name's parameters on another (James p1).
