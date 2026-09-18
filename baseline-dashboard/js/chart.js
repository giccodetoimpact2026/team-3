// Price chart as an inline SVG string: the price line built from SSE samples,
// our own fills marked as dots (Raymond: "real charts with the bot's trades
// marked on them"), our resting orders as dashed lines at their limit prices,
// and the forecast drawn as a cone ahead of the last price.
// Pure string building, no DOM, so the layout is unit-tested in Node.
// Depends on format.js for money and SGT time labels.
(function (root, factory) {
  const api = factory(
    typeof module !== "undefined" && module.exports ? require("./format.js") : root
  );
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.Chart = api;
  }
})(typeof self !== "undefined" ? self : this, function (fmt) {
  var PAD = { l: 68, r: 20, t: 14, b: 26 };

  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function n1(x) { return String(Math.round(x * 10) / 10); }
  function clock(t) { return fmt.formatSgt(t).slice(11, 19); }

  function chartSvg(opts) {
    var symbol = opts.symbol || "";
    var samples = opts.samples || [];
    var fills = opts.fills || [];
    var orders = opts.orders || [];
    var f = opts.forecast || null;
    var W = opts.width || 900, H = opts.height || 260;
    var head = '<svg class="price-chart" viewBox="0 0 ' + W + " " + H + '" width="100%" height="' + H + '" role="img" aria-label="' + esc(symbol) + ' price chart">';
    if (!samples.length) {
      return head + '<text class="placeholder" x="' + W / 2 + '" y="' + H / 2 + '" text-anchor="middle">Waiting for price events for ' + esc(symbol) + "…</text></svg>";
    }
    var n = samples.length;
    var t0 = samples[0].t;
    var tLast = samples[n - 1].t;
    var tEnd = f ? tLast + (f.horizonMs || 0) : tLast;
    if (tEnd <= t0) tEnd = t0 + 1;
    var pMin = Infinity, pMax = -Infinity;
    function grow(p) { if (typeof p === "number") { if (p < pMin) pMin = p; if (p > pMax) pMax = p; } }
    samples.forEach(function (s) { grow(s.p); });
    fills.forEach(function (x) { grow(x.p); });
    orders.forEach(function (o) { grow(o.price); });
    if (f) { grow(f.lower); grow(f.upper); grow(f.projected); }
    if (pMax === pMin) { pMax += 1; pMin -= 1; }
    var iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
    function x(t) { return PAD.l + ((t - t0) / (tEnd - t0)) * iw; }
    function y(p) { return PAD.t + ((pMax - p) / (pMax - pMin)) * ih; }

    var out = [head];
    out.push('<text class="chart-title" x="' + PAD.l + '" y="' + (PAD.t - 2) + '">' + esc(symbol) + "</text>");
    // Horizontal grid at min, mid and max with dollar labels.
    [pMin, (pMin + pMax) / 2, pMax].forEach(function (p) {
      var yy = n1(y(p));
      out.push('<line class="grid" x1="' + PAD.l + '" x2="' + (W - PAD.r) + '" y1="' + yy + '" y2="' + yy + '"/>');
      out.push('<text class="axis" x="' + (PAD.l - 6) + '" y="' + n1(y(p) + 4) + '" text-anchor="end">' + esc(fmt.formatCurrency(Math.round(p))) + "</text>");
    });
    // Time labels at the first and last sample.
    out.push('<text class="axis" x="' + PAD.l + '" y="' + (H - 8) + '">' + esc(clock(t0)) + "</text>");
    out.push('<text class="axis" x="' + n1(x(tLast)) + '" y="' + (H - 8) + '" text-anchor="end">' + esc(clock(tLast)) + "</text>");
    // Price line.
    var d = samples.map(function (s, i) { return (i ? "L" : "M") + n1(x(s.t)) + "," + n1(y(s.p)); }).join(" ");
    out.push('<path class="price" d="' + d + '"/>');
    // Forecast cone from the last sample to the horizon.
    if (f) {
      var xl = n1(x(tLast)), yl = n1(y(samples[n - 1].p)), xe = n1(x(tEnd));
      out.push('<polygon class="cone" points="' + xl + "," + yl + " " + xe + "," + n1(y(f.upper)) + " " + xe + "," + n1(y(f.lower)) + '"/>');
      out.push('<line class="projected" x1="' + xl + '" y1="' + yl + '" x2="' + xe + '" y2="' + n1(y(f.projected)) + '"/>');
      out.push('<text class="axis forecast-label" x="' + xe + '" y="' + n1(y(f.projected) - 6) + '" text-anchor="end">est. ' + esc(fmt.formatCurrency(f.projected)) + " · " + esc(f.confidence || "") + "</text>");
    }
    // Our resting orders: a dashed line across the plot at the limit price.
    orders.forEach(function (o) {
      if (typeof o.price !== "number") return;
      var side = String(o.side || "").toLowerCase() === "sell" ? "sell" : "buy";
      var yy = n1(y(o.price));
      var label = side.toUpperCase() + " " + esc(o.remaining) + " @ " + esc(fmt.formatCurrency(o.price));
      out.push('<line class="resting ' + side + '" x1="' + PAD.l + '" x2="' + (W - PAD.r) + '" y1="' + yy + '" y2="' + yy + '"><title>resting ' + label + "</title></line>");
      out.push('<text class="axis resting-label ' + side + '" x="' + (W - PAD.r) + '" y="' + n1(y(o.price) - 3) + '" text-anchor="end">' + label + "</text>");
    });
    // Our fills.
    fills.forEach(function (t) {
      var side = String(t.side || "").toLowerCase() === "sell" ? "sell" : "buy";
      out.push('<circle class="fill ' + side + '" cx="' + n1(x(t.t)) + '" cy="' + n1(y(t.p)) + '" r="4"><title>' + side.toUpperCase() + " " + esc(t.quantity) + " @ " + esc(fmt.formatCurrency(t.p)) + " · " + esc(clock(t.t)) + (t.strategy ? " · " + esc(t.strategy) : "") + "</title></circle>");
    });
    out.push("</svg>");
    return out.join("");
  }

  return { chartSvg: chartSvg };
});
