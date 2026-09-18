# Instrument risk model: design

Date: 18 September 2026 (SGT). Status: implemented in `baseline-bot/risk/` behind
`risk_model.enabled` in `config.yaml`; thresholds are starting points to tune on the
sandbox before a live window.

## Purpose

The bot sizes orders, cuts losing positions and caps exposure using rules derived from
how each instrument behaves, computed only from data the bot already receives. The
model answers four questions on every price tick and every order intent:

| Question | Output | Consumer |
|---|---|---|
| How much of this buy may go out? | Adjusted quantity and the binding reason | `OrderManager.submit_intent` |
| Should this position be cut now? | Stop-loss trigger per instrument | `main.on_price` → flatten into the bids |
| Is the portfolio still inside its limits? | Mode `NORMAL` or `DEFENSIVE`, per-instrument pause | Sizing gate, `GET /status` |
| What should the desk see? | Regime, ATR, stop level, drawdown, exposure, alerts | Dashboard via `GET /status` |

Sells are never reduced by the model. Reducing exposure is always allowed, subject only
to the long-only availability check that already exists.

## Requirement sources

| Rule | Source | Reading |
|---|---|---|
| Per-instrument stop-loss, visible, with a defined action | Priya (Deputy CIO); BUG-009 | Genuine. Volatility-scaled distance from cost basis; action is flatten into the bids. |
| Alert at 15% portfolio drawdown of starting capital | Priya | Genuine. Baseline is equity at the first portfolio read of the window; alert at 10%, alert plus buy block at 15%. |
| Alert when a single instrument drops too far | Priya | Genuine. Per-instrument session loss limit pauses new buys on that symbol. |
| DRFT sized far smaller than NOVA, about a fifth | Priya; James page 4; alignment 00:11 | Genuine and agreed. `size_multiplier: 0.2` plus a book-depth cap. The junior note saying DRFT is fine at normal size is contradicted by Derek's own interview. |
| Correlated names must not both run at full size | Clarissa; Farrukh; BUG-008 | Genuine. Configured group `energy_freight` (NOVA, AXON) with a group exposure cap, plus a gross exposure cap and a measured rolling correlation for display. |
| Size by volatility and liquidity, not a fixed quantity | BUG-014; Adeline (partial fills on NOVA) | Genuine. Risk-budget cap from ATR, depth cap from the live book. |
| Regime detection with hysteresis | James page 3; Adeline | Genuine. ATR percentage per instrument routes a size factor per strategy type; momentum is suspended in `LOW`. |
| Order-rate anomaly, kill switch, 15% crash guard | Clarissa; BUG-003 | Already built. The model runs beside them and does not replace them. |
| Sharpe and information ratio per instrument | Raymond; Priya | Deferred. Reporting, not a trading control; needs a sampling convention and a benchmark that do not exist. |

## Inputs the bot already has

| Input | Source | Cost | Used for |
|---|---|---|---|
| `price` events (`symbol`, `last_price`) | SSE, public | Free | Buckets, ATR, regime, marks, correlation, stop checks |
| `book_update` events (top 5 bids and asks) | SSE, public | Free | Best bid/ask, visible depth for the liquidity cap |
| `GET /portfolio` (`cash`, positions with `quantity`, `reserved_qty`, `avg_cost`, `realized_pnl`) | REST, shared 300 per 30 s tier | Already read at most once per second by `OrderManager` | Equity, cost basis, session P&L, exposure |
| `GET /instruments` (`symbol`, `sector`) | REST, once at startup | Already read | Profile lookup by symbol |
| `GET /orderbook/:symbol` | REST, 600 per 30 s tier | Only when a stop fires | Sweep price for the exit order |

The model adds no REST calls on the tick path. Equity is marked locally as
`cash + Σ quantity × last SSE price`, so drawdown and stop checks stay live between
portfolio reads.

Observed on the sandbox on 18 September 2026: every window grants roughly 1,000 shares
per instrument at the starting price with `avg_cost` populated, so granted positions
have a cost basis and the stop-loss applies to them from the open.

