// Pure analytics for the Execution, Performance and Risk tabs. DOM-free and
// jQuery-free so it can be unit-tested in Node (tests/analytics.test.js) and
// loaded as a browser global. Depends on trades.js (for csvRows) and format.js.
//
// Everything here is *derived* on the client from data the exchange already
// gives us — the trade log, the order list, and the price/trade SSE buffers —
// because the exchange exposes no analytics, candles or correlation endpoint.
(function (root, factory) {
  const isNode = typeof module !== "undefined" && module.exports;
  const api = factory(isNode ? require("./trades.js") : root.Trades);
  if (isNode) {
    module.exports = api;
  } else {
    root.Analytics = api;
  }
})(typeof self !== "undefined" ? self : this, function (Trades) {
  // --- Performance / Analytics tab ----------------------------------------
  // Per-instrument risk-adjusted stats derived from the lifecycle CSV rows
  // (Trades.csvRows), whose FILLED / PARTIALLY_FILLED rows already carry a
  // realized PnLImpact in dollars. A "closed trade" for win/loss accounting is
  // a *realizing* fill (a sell fill with a non-zero P&L impact); buy fills book
  // zero and only move the cost basis, so they are not counted as trades.
  function num(v) {
    var n = parseFloat(v);
    return isFinite(n) ? n : 0;
  }

  function mean(xs) {
    if (!xs.length) return 0;
    return xs.reduce(function (a, b) { return a + b; }, 0) / xs.length;
  }

  // Population standard deviation (we treat the observed fills as the whole
  // sample of the session, not an estimate of a wider population).
  function stddev(xs) {
    if (xs.length < 2) return 0;
    var m = mean(xs);
    var v = xs.reduce(function (a, b) { return a + (b - m) * (b - m); }, 0) / xs.length;
    return Math.sqrt(v);
  }

  // Sharpe here is a *per-trade* ratio (mean P&L per realizing fill / its
  // stddev), NOT annualized — there is no risk-free rate or calendar in a
  // single intraday window. The UI must label it as such.
  function sharpe(pnls) {
    if (pnls.length < 2) return null;
    var sd = stddev(pnls);
    if (sd === 0) return null;
    return mean(pnls) / sd;
  }

  function performance(csvRows) {
    var bySym = {};
    function bucket(sym) {
      return (bySym[sym] = bySym[sym] || { symbol: sym, pnls: [], wins: [], losses: [], total: 0 });
    }
    (csvRows || []).forEach(function (r) {
      if (r.Status !== "FILLED" && r.Status !== "PARTIALLY_FILLED") return;
      var pnl = num(r.PnLImpact);
      if (pnl === 0) return; // buy fills / no-op moves are not closed trades
      var b = bucket(r.Instrument);
      b.pnls.push(pnl);
      b.total += pnl;
      if (pnl > 0) b.wins.push(pnl); else b.losses.push(pnl);
    });
    return Object.keys(bySym).sort().map(function (sym) {
      var b = bySym[sym];
      var grossProfit = b.wins.reduce(function (a, x) { return a + x; }, 0);
      var grossLoss = Math.abs(b.losses.reduce(function (a, x) { return a + x; }, 0));
      return {
        symbol: sym,
        trades: b.pnls.length,
        wins: b.wins.length,
        losses: b.losses.length,
        winRate: b.pnls.length ? b.wins.length / b.pnls.length : 0,
        profitFactor: grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? Infinity : 0),
        avgWin: b.wins.length ? grossProfit / b.wins.length : 0,
        avgLoss: b.losses.length ? -grossLoss / b.losses.length : 0,
        totalPnl: b.total,
        sharpe: sharpe(b.pnls), // per-trade, not annualized; null if < 2 trades or zero variance
      };
    });
  }

  // --- Execution tab: fill-rate table -------------------------------------
  // Per instrument: orders sent, fully filled, partially filled (ever),
  // rejected, and fill-rate % (fully-filled / sent). Uses the observed
  // lifecycle history for "ever partially filled", like Trades.counters.
  function fillRate(orders, history) {
    var partial = history ? history.reached("PARTIALLY_FILLED") : {};
    var bySym = {};
    function bucket(sym) {
      return (bySym[sym] = bySym[sym] || { symbol: sym, sent: 0, filled: 0, partial: 0, rejected: 0 });
    }
    (orders || []).forEach(function (o) {
      var b = bucket(o.symbol);
      b.sent++;
      var s = Trades.normalizeStatus(o.status);
      if (s === "FILLED") b.filled++;
      if (s === "REJECTED") b.rejected++;
      if (s === "PARTIALLY_FILLED" || partial[o.id]) b.partial++;
    });
    return Object.keys(bySym).sort().map(function (sym) {
      var b = bySym[sym];
      b.fillRate = b.sent ? b.filled / b.sent : 0;
      return b;
    });
  }

  // --- Overview: the bot's live resting quotes ----------------------------
  // The bot's live bids/asks are its *open* orders on the exchange — anything
  // not yet terminal (FILLED / CANCELLED / REJECTED). A resting buy is a bid,
  // a resting sell is an ask. Bids sort best (highest) first, asks best
  // (lowest) first, so each column reads top-of-book downwards.
  function liveQuotes(orders) {
    var open = (orders || []).filter(function (o) {
      var s = Trades.normalizeStatus(o.status);
      return s === "PENDING" || s === "ACKNOWLEDGED" || s === "PARTIALLY_FILLED";
    });
    function side(name) { return open.filter(function (o) { return String(o.side).toLowerCase() === name; }); }
    var bids = side("buy").sort(function (a, b) { return (b.price || 0) - (a.price || 0); });
    var asks = side("sell").sort(function (a, b) { return (a.price || 0) - (b.price || 0); });
    return { bids: bids, asks: asks };
  }

  // --- Execution tab: candlesticks from the SSE price buffer --------------
  // No OHLCV endpoint exists, so we bucket the accumulated `price` samples
  // ({ t: epoch ms, price: cents }) into fixed-width bars. open/close are the
  // first/last sample in the bucket; high/low the extremes.
  function candles(samples, bucketMs) {
    if (!bucketMs || bucketMs <= 0) return [];
    var sorted = (samples || []).slice().sort(function (a, b) { return a.t - b.t; });
    var out = [];
    var cur = null;
    sorted.forEach(function (s) {
      var bt = Math.floor(s.t / bucketMs) * bucketMs;
      if (!cur || cur.t !== bt) {
        cur = { t: bt, open: s.price, high: s.price, low: s.price, close: s.price };
        out.push(cur);
      } else {
        cur.high = Math.max(cur.high, s.price);
        cur.low = Math.min(cur.low, s.price);
        cur.close = s.price;
      }
    });
    return out;
  }

  // Session-cumulative VWAP aligned to the same buckets, from `trade` samples
  // ({ t, price: cents, qty }). Returns [{ t, vwap }] where vwap is the
  // running volume-weighted average price up to the end of that bucket.
  function vwapPoints(trades, bucketMs) {
    if (!bucketMs || bucketMs <= 0) return [];
    var sorted = (trades || []).slice().sort(function (a, b) { return a.t - b.t; });
    var pv = 0, vol = 0, out = [], curBt = null;
    sorted.forEach(function (t) {
      var bt = Math.floor(t.t / bucketMs) * bucketMs;
      pv += t.price * t.qty;
      vol += t.qty;
      var vwap = vol > 0 ? pv / vol : t.price;
      if (curBt === bt) out[out.length - 1] = { t: bt, vwap: vwap };
      else { out.push({ t: bt, vwap: vwap }); curBt = bt; }
    });
    return out;
  }

  // --- Risk tab: correlation matrix ---------------------------------------
  // Pearson correlation of simple returns between every pair of instruments,
  // computed over the overlapping tail of their price buffers. Buffers is
  // { symbol: [{ t, price }] }. Returns { symbols, matrix } where matrix[i][j]
  // is in [-1, 1], or null when there is not enough overlap.
  function returnsOf(samples) {
    var r = [];
    for (var i = 1; i < samples.length; i++) {
      var prev = samples[i - 1].price;
      if (prev) r.push((samples[i].price - prev) / prev);
    }
    return r;
  }

  function pearson(a, b) {
    var n = Math.min(a.length, b.length);
    if (n < 3) return null;
    var xa = a.slice(a.length - n), xb = b.slice(b.length - n);
    var ma = mean(xa), mb = mean(xb);
    var cov = 0, va = 0, vb = 0;
    for (var i = 0; i < n; i++) {
      var da = xa[i] - ma, db = xb[i] - mb;
      cov += da * db; va += da * da; vb += db * db;
    }
    if (va === 0 || vb === 0) return null;
    return cov / Math.sqrt(va * vb);
  }

  function correlation(buffers, symbols) {
    var syms = symbols && symbols.length ? symbols : Object.keys(buffers || {}).sort();
    var rets = {};
    syms.forEach(function (s) { rets[s] = returnsOf((buffers && buffers[s]) || []); });
    var matrix = syms.map(function (a, i) {
      return syms.map(function (b, j) {
        if (i === j) return 1;
        return pearson(rets[a], rets[b]);
      });
    });
    return { symbols: syms, matrix: matrix };
  }

  // Net directional exposure per instrument: signed market value (long +,
  // short −) in cents, plus the gross and net totals. positions come straight
  // from GET /portfolio.
  function netExposure(positions) {
    var rows = (positions || []).map(function (p) {
      var mv = p.market_value !== undefined && p.market_value !== null
        ? p.market_value
        : (p.quantity || 0) * (p.last_price || p.avg_cost || 0);
      var signed = (p.quantity || 0) < 0 ? -Math.abs(mv) : mv;
      return { symbol: p.symbol, quantity: p.quantity || 0, exposure: signed };
    });
    var net = rows.reduce(function (a, r) { return a + r.exposure; }, 0);
    var gross = rows.reduce(function (a, r) { return a + Math.abs(r.exposure); }, 0);
    return { rows: rows, net: net, gross: gross };
  }

  // --- Risk tab: drawdown -------------------------------------------------
  // Max drawdown over a series of equity snapshots ({ t, value } in cents).
  // Returns the running high-water mark, the worst peak-to-trough drop, and
  // the current drawdown from the latest peak — all as fractions of the peak.
  function drawdown(equity) {
    var series = (equity || []).slice().sort(function (a, b) { return a.t - b.t; });
    var peak = -Infinity, maxDd = 0, curDd = 0;
    series.forEach(function (e) {
      if (e.value > peak) peak = e.value;
      var dd = peak > 0 ? (peak - e.value) / peak : 0;
      if (dd > maxDd) maxDd = dd;
      curDd = dd;
    });
    return { maxDrawdown: maxDd, currentDrawdown: curDd, peak: peak === -Infinity ? null : peak, points: series.length };
  }

  return {
    mean: mean, stddev: stddev, sharpe: sharpe,
    performance: performance, fillRate: fillRate, liveQuotes: liveQuotes,
    candles: candles, vwapPoints: vwapPoints,
    correlation: correlation, netExposure: netExposure, drawdown: drawdown,
  };
});
