// Canvas candlestick renderer with a VWAP overlay, ported from the
// dashboard-homepage branch (their js/chart.js) and renamed so it does not
// clash with the SVG price chart in chart.js. Dependency-free; prices are
// integer cents, the y-axis is drawn in dollars; colours come from CSS vars.
const Candles = {
  draw: function (canvas, candles, vwap) {
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var style = getComputedStyle(canvas);
    var css = function (name, fallback) { var v = style.getPropertyValue(name); return (v && v.trim()) || fallback; };
    var fg = css("--muted", "#6b7488"), grid = css("--border", "#e2e6ef");
    var up = css("--pos", "#0a8f4f"), down = css("--neg", "#c62b3b"), vwapColor = css("--accent", "#1034a6");
    ctx.clearRect(0, 0, W, H);
    if (!candles || !candles.length) {
      ctx.fillStyle = fg; ctx.font = "13px system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillText("Waiting for price samples\u2026", W / 2, H / 2);
      return;
    }
    var padL = 68, padR = 20, padT = 14, padB = 26;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var lo = Infinity, hi = -Infinity;
    candles.forEach(function (c) { lo = Math.min(lo, c.low); hi = Math.max(hi, c.high); });
    (vwap || []).forEach(function (v) { lo = Math.min(lo, v.vwap); hi = Math.max(hi, v.vwap); });
    if (lo === hi) { lo -= 100; hi += 100; }
    var range = hi - lo; lo -= range * 0.05; hi += range * 0.05;
    function y(price) { return padT + plotH * (1 - (price - lo) / (hi - lo)); }
    var n = candles.length, step = plotW / n, bw = Math.max(1, Math.min(step * 0.6, 14));
    ctx.strokeStyle = grid; ctx.fillStyle = fg; ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "right"; ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (plotH * g) / 4;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
      ctx.fillText("$" + ((hi - ((hi - lo) * g) / 4) / 100).toFixed(2), padL - 6, gy + 3);
    }
    candles.forEach(function (c, i) {
      var cx = padL + step * i + step / 2, bull = c.close >= c.open;
      ctx.strokeStyle = bull ? up : down; ctx.fillStyle = bull ? up : down;
      ctx.beginPath(); ctx.moveTo(cx, y(c.high)); ctx.lineTo(cx, y(c.low)); ctx.stroke();
      var yo = y(c.open), yc = y(c.close);
      ctx.fillRect(cx - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)));
    });
    if (vwap && vwap.length > 1) {
      ctx.strokeStyle = vwapColor; ctx.lineWidth = 1.5; ctx.beginPath();
      vwap.forEach(function (v, i) { var vx = padL + (plotW * i) / (vwap.length - 1); if (i === 0) ctx.moveTo(vx, y(v.vwap)); else ctx.lineTo(vx, y(v.vwap)); });
      ctx.stroke();
    }
  },
};
if (typeof module !== "undefined" && module.exports) module.exports = Candles;