## Instrument profiles

Personality descriptions come from the opening brief §3 and James's notes. Numbers are
starting points; each is a config field per symbol. An instrument discovered at runtime
with no profile entry uses `default_profile`.

| Symbol | Personality (source) | `size_multiplier` | Liquidity | `depth_fraction` | Regime `low_vol` / `high_vol` | Stop `min` / `max` pct | Rationale |
|---|---|---|---|---|---|---|---|
| NOVA | Trending, event-sensitive; momentum with a wide lookback (brief, James p.1) | 1.0 | liquid | 0.5 | 0.004 / 0.010 | 0.05 / 0.12 | Reference size. Trend names need room, so the stop floor is not the tightest. |
| AXON | Follows NOVA; freight follows energy costs (brief, Derek, James p.4) | 0.75 | liquid | 0.5 | 0.004 / 0.010 | 0.05 / 0.10 | Shares the `energy_freight` group with NOVA; the group cap prevents a doubled position. |
| CRVX | Growth-like momentum (brief, Derek) | 0.75 | liquid | 0.5 | 0.004 / 0.010 | 0.05 / 0.12 | Momentum name without a documented pair; sized below NOVA until it has a strategy. |
| DRFT | Event-driven, thin book; own orders move the price (brief, Derek, Clarissa, Aaron) | 0.2 | thin | 0.2 | 0.006 / 0.015 | 0.06 / 0.15 | A fifth of NOVA (Priya). Depth cap binds on a thin book. Higher regime thresholds because a thin name looks volatile per sample (James p.3). |
| ELXR | Defensive, range-bound, least correlated; mean reversion over 15 samples (brief, James p.2) | 1.0 | liquid | 0.5 | 0.003 / 0.007 | 0.04 / 0.10 | Range name: a sustained break below cost is a regime break, so the stop floor is the tightest. |

## Calculations

Prices are integer cents. Percentages are fractions (0.04 is 4%).

### Market statistics (`risk/stats.py`)

Bucketing
: Ticks arrive at irregular times (0.2 to 0.4 per second per instrument on the
  sandbox), so the model builds fixed `bucket_seconds` bars (default 10 s) from the
  ticks and computes volatility over bars, not raw samples. This answers brief §8
  question 3 for the risk model. A bar with no ticks carries the previous close with
  zero range.

True range and ATR
: `TR = max(high, prev_close) − min(low, prev_close)` per closed bar.
  `ATR = mean(TR over the last atr_buckets bars)` (default 14, James p.3).
  `ATR_pct = ATR / last_close`. `ATR` is unavailable until 14 bars have closed
  (about 2.5 minutes after the open).

Regime with hysteresis
: `ATR_pct < low_vol` → `LOW`; `ATR_pct > high_vol` → `HIGH`; otherwise `NEUTRAL`.
  A change is adopted only after `regime_confirm_buckets` consecutive closed bars
  (default 3, so 30 s) classify the same way. This is James's "require a few samples
  before switching back".

Book depth
: From the latest `book_update`: `best_bid`, `best_ask`, `bid_depth` and `ask_depth`
  (sum of the top-5 resting quantities per side).

Rolling correlation
: Log return per closed bar per symbol. Pearson correlation over the last
  `correlation_buckets` aligned bars (default 30, about 5 minutes) for every pair.
  Reported as unavailable below `correlation_min_samples` (default 20) or when either
  series has zero variance. Display and alerting only; the exposure cap uses the
  configured groups.

### Equity, exposure and drawdown

```text
equity            = cash + Σ quantity_i × mark_i          (mark = last SSE price, else portfolio last_price)
baseline_equity   = equity at the first non-zero portfolio read of the window
peak_equity       = max(equity) over the window
drawdown_start    = (baseline_equity − equity) / baseline_equity
drawdown_peak     = (peak_equity − equity) / peak_equity
gross_exposure    = Σ quantity_i × mark_i / equity        (long-only, so net equals gross)
group_exposure_g  = Σ_{i ∈ g} quantity_i × mark_i / equity
risk_at_stops     = Σ quantity_i × stop_distance_i / equity   (loss if every stop fills at its level)
```

