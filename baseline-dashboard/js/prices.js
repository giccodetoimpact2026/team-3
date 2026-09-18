// Price history from the SSE `price` events (there is no candles endpoint), the
// short-horizon forecast, sparkline paths and instrument correlation. Pure and
// DOM-free; unit-tested in tests/prices.test.js. Prices are integer cents.
//
// Forecast method (Sophia: "a model estimate with a confidence band, never a
// fact"): drift and volatility of per-sample log returns over the buffer,
// projected `horizonMs` ahead in sample steps; the band is ±z·σ·√k around the
// drifted price (z = 1.645 -> 90%). Confidence is categorical and derived by
// rule from sample count and band width, never a made-up percentage.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.Prices = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  function PriceHistory(capacity) {
    this.capacity = capacity || 600;
    this._by = {};
  }
  PriceHistory.prototype.push = function (symbol, price, t) {
    if (!symbol || !(price > 0)) return;
    var arr = this._by[symbol] || (this._by[symbol] = []);
    arr.push({ t: t === undefined ? Date.now() : t, p: price });
    if (arr.length > this.capacity) arr.splice(0, arr.length - this.capacity);
  };
  PriceHistory.prototype.samples = function (symbol) {
    return (this._by[symbol] || []).slice();
  };
  PriceHistory.prototype.last = function (symbol) {
    var arr = this._by[symbol];
    return arr && arr.length ? arr[arr.length - 1] : null;
  };
  PriceHistory.prototype.symbols = function () {
    var self = this;
    return Object.keys(this._by).filter(function (s) { return self._by[s].length > 0; }).sort();
  };
  PriceHistory.prototype.reset = function () {
    this._by = {};
  };

  function logReturns(samples) {
    var out = [];
    for (var i = 1; i < samples.length; i++) out.push(Math.log(samples[i].p / samples[i - 1].p));
    return out;
  }

  function mean(xs) {
    var s = 0;
    for (var i = 0; i < xs.length; i++) s += xs[i];
    return xs.length ? s / xs.length : 0;
  }

  function stdev(xs, m) {
    if (xs.length < 2) return 0;
    var s = 0;
    for (var i = 0; i < xs.length; i++) s += (xs[i] - m) * (xs[i] - m);
    return Math.sqrt(s / (xs.length - 1));
  }

  function forecast(samples, opts) {
    opts = opts || {};
    var minSamples = opts.minSamples || 20;
    var horizonMs = opts.horizonMs || 300000;
    var z = opts.z || 1.645;
    var n = samples ? samples.length : 0;
    if (n < minSamples) return null;

    var rets = logReturns(samples);
    var mu1 = mean(rets);
    var sd1 = stdev(rets, mu1);
    var spanMs = samples[n - 1].t - samples[0].t;
    var stepMs = spanMs > 0 ? spanMs / (n - 1) : 1000;
    var k = horizonMs / stepMs;
    var last = samples[n - 1].p;
    // Only extrapolate the drift when it is distinguishable from noise
    // (|t| >= 2 on the per-sample mean); otherwise a random walk around the
    // last price is the honest estimate, and only the band carries information.
    var t = sd1 > 0 ? (mu1 / (sd1 / Math.sqrt(rets.length))) : (mu1 !== 0 ? Infinity : 0);
    var driftUsed = Math.abs(t) >= 2;
    var drift = driftUsed ? mu1 * k : 0;
    var sigma = sd1 * Math.sqrt(k);
    var projected = Math.round(last * Math.exp(drift));
    var lower = Math.round(last * Math.exp(drift - z * sigma));
    var upper = Math.round(last * Math.exp(drift + z * sigma));
    var bandPct = (upper - lower) / last;
    var confidence = n < 40 || bandPct > 0.03 ? "low" : n < 100 || bandPct > 0.01 ? "medium" : "high";
    var mins = Math.round(horizonMs / 60000 * 10) / 10;
    return {
      last: last,
      projected: projected,
      lower: lower,
      upper: upper,
      changePct: ((projected - last) / last) * 100,
      horizonMs: horizonMs,
      samples: n,
      stepMs: Math.round(stepMs),
      drift: mu1,
      driftUsed: driftUsed,
      vol: sd1,
      bandPct: bandPct,
      confidence: confidence,
      method: (driftUsed ? "drift + volatility" : "random walk (drift not significant, t=" + (Math.round(t * 10) / 10) + ") + volatility") +
        " of log returns over the last " + n + " price samples (~" + Math.round(stepMs / 1000) + "s apart), " + mins + " min ahead, 90% band",
    };
  }

  function num(x) {
    return String(Math.round(x * 100) / 100);
  }

  function sparklinePath(samples, width, height) {
    if (!samples || samples.length < 2) return "";
    var min = Infinity, max = -Infinity;
    samples.forEach(function (s) { if (s.p < min) min = s.p; if (s.p > max) max = s.p; });
    var range = max - min;
    var n = samples.length;
    var pts = samples.map(function (s, i) {
      var x = (i / (n - 1)) * width;
      var y = range ? height - ((s.p - min) / range) * height : height / 2;
      return num(x) + "," + num(y);
    });
    return "M" + pts[0] + " L" + pts.slice(1).join(" L");
  }

  // Fixed-width buckets ending at `endT`: the last price seen at or before each
  // bucket end, carried forward through quiet buckets, null before the first sample.
  function resample(samples, bucketMs, count, endT) {
    var out = [];
    var i = 0;
    var current = null;
    for (var j = 0; j < count; j++) {
      var end = endT - (count - 1 - j) * bucketMs;
      while (i < samples.length && samples[i].t <= end) { current = samples[i].p; i++; }
      out.push(current);
    }
    return out;
  }

  function correlation(xs, ys) {
    var n = Math.min(xs.length, ys.length);
    if (n < 3) return null;
    var mx = mean(xs.slice(0, n)), my = mean(ys.slice(0, n));
    var sxy = 0, sxx = 0, syy = 0;
    for (var i = 0; i < n; i++) {
      var dx = xs[i] - mx, dy = ys[i] - my;
      sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
    }
    if (sxx === 0 || syy === 0) return null;
    return sxy / Math.sqrt(sxx * syy);
  }

  // Pairwise correlation of bucketed log returns. Buckets where either series
  // has no price yet are skipped; a pair with too few common buckets is null.
  function correlationMatrix(history, symbols, opts) {
    opts = opts || {};
    var bucketMs = opts.bucketMs || 5000;
    var buckets = opts.buckets || 60;
    var now = opts.now === undefined ? Date.now() : opts.now;
    var minPoints = opts.minPoints || 5;
    var returns = symbols.map(function (s) {
      var series = resample(history.samples(s), bucketMs, buckets, now);
      var r = [];
      for (var i = 1; i < series.length; i++) {
        r.push(series[i] === null || series[i - 1] === null ? null : Math.log(series[i] / series[i - 1]));
      }
      return r;
    });
    var matrix = symbols.map(function () { return symbols.map(function () { return null; }); });
    for (var a = 0; a < symbols.length; a++) {
      matrix[a][a] = 1;
      for (var b = a + 1; b < symbols.length; b++) {
        var xs = [], ys = [];
        for (var i = 0; i < returns[a].length; i++) {
          if (returns[a][i] !== null && returns[b][i] !== null) { xs.push(returns[a][i]); ys.push(returns[b][i]); }
        }
        var r = xs.length >= minPoints ? correlation(xs, ys) : null;
        matrix[a][b] = r;
        matrix[b][a] = r;
      }
    }
    return { symbols: symbols.slice(), matrix: matrix, bucketMs: bucketMs, buckets: buckets };
  }

  return { PriceHistory: PriceHistory, logReturns: logReturns, forecast: forecast, sparklinePath: sparklinePath, resample: resample, correlation: correlation, correlationMatrix: correlationMatrix };
});
