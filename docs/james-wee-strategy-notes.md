# Strategy Notes — J. Wee
### Transcribed from the shared-drive scan (partially handwritten; some sections illegible)

> Committee note: this is the transcription distributed to teams. Where the scan is
> unclear it is marked `[illegible]`. Page 3 (regime detection) is the section the desk
> most wants implemented.

---

## Page 1 — Momentum (primary instrument, NOVA-type)

Momentum works on the trending, event-sensitive names. Signal over the last *N* price
samples:

```
momentum = (latest_price - oldest_price) / oldest_price
if momentum >  threshold  -> BUY
if momentum < -threshold  -> SELL
```

- Trending names move slow and far — use a **wider** lookback than the fast names.
- threshold around 0.3% (0.003) is a reasonable starting point; tune per instrument.
- Don't run one instrument's parameters on another. The fast and slow names have very
  different characters. [illegible] ... per-instrument config.

---

## Page 2 — Mean-reversion (secondary instrument, ELXR-type / defensive)

For the defensive, range-bound name. Compute a rolling mean over the lookback window;
trade the deviation back toward the mean:

```
mean = average(last N prices)
deviation = (price - mean) / mean
if deviation < -threshold  -> BUY  (price below mean)
if deviation >  threshold  -> SELL (price above mean)
```

> **Lookback = 15 samples.** This is important. I tested shorter windows and a
> **5-sample** lookback is too reactive — it reads a downtrend as a series of "dips" and
> buys into it. That is not mean-reversion, that's catching a falling knife. **Use 15.**
> If you see the bot buying every small dip during a clear downtrend, the window is too
> short — check this first.

deviation threshold ~0.4% (0.004) to start. The defensive name is the cleanest
reversion candidate because it's the least correlated with the others.

---

## Page 3 — Regime detection (NOT YET IMPLEMENTED)

This is the one that matters. Momentum bleeds money in flat, low-volatility markets —
it keeps trading a trend that isn't there. The fix is to detect the **market regime**
and switch behaviour.

Use **ATR (Average True Range)** as a volatility proxy. With no candles endpoint, we
approximate True Range from the live price series — the absolute move between
consecutive samples:

```
TR_t   = | price_t - price_(t-1) |              (sample-to-sample range proxy)
ATR    = average(last M values of TR_t)          (M ~ 14)
ATR_pct = ATR / price_t                           (normalise, so it's comparable across names)
```

Decision:

```
if ATR_pct < low_vol_threshold      -> LOW-VOL / RANGING regime
        -> suspend momentum (it will bleed); switch to mean-reversion or
           market-making, or cut size toward zero
if ATR_pct > high_vol_threshold     -> TRENDING / VOLATILE regime
        -> momentum active
else                                 -> NEUTRAL: hold current posture, reduce size
```

Starting thresholds (tune on sandbox): low_vol_threshold ~0.0015, high_vol_threshold
~0.004. These are *per-instrument* — the thin name will look more volatile per sample
even when nothing is happening, so don't share thresholds across instruments.

[illegible] ... hysteresis so it doesn't flip-flop on the boundary — once you switch
regime, require a few samples before switching back. [illegible]

Implementation sketch: a regime check that runs before strategy selection and routes
to the right strategy instance — not a giant if-else in the main loop. A clean
`BaseStrategy` + a router/decorator is the right shape.

---

## Page 4 — Correlation / pairs (notes only)

The energy name leads the freight name (freight margins follow energy costs). If the
energy name breaks a new high, the freight name tends to follow — usable as a pairs
signal. The semiconductor name behaves like a growth/momentum name. The thin defense
name moves on announcements, not on the others — handle separately and **size it
small**, your own orders move its price. [rest of page illegible]

---

*Transcribed for hackathon distribution. Fix the page-2 lookback first — it's the immediate must-have — then treat page 3 (regime detection) as where the real edge is. — Committee*