Mode
: `drawdown_start ≥ drawdown.warn_pct` (0.10) raises one alert.
  `drawdown_start ≥ drawdown.limit_pct` (0.15) raises an alert and switches the mode
  to `DEFENSIVE` for the rest of the window: new buys are refused, sells and stop exits
  continue. `drawdown.action` may instead be `alert_only` or `halt` (the kill-switch
  path). The mode does not auto-recover; the desk decides.

All window-scoped values reset when the session id changes, not when the process starts.

### Order sizing (buy side)

Inputs: symbol, requested quantity `q`, limit price `p`, projected position `pos`
(filled plus pending, from `OrderManager`), source (`momentum`, `mean_reversion`,
`dca`). Profile `P`, equity `E`.

| Step | Rule | Refusal reason |
|---|---|---|
| 1 | Mode `DEFENSIVE` → 0 | `drawdown limit: buys blocked` |
| 2 | Symbol paused (instrument loss limit) or in stop cooldown → 0 | `instrument paused: …` / `stop-loss cooldown` |
| 3 | `f = regime_factors[source][regime]` (default 1.0; momentum `LOW` = 0.0, `NEUTRAL` = 0.5); `f = 0` → 0 | `regime LOW: momentum suspended` |
| 4 | `base = q × P.size_multiplier × f` in `cap` mode; `base = vol_cap × f` in `target` mode | |
| 5 | `stop_distance = clamp(P.stop_atr_mult × ATR, P.min_stop_pct × p, P.max_stop_pct × p)`; without ATR use `P.max_stop_pct × p`. `vol_cap = E × risk_per_trade_pct × P.size_multiplier / stop_distance` | `vol_cap` |
| 6 | `depth_cap = P.depth_fraction × ask_depth` when a book has been seen, else `P.max_qty_without_book` | `depth_cap` |
| 7 | `notional_cap = (max_notional_per_symbol_pct × E − pos × p) / p` | `notional_cap` |
| 8 | `group_cap = (group.max_exposure_pct × E − Σ_{other members} quantity × mark − pos × p) / p` | `group_cap` |
| 9 | `gross_cap = (max_gross_exposure_pct × E − Σ_{others} quantity × mark − pos × p) / p` | `gross_cap` |
| 10 | `quantity = floor(min(base, vol_cap, depth_cap, notional_cap, group_cap, gross_cap))`; below 1 → 0 | binding cap |

The existing share cap in `RiskLimits` (`max_position_*`) still applies afterwards
inside `OrderManager`. Sells return `q` unchanged.

Worked example (sandbox, 18 September): `E` = 177,641,222 cents, NOVA at 15,440,
`risk_per_trade_pct` 0.005, stop 4% → `vol_cap` = 888,206 / 618 = 1,437 shares, so a
100-share momentum buy passes at 100 in `HIGH`, 50 in `NEUTRAL`, 0 in `LOW`. DRFT DCA
buy of 80 shares → `base` = 16; with 1,192 shares of ask depth `depth_cap` = 238, so 16
goes out.

### Stop-loss

```text
reference     = avg_cost if avg_cost > 0 else session-open price     (per symbol, quantity > 0)
stop_distance = clamp(P.stop_atr_mult × ATR, P.min_stop_pct × reference, P.max_stop_pct × reference)
stop_level    = reference − stop_distance
trigger       when price ≤ stop_level on P.confirm_ticks consecutive ticks
action        cancel our resting orders on the symbol; sell the sellable quantity in one
              limit order at the bid level that absorbs it, floored at floor_pct under the
              trigger price (same sweep as the crash guard); reprice once after
              reprice_after_seconds if still resting
cooldown      P.cooldown_seconds during which strategies and DCA do not buy the symbol
```

