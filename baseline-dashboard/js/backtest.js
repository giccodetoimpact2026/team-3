// Pure view-model helpers for the parameter-sweep backtest section. DOM-free so
// they can be unit-tested in Node (see tests/backtest.test.js) and loaded as
// browser globals. Depends on format.js. Reads the results.json produced by the
// bot's run_backtest.py (integer cents throughout).
(function (root, factory) {
  const api = factory(
    typeof module !== "undefined" && module.exports ? require("./format.js") : root
  );
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.Backtest = api; // browser global
  }
})(typeof self !== "undefined" ? self : this, function (fmt) {
  function instrumentList(doc) {
    return ((doc && doc.instruments) || []).map(function (i) { return i.symbol; });
  }

  function findInstrument(doc, symbol) {
    return ((doc && doc.instruments) || []).filter(function (i) {
      return i.symbol === symbol;
    })[0] || null;
  }

  // "lookback 5, thr 0.003" — order-stable, human-readable.
  function formatParams(params) {
    if (!params) return "—";
    var lb = params.lookback_samples;
    var thr = params.momentum_threshold !== undefined
      ? params.momentum_threshold
      : params.deviation_threshold;
    return "lookback " + lb + ", thr " + thr;
  }

  // A win rate is only meaningful once a position has been closed by a sell.
  // With no sells, show "N/A" rather than a misleading 0% or 100%.
  function formatWinRate(rate, sellCount) {
    if (!sellCount || rate === null || rate === undefined) return "N/A";
    return Math.round(rate * 100) + "%";
  }

  // One display row per parameter set, ranked order preserved. Money strings via
  // format.js; the caller escapes before injecting into HTML.
  function backtestRows(instrument) {
    var results = (instrument && instrument.results) || [];
    return results.map(function (r) {
      var m = r.metrics || {};
      var tags = [];
      if (r.is_winner) tags.push("Best");
      if (r.is_current) tags.push("Current");
      var cls = [];
      if (r.is_winner) cls.push("row-winner");
      if (r.is_current) cls.push("row-current");
      return {
        params: formatParams(r.params),
        totalPnl: fmt.formatCurrency(m.total_pnl_cents),
        realized: fmt.formatCurrency(m.realized_pnl_cents),
        unrealized: fmt.formatCurrency(m.unrealized_pnl_cents),
        trades: String(m.trade_count === undefined ? 0 : m.trade_count),
        winRate: formatWinRate(m.win_rate, m.sell_count),
        maxDd: fmt.formatCurrency(m.max_drawdown_cents),
        tag: tags.join(" · ") || "—",
        cls: cls.join(" "),
      };
    });
  }

  // Current-vs-best: where the live config ranks, and how much P&L the best set
  // would have added. Deltas in cents (caller formats).
  function comparisonSummary(instrument) {
    var results = (instrument && instrument.results) || [];
    if (!results.length) return null;
    var best = results[0]; // ranked, so rank 1 is first
    var current = results.filter(function (r) { return r.is_current; })[0];
    if (!current) return { total: results.length, hasCurrent: false };
    var delta = best.metrics.total_pnl_cents - current.metrics.total_pnl_cents;
    return {
      hasCurrent: true,
      total: results.length,
      currentRank: current.rank,
      deltaVsBestCents: delta,
      betterExists: delta > 0,
    };
  }

  function metaLine(doc) {
    if (!doc || !doc.sample) return "—";
    var s = doc.sample;
    var fm = doc.fill_model || {};
    var sessions = s.is_bundled_sample ? "bundled sample" : "1 session";
    return s.ticks + " ticks · " + sessions +
      " · fill: " + (fm.type || "?") + " (" + (fm.slippage_bps || 0) + " bps slippage)";
  }

  // Scale an equity curve [{t, equity_cents}] to a coordinate string
  // "x1,y1 x2,y2 ..." inside box {w, h, pad}. Highest equity maps to the top
  // (small y). Empty curve -> "". A flat curve draws along the vertical middle.
  // `minOverride`/`maxOverride` force a shared vertical scale across curves;
  // omit them to scale to this curve alone. Pure geometry — testable without a
  // canvas.
  function equityPolyline(curve, box, minOverride, maxOverride) {
    curve = curve || [];
    if (!curve.length) return "";
    var w = box.w, h = box.h, pad = box.pad || 0;
    var n = curve.length;
    var min = curve[0].equity_cents, max = curve[0].equity_cents;
    for (var i = 1; i < n; i++) {
      var v = curve[i].equity_cents;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    if (minOverride !== undefined && minOverride !== null) min = minOverride;
    if (maxOverride !== undefined && maxOverride !== null) max = maxOverride;
    var span = max - min;
    var innerW = w - 2 * pad;
    var innerH = h - 2 * pad;
    var pts = [];
    for (var j = 0; j < n; j++) {
      var x = n === 1 ? pad + innerW / 2 : pad + (j / (n - 1)) * innerW;
      var frac = span === 0 ? 0.5 : (curve[j].equity_cents - min) / span;
      var y = h - pad - frac * innerH; // invert: max equity at top
      pts.push(round1(x) + "," + round1(y));
    }
    return pts.join(" ");
  }

  function round1(n) { return Math.round(n * 10) / 10; }

  return {
    instrumentList: instrumentList,
    findInstrument: findInstrument,
    formatParams: formatParams,
    formatWinRate: formatWinRate,
    backtestRows: backtestRows,
    comparisonSummary: comparisonSummary,
    metaLine: metaLine,
    equityPolyline: equityPolyline,
  };
});
