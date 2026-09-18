// Minimal canvas chart renderer for the Execution tab: candlesticks built from
// the SSE price buffer, with an optional VWAP line overlay. Dependency-free
// (no charting library) to match the repo's vanilla-JS, no-build constraint.
// All prices are integer cents; the y-axis is drawn in dollars.
const Chart = {
  // candles: [{ t, open, high, low, close }] (cents), vwap: [{ t, vwap }] (cents).
  // Reads CSS custom properties so it tracks the dashboard's light/dark theme.
  candlestick: function (canvas, candles, vwap) {
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var style = getComputedStyle(canvas);
    var css = function (name, fallback) {
      var v = style.getPropertyValue(name);
      return (v && v.trim()) || fallback;
    };
    var fg = css("--chart-fg", "#c9d1d9");
    var grid = css("--chart-grid", "rgba(128,128,128,0.18)");
    var up = css("--pos", "#2ea043");
    var down = css("--neg", "#f85149");
    var vwapColor = css("--accent", "#e3b341");

    ctx.clearRect(0, 0, W, H);
    if (!candles || !candles.length) {
      ctx.fillStyle = fg;
      ctx.font = "13px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Waiting for price samples…", W / 2, H / 2);
      return;
    }

    var padL = 56, padR = 12, padT = 12, padB = 22;
    var plotW = W - padL - padR, plotH = H - padT - padB;

    var lo = Infinity, hi = -Infinity;
    candles.forEach(function (c) { lo = Math.min(lo, c.low); hi = Math.max(hi, c.high); });
    (vwap || []).forEach(function (v) { lo = Math.min(lo, v.vwap); hi = Math.max(hi, v.vwap); });
    if (lo === hi) { lo -= 100; hi += 100; }
    var range = hi - lo;
    lo -= range * 0.05; hi += range * 0.05;

    function y(price) { return padT + plotH * (1 - (price - lo) / (hi - lo)); }
    var n = candles.length;
    var step = plotW / n;
    var bw = Math.max(1, Math.min(step * 0.6, 14));

    // grid + y-axis labels (dollars)
    ctx.strokeStyle = grid;
    ctx.fillStyle = fg;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (plotH * g) / 4;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
      var price = hi - ((hi - lo) * g) / 4;
      ctx.fillText("$" + (price / 100).toFixed(2), padL - 6, gy + 3);
    }

    // candles
    candles.forEach(function (c, i) {
      var cx = padL + step * i + step / 2;
      var bull = c.close >= c.open;
      ctx.strokeStyle = bull ? up : down;
      ctx.fillStyle = bull ? up : down;
      ctx.beginPath(); ctx.moveTo(cx, y(c.high)); ctx.lineTo(cx, y(c.low)); ctx.stroke();
      var yo = y(c.open), yc = y(c.close);
      var top = Math.min(yo, yc), h = Math.max(1, Math.abs(yc - yo));
      ctx.fillRect(cx - bw / 2, top, bw, h);
    });

    // VWAP overlay
    if (vwap && vwap.length > 1) {
      ctx.strokeStyle = vwapColor;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      vwap.forEach(function (v, i) {
        var vx = padL + (plotW * i) / (vwap.length - 1);
        var vy = y(v.vwap);
        if (i === 0) ctx.moveTo(vx, vy); else ctx.lineTo(vx, vy);
      });
      ctx.stroke();
    }
  },
};

if (typeof module !== "undefined" && module.exports) module.exports = Chart;