The stop distance follows current ATR between `min_stop_pct` and `max_stop_pct`, so a
volatile session widens the stop inside a bound. The crash guard (15% below the window
high) remains the tail guard outside the stop.

### Instrument loss limit

```text
session_pnl_i = realized_pnl_i + quantity_i × (mark_i − avg_cost_i)
pause buys on i when session_pnl_i ≤ −instrument_loss_limit_pct × baseline_equity   (default 0.02)
```

The pause lasts for the window. Sells and the stop exit still run.

### Alerts

Bounded list (last 100), each `{seq, at, level, kind, symbol, message}` with `at` in
UTC ISO like the order journal (the dashboard formats SGT). Kinds:
`drawdown_warn`, `drawdown_limit`, `instrument_loss`, `stop_loss`, `correlation`
(measured correlation above `correlation_alert` between two held names outside a
configured group). Every alert is also logged.

## Integration

| Component | File | Role |
|---|---|---|
| `MarketStats` | `risk/stats.py` | Pure per-instrument statistics fed from the worker thread by `on_price` and `on_book`. Clock-injectable. |
| `InstrumentProfile`, `load_profiles` | `risk/profiles.py` | Merge `default_profile`, per-symbol overrides and group membership for the discovered symbols. |
| `RiskModel` | `risk/model.py` | The calculation. Implements the `RiskLimits` protocol (`max_position`, `allows_buy`, `allows_sell`) by delegation, plus `adjust_intent`, `on_portfolio`, `on_price`, `allows_entry`, `status`. No I/O. |
| `Flattener` | `risk/exits.py` | Sell-into-bids exit shared by the crash guard and the stop-loss (book read, sweep price, `OrderManager.flatten`, one reprice). |
| `OrderManager` | `orders/manager.py` | Calls `risk.on_portfolio()` on every portfolio refresh and `risk.adjust_intent()` after its own gates; submits the adjusted quantity or refuses. `submit_intent` gains a `source` keyword. |
| `main.py` | | Subscribes to `book_update`; feeds stats before strategies; runs the stop check after the crash guard; adds `risk` to `GET /status`; resets on session change; validates the config section. |

Thread model is unchanged: SSE reader → bounded queue → trading worker. All model
calls happen on the worker thread or the control thread, guarded by one lock.

Refused or reduced intents are logged with their reason and kept in
`status.instruments[symbol].last_decision`; they are not written to the order journal
because the journal carries exchange statuses only.

## Configuration

Shipped section of `baseline-bot/config.yaml`:

