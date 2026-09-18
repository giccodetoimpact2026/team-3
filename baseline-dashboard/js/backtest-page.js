// Controller for the standalone Backtest page (backtest.html). Reads the static
// data/backtest.json artifact produced by baseline-bot/run_backtest.py and
// renders the ranked parameter grid, the current-vs-best comparison, and a
// hand-rolled equity curve. Pure logic lives in backtest.js (unit-tested);
// every exchange/artifact string rendered into HTML goes through escapeHtml.
const BacktestPage = {
  backtest: null,

  init: function () {
    $("#backtest-symbol").on("change", this.onSymbol.bind(this));
    this.renderTimestamp();
    setInterval(this.renderTimestamp, 1000);
    this.load();
  },

  load: function () {
    var self = this;
    return BacktestAPI.fetch()
      .then(function (doc) {
        self.backtest = doc;
        self.render(doc);
      })
      .fail(function () {
        $("#backtest-meta").text("No backtest results");
        $("#backtest-source").text("data/backtest.json not found — run baseline-bot/run_backtest.py");
        $("#backtest-body").html("<tr><td colspan=\"8\" class=\"empty\">No backtest data. Run run_backtest.py and copy results.json to data/backtest.json.</td></tr>");
      });
  },

  render: function (doc) {
    $("#backtest-meta").text(Backtest.metaLine(doc));
    $("#backtest-caveat").html(
      ((doc && doc.caveats) || []).map(function (c) {
        return "<div>" + escapeHtml(c) + "</div>";
      }).join("")
    );
    $("#backtest-source").text(
      "Source: " + ((doc && doc.source_file) || "—") +
      " · session " + ((doc && doc.session_id) || "—") +
      " · generated " + (doc && doc.generated_at ? formatSgt(doc.generated_at) : "—")
    );

    var symbols = Backtest.instrumentList(doc);
    $("#backtest-symbol").html(symbols.map(function (s) {
      return "<option value=\"" + escapeHtml(s) + "\">" + escapeHtml(s) + "</option>";
    }).join(""));

    if (!symbols.length) {
      $("#backtest-body").html("<tr><td colspan=\"8\" class=\"empty\">No instruments in the backtest</td></tr>");
      this.drawEquity(null);
      return;
    }
    this.renderInstrument(symbols[0]);
  },

  onSymbol: function () {
    this.renderInstrument($("#backtest-symbol").val());
  },

  renderInstrument: function (symbol) {
    var inst = Backtest.findInstrument(this.backtest, symbol);
    var rows = Backtest.backtestRows(inst).map(function (r) {
      return (
        "<tr class=\"" + escapeHtml(r.cls) + "\">" +
        "<td class=\"mono\">" + escapeHtml(r.params) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.totalPnl) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.realized) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.unrealized) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.trades) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.winRate) + "</td>" +
        "<td class=\"num\">" + escapeHtml(r.maxDd) + "</td>" +
        "<td>" + escapeHtml(r.tag) + "</td>" +
        "</tr>"
      );
    }).join("");
    $("#backtest-body").html(rows || "<tr><td colspan=\"8\" class=\"empty\">No results</td></tr>");

    var cmp = Backtest.comparisonSummary(inst);
    var $c = $("#backtest-compare");
    if (cmp && cmp.hasCurrent) {
      if (cmp.betterExists) {
        $c.text("Current params rank " + cmp.currentRank + "/" + cmp.total +
          " · best would have earned " + formatCurrency(cmp.deltaVsBestCents) + " more");
      } else {
        $c.text("Current params are already best of " + cmp.total);
      }
    } else {
      $c.text("");
    }
    this.drawEquity(inst);
  },

  // Hand-rolled 2D equity curve: current (grey) vs best (green). No chart lib.
  drawEquity: function (inst) {
    var canvas = document.getElementById("backtest-equity");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var w = canvas.width, h = canvas.height, pad = 24;
    ctx.clearRect(0, 0, w, h);

    var results = (inst && inst.results) || [];
    var current = results.filter(function (r) { return r.is_current; })[0];
    var winner = results.filter(function (r) { return r.is_winner; })[0];
    var curves = [];
    if (current && current.equity_curve && current.equity_curve.length) {
      curves.push({ curve: current.equity_curve, color: "#8a8f98", label: "Current" });
    }
    if (winner && winner.equity_curve && winner.equity_curve.length && winner !== current) {
      curves.push({ curve: winner.equity_curve, color: "#2e8b57", label: "Best" });
    }

    if (!curves.length) {
      ctx.fillStyle = "#8a8f98";
      ctx.font = "13px sans-serif";
      ctx.fillText("No equity curve for this instrument", pad, h / 2);
      return;
    }

    // Shared vertical scale so the two lines are comparable.
    var box = { w: w, h: h, pad: pad };
    var allMin = Infinity, allMax = -Infinity;
    curves.forEach(function (c) {
      c.curve.forEach(function (p) {
        if (p.equity_cents < allMin) allMin = p.equity_cents;
        if (p.equity_cents > allMax) allMax = p.equity_cents;
      });
    });

    // Zero-equity baseline.
    var span = allMax - allMin || 1;
    var zeroFrac = (0 - allMin) / span;
    var zeroY = h - pad - zeroFrac * (h - 2 * pad);
    if (zeroY >= pad && zeroY <= h - pad) {
      ctx.strokeStyle = "rgba(138,143,152,0.35)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad, zeroY);
      ctx.lineTo(w - pad, zeroY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    var self = this;
    curves.forEach(function (c) {
      var poly = Backtest.equityPolyline(c.curve, box, allMin, allMax);
      self._strokePolyline(ctx, poly, c.color);
    });

    // Legend + P&L range.
    ctx.font = "12px sans-serif";
    var lx = pad, ly = pad - 8 < 12 ? 14 : pad - 8;
    curves.forEach(function (c, i) {
      ctx.fillStyle = c.color;
      ctx.fillRect(lx + i * 90, ly - 9, 10, 10);
      ctx.fillStyle = "#8a8f98";
      ctx.fillText(c.label, lx + i * 90 + 14, ly);
    });
    ctx.fillStyle = "#8a8f98";
    ctx.textAlign = "right";
    ctx.fillText(formatCurrency(allMax), w - pad, pad - 2 < 10 ? 10 : pad - 2);
    ctx.fillText(formatCurrency(allMin), w - pad, h - pad + 12);
    ctx.textAlign = "left";
  },

  _strokePolyline: function (ctx, poly, color) {
    if (!poly) return;
    var pts = poly.split(" ");
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach(function (pt, i) {
      var xy = pt.split(",");
      var x = Number(xy[0]), y = Number(xy[1]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  },

  renderTimestamp: function () {
    $("#last-updated").text("Last updated: " + formatSgt(new Date()));
  },
};

$(function () {
  BacktestPage.init();
});