```yaml
risk_model:
  # Instrument risk model (docs/risk-model-design.md). Sizes buys by each
  # instrument's personality, volatility regime and visible liquidity; cuts a
  # position that falls a volatility-scaled distance below its cost; blocks
  # buys at 15% portfolio drawdown; caps correlated and gross exposure. Sells
  # are never reduced. Everything below is a starting point to tune on the
  # sandbox; the calibration sample is recorded in the design doc.
  enabled: true
  size_mode: cap                 # cap: strategy quantity is the ceiling; target: the risk budget sets the size
  bucket_seconds: 10             # ticks are bucketed into 10 s bars before ATR (feed timing is irregular)
  atr_buckets: 14                # James p.3, M ~ 14
  regime_confirm_buckets: 3      # hysteresis: 3 bars (30 s) agree before a regime switch
  correlation_buckets: 30        # rolling 5-minute return correlation, display and alert only
  correlation_min_samples: 20
  correlation_alert: 0.7
  risk_per_trade_pct: 0.005      # equity at risk per buy if its stop fills (vol cap)
  max_notional_per_symbol_pct: 0.35
  max_gross_exposure_pct: 0.90
  instrument_loss_limit_pct: 0.02   # per-symbol session loss that pauses its buys
  drawdown:
    warn_pct: 0.10               # alert
    limit_pct: 0.15              # Priya: 15% of starting capital
    action: block_buys           # alert_only | block_buys | halt
  stop_loss:
    enabled: true                # set false to keep sizing/caps but no cost-basis stops
  exit:
    floor_pct: 0.03              # a stop sells into the bids, never below 3% under the trigger
    reprice_after_seconds: 5
  regime_factors:                # size factor by strategy type and regime (James p.3)
    momentum:       {LOW: 0.0, NEUTRAL: 0.5, HIGH: 1.0}
    mean_reversion: {LOW: 1.0, NEUTRAL: 0.75, HIGH: 0.5}
    dca:            {LOW: 1.0, NEUTRAL: 1.0, HIGH: 0.5}
  groups:
    # Freight follows energy costs (brief, Derek, James p.4): long both is one
    # position, so the pair shares a cap (Clarissa, Farrukh, BUG-008).
    energy_freight: {members: [NOVA, AXON], max_exposure_pct: 0.45}
  default_profile:               # any instrument discovered without a profile
    personality: unknown
    size_multiplier: 0.5
    liquidity: liquid
    depth_fraction: 0.5          # max share of the visible top-5 ask depth per buy
    max_qty_without_book: 1000   # ceiling before the first book_update arrives
    low_vol: 0.004               # 10 s ATR% below -> LOW (momentum off)
    high_vol: 0.010              # above -> HIGH (full size); sandbox liquid names run ~0.8-1.0%
    stop_atr_mult: 4.0           # stop distance = 4 x ATR ...
    min_stop_pct: 0.05           # ... but never tighter than 5% of cost (sandbox swings 3-5% per 2 min)
    max_stop_pct: 0.12           # ... nor wider than 12%; the crash guard sits at 15%
    confirm_ticks: 3
    cooldown_seconds: 120
  profiles:
    NOVA: {personality: trending, size_multiplier: 1.0}
    AXON: {personality: follower, size_multiplier: 0.75, max_stop_pct: 0.10}
    CRVX: {personality: growth_momentum, size_multiplier: 0.75}
    # Thin book: a fifth of NOVA (Priya), a fifth of the visible asks, wider
    # regime bands because a thin name looks volatile per sample (James p.3).
    DRFT: {personality: thin_event, size_multiplier: 0.2, liquidity: thin,
           depth_fraction: 0.2, max_qty_without_book: 20,
           low_vol: 0.006, high_vol: 0.015, min_stop_pct: 0.06, max_stop_pct: 0.15}
    # Range-bound: lowest volatility on the sandbox (10 s ATR ~0.5%), so its
    # own bands and the tightest stop floor.
    ELXR: {personality: range_bound, size_multiplier: 1.0,
           low_vol: 0.003, high_vol: 0.007, min_stop_pct: 0.04, max_stop_pct: 0.10}
```

Validation at startup rejects percentages outside (0, 1), `low_vol ≥ high_vol`,
non-positive bucket or sample counts, negative multipliers, a group naming an unknown
symbol, and an unknown `drawdown.action`.

## Calibration evidence (sandbox, 18 September 2026)

Two-minute public-feed sample, per-tick statistics:

| Symbol | Ticks per s | ATR over 14 ticks (pct) | Per-tick return sd (pct) | 2-minute range (pct) | Top-5 depth bid / ask | Spread (bps) | Median trade size |
|---|---|---|---|---|---|---|---|
| NOVA | 0.32 | 0.02 | 0.42 | 3.6 | 422 / 639 | 15 | 40 |
| AXON | 0.23 | 0.40 | 0.41 | 3.9 | 447 / 534 | 71 | 35 |
| CRVX | 0.23 | 0.10 | 0.47 | 3.5 | 1081 / 327 | 12 | 32 |
| DRFT | 0.17 | 0.59 | 0.87 | 4.8 | 658 / 1192 | 125 | 30 |
| ELXR | 0.28 | 0.16 | 0.91 | 3.2 | 534 / 665 | 90 | 28 |

Consequences for the defaults:

- Per-tick ATR is noisy (NOVA read 0.02% during a quiet spell inside a 3.6% two-minute
  range), which is why the model buckets ticks into 10-second bars before computing ATR.
- The sandbox moves about 3% to 5% every two minutes. Stops tighter than 4% would fire
  on noise, so the stop floors start at 3% to 5% and the crash guard keeps its 15%.
- James's per-sample thresholds (0.0015 / 0.004) do not transfer to 10-second bars:
  a four-minute sample put the liquid names at 0.8% to 1.0% bar ATR, so the bands start
  at 0.4% / 1.0% for them, 0.6% / 1.5% for DRFT and 0.3% / 0.7% for ELXR.
- Spreads of 70 to 125 bps on AXON, ELXR and DRFT exceed the strategies' 5 bps limit
  offset, so those orders rest instead of filling. The model exposes `best_bid` and
  `best_ask`; making the limit offset spread-aware is a strategy change, not in scope.

## Testing

Unit tests under `baseline-bot/tests/`:

- `test_market_stats.py`: bucketed true range and ATR on known sequences, readiness,
  regime hysteresis, depth from `book_update`, correlation of aligned returns
  including the insufficient-data case, session reset.
- `test_risk_model.py`: profile defaults and overrides; every sizing step with a
  known binding cap; sells untouched; drawdown warn and limit with mode change and
  alerts; instrument loss pause; stop-loss confirm ticks, cost fallback, cooldown and
  reset; status contents; `target` size mode.
- `test_orders.py` additions: `adjust_intent` reduces or refuses inside
  `submit_intent`; `on_portfolio` receives every refresh.
- `test_config.py` additions: the validation rules above.
- Existing crash-guard tests keep passing after the exit path is shared.

## Not in scope

- Manual per-instrument pause and resume over the control endpoint (the model exposes
  `pause` and `resume` for it).
- Trailing stops, Sharpe and information ratio, VWAP.
- Strategy routing by regime (swapping strategy instances). The model provides
  `regime(symbol)` and the size factor; routing is strategy work.
- Spread-aware limit pricing.

## Decisions to defend

1. Baseline for the 15% drawdown is equity at the first portfolio read of the window,
   including granted positions, because that is the capital the window starts with.
2. The drawdown action is to block buys rather than halt: exits and stops must keep
   running when the portfolio is already losing.
3. Stops are volatility-scaled and bounded, not a single fixed percentage, because
   per-instrument volatility differs by a factor of three on the sandbox.
4. DRFT trades, at a fifth of NOVA's size and never more than a fifth of the visible
   ask depth, which is the "informed risk management" the alignment meeting agreed on.
5. The NOVA plus AXON group cap encodes the documented energy-to-freight relationship;
   the measured correlation is shown but does not drive the cap, because a five-minute
   estimate is too unstable to trade on.

## Smoke run (sandbox, 18 September 2026, read-only)

The model was built from the shipped `config.yaml` against the live sandbox portfolio
and fed 150 seconds of public feed (204 `price`, 985 `book_update` events). No order
was sent.

| Check | Result |
|---|---|
| Baseline equity | Set from the first portfolio read (S$1,815,672.55) |
| Instrument loss limit | CRVX session loss S$37,664 exceeded 2% of baseline; CRVX buys paused, alert raised |
| Stop-loss | CRVX at 19,987 confirmed below its stop 20,071 (12% under cost 22,809); trigger returned once, cooldown armed |
| Regime | ATR ready after 14 bars; regime still `None` at 150 s because confirmation needs 3 further closed bars (about 170 s from the open) |
| Sizing | NOVA momentum 100 → 100; ELXR mean reversion 50 → 50; DRFT DCA 80 → 16 (`size_multiplier=0.2`); AXON DCA 60 → 45; CRVX DCA 25 → 0 (paused) |
| Exposure | Gross 48%, `energy_freight` 2%, risk at stops 4.3% |

Note for the desk: granted starting positions carry a cost basis, so a position that
is already deep under cost when the bot starts (CRVX above) triggers its stop on the
first confirming ticks. If that is not wanted, set `stop_loss.enabled: false` or raise
`max_stop_pct` for that symbol.
