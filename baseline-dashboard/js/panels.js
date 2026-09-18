// DOM rendering for the persona panels. Thin: every number and row comes from
// the pure modules (views.js, prices.js, chart.js, analytics.js) via the
// controller; this file only writes HTML. Every string from the exchange or
// the bot goes through escapeHtml (view.js). Depends on jQuery and the browser
// globals. API (kept stable for dashboard.js):
//   renderViewSwitcher(current, onSelect)   applyView(viewId) -> resolved id
//   renderAlerts(alerts, meta?)            renderHeatmap(tiles, sourceLabel?)
//   renderChartTabs(symbols, current, onSelect)  renderChart(symbol, samples, fills, forecast)
//   sparklineCell(samples)                 renderFillRate(rows)
//   renderExposure(rows, net, capsKnown)   renderCorrelation(matrix)
//   renderForecast(cards)                  renderPerformance(rows)
//   renderSummary(summary, sessionId, botLabel?)
const Panels = {
  DASH: "—",

  // --- View switcher --------------------------------------------------------
  renderViewSwitcher: function (current, onSelect) {
    var $nav = $("#view-switcher").empty();
    Views.VIEWS.forEach(function (v) {
      var $b = $("<button type=\"button\"></button>").text(v.label).attr("title", v.audience).attr("data-view", v.id);
      $b.on("click", function () { onSelect(v.id); });
      $nav.append($b);
    });
    return this.applyView(current);
  },

  // Shows the sections of `viewId`, hides the rest; returns the resolved id.
  applyView: function (viewId) {
    var id = Views.resolveView(viewId);
    $("[data-section]").each(function () {
      $(this).toggleClass("view-hidden", !Views.isVisible(id, $(this).attr("data-section")));
    });
    $("#view-switcher button").each(function () {
      $(this).toggleClass("active", $(this).attr("data-view") === id);
    });
    var view = Views.VIEWS.filter(function (v) { return v.id === id; })[0];
    $("#view-audience").text(view ? "For " + view.audience : "");
    return id;
  },

  // --- Risk: alerts ---------------------------------------------------------
  renderAlerts: function (alerts, meta) {
    alerts = alerts || [];
    var $list = $("#alerts-list");
    if (!alerts.length) {
      $list.html("<div class=\"alert none\"><span class=\"code\">OK</span><span>No alert: drawdown, position caps, instrument drops and bot health are within limits.</span></div>");
    } else {
      $list.html(alerts.map(function (a) {
        return "<div class=\"alert " + escapeHtml(a.level) + "\"><span class=\"code\">" + escapeHtml(a.code) + "</span><span>" + escapeHtml(a.message) + "</span></div>";
      }).join(""));
    }
    if (meta === undefined) {
      var crit = alerts.filter(function (a) { return a.level === "critical"; }).length;
      meta = alerts.length
        ? crit + " critical, " + (alerts.length - crit) + " warning"
        : "Limits: drawdown " + CONFIG.ALERT_THRESHOLDS.drawdownPct + "% of starting capital, instrument drop " + CONFIG.ALERT_THRESHOLDS.instrumentDropPct + "%, heartbeat " + CONFIG.ALERT_THRESHOLDS.heartbeatDeadS + "s";
    }
    $("#alerts-meta").text(meta);
  },

  // --- Executive: heat map --------------------------------------------------
  renderHeatmap: function (tiles, sourceLabel) {
    $("#heat-grid").html((tiles || []).map(function (t) {
      var alpha = (0.12 + 0.55 * t.intensity).toFixed(2);
      var bg = t.cls === "pos" ? "rgba(10,143,79," + alpha + ")" : t.cls === "neg" ? "rgba(198,43,59," + alpha + ")" : "";
      return "<div class=\"heat-tile " + t.cls + "\"" + (bg ? " style=\"background:" + bg + "\"" : "") + "><div class=\"sym\">" + escapeHtml(t.symbol) + "</div><div class=\"val\">" + escapeHtml(t.label) + "</div></div>";
    }).join(""));
    if (sourceLabel) $("#heatmap-meta").text(sourceLabel);
  },

  // --- Trader: chart --------------------------------------------------------
  renderChartTabs: function (symbols, current, onSelect) {
    var $tabs = $("#chart-tabs");
    if ($tabs.data("symbols") !== symbols.join()) {
      $tabs.empty().data("symbols", symbols.join());
      symbols.forEach(function (s) {
        $tabs.append($("<button type=\"button\" role=\"tab\"></button>").text(s).attr("data-symbol", s).on("click", function () { onSelect(s); }));
      });
    }
    $tabs.find("button").each(function () { $(this).toggleClass("active", $(this).attr("data-symbol") === current); });
  },

  renderChart: function (symbol, samples, fills, forecast, resting) {
    samples = samples || [];
    fills = fills || [];
    resting = resting || [];
    $("#chart-box").html(Chart.chartSvg({ symbol: symbol || "", samples: samples, fills: fills, forecast: forecast || null, orders: resting, width: 900, height: 260 }));
    var meta = samples.length + " price samples";
    if (fills.length) meta += " · " + fills.length + " of our fills";
    if (resting.length) meta += " · " + resting.length + " resting order" + (resting.length === 1 ? "" : "s") + " (dashed)";
    if (forecast) meta += " · 5-min estimate " + formatCurrency(forecast.projected) + " (" + formatCurrency(forecast.lower) + " to " + formatCurrency(forecast.upper) + ", " + forecast.confidence + " confidence)";
    else if (samples.length) meta += " · forecast needs " + CONFIG.FORECAST_MIN_SAMPLES + " samples";
    $("#chart-meta").text(meta);
  },

  sparklineCell: function (samples) {
    var d = Prices.sparklinePath((samples || []).slice(-120), 100, 24);
    if (!d) return "<td class=\"spark\"></td>";
    return "<td class=\"spark\"><svg viewBox=\"0 0 100 24\" aria-hidden=\"true\"><path d=\"" + d + "\"/></svg></td>";
  },

  // --- Trader: fill rate ----------------------------------------------------
  renderFillRate: function (rows) {
    $("#fillrate-body").html((rows || []).map(function (r) {
      return "<tr><td>" + escapeHtml(r.symbol) + "</td><td class=\"num\">" + r.sent + "</td><td class=\"num\">" + r.filled + "</td><td class=\"num\">" + r.partial + "</td><td class=\"num\">" + r.rejected + "</td><td class=\"num\">" + escapeHtml(r.fillRate) + "</td></tr>";
    }).join(""));
  },

  // --- Risk: exposure -------------------------------------------------------
  renderExposure: function (rows, net, capsKnown) {
    var self = this;
    $("#exposure-body").html((rows || []).map(function (r) {
      var pct = r.pctOfCap;
      var barCls = pct === null ? "" : pct > 100 ? " over" : pct > 80 ? " high" : "";
      var bar = pct === null ? self.DASH : "<span class=\"bar" + barCls + "\"><span style=\"width:" + Math.min(100, pct) + "%\"></span></span>" + pct + "%";
      return "<tr><td>" + escapeHtml(r.symbol) + "</td><td class=\"num" + (r.overCap ? " over" : "") + "\">" + r.quantity + "</td><td class=\"num\">" + (r.cap === null ? self.DASH : r.cap) + "</td><td>" + bar + "</td><td class=\"num\">" + escapeHtml(formatCurrency(r.marketValue)) + "</td><td class=\"num\">" + r.share + "%</td></tr>";
    }).join(""));
    net = net || { longValue: 0, totalValue: 0, pct: 0 };
    $("#exposure-meta").text("Net long " + formatCurrency(net.longValue) + " = " + net.pct + "% of " + formatCurrency(net.totalValue) + (capsKnown ? "" : " · caps unknown until the bot reports /status"));
  },

  // --- Risk: correlation ----------------------------------------------------
  renderCorrelation: function (m) {
    var self = this;
    $("#corr-head").html("<th></th>" + m.symbols.map(function (s) { return "<th>" + escapeHtml(s) + "</th>"; }).join(""));
    $("#corr-body").html(m.symbols.map(function (s, i) {
      return "<tr><th>" + escapeHtml(s) + "</th>" + m.symbols.map(function (_, j) {
        var r = m.matrix[i][j];
        if (i === j) return "<td class=\"diag\">1.00</td>";
        if (r === null) return "<td class=\"na\">" + self.DASH + "</td>";
        var alpha = (0.1 + 0.6 * Math.abs(r)).toFixed(2);
        return "<td style=\"background:" + (r > 0 ? "rgba(198,43,59," : "rgba(16,52,166,") + alpha + ")\">" + r.toFixed(2) + "</td>";
      }).join("") + "</tr>";
    }).join(""));
    $("#correlation-meta").text(Math.round((m.bucketMs * m.buckets) / 60000) + "-minute window, " + (m.bucketMs / 1000) + "s returns. Red = move together (long both is one doubled bet), blue = opposite. Dash = not enough common samples yet.");
  },

  // --- Analyst: forecast cards ---------------------------------------------
  renderForecast: function (cards) {
    var self = this;
    $("#forecast-grid").html((cards || []).map(function (c) {
      var s = c.symbol, f = c.forecast;
      if (!f) {
        return "<div class=\"forecast-card\"><div class=\"sym\">" + escapeHtml(s) + "<span class=\"conf unverified\">no estimate</span></div><div class=\"proj\">" + self.DASH + "</div><div class=\"basis\">Collecting price samples (" + (c.samples || 0) + "/" + CONFIG.FORECAST_MIN_SAMPLES + ")</div></div>";
      }
      var dir = f.projected > f.last ? "▲" : f.projected < f.last ? "▼" : "▶";
      var pct = ((f.projected - f.last) / f.last * 100).toFixed(2);
      return "<div class=\"forecast-card\"><div class=\"sym\">" + escapeHtml(s) + "<span class=\"conf " + escapeHtml(f.confidence) + "\">" + escapeHtml(f.confidence) + "</span></div>" +
        "<div class=\"proj\">" + dir + " " + escapeHtml(formatCurrency(f.projected)) + " <small>(" + (pct > 0 ? "+" : "") + pct + "%)</small></div>" +
        "<div class=\"band\">90% band " + escapeHtml(formatCurrency(f.lower)) + " to " + escapeHtml(formatCurrency(f.upper)) + " · last " + escapeHtml(formatCurrency(f.last)) + "</div>" +
        "<div class=\"basis\">Estimate, " + Math.round(f.horizonMs / 60000) + " min ahead. " + escapeHtml(f.method) + "</div></div>";
    }).join(""));
  },

  // --- Analyst: performance -------------------------------------------------
  renderPerformance: function (rows) {
    var self = this;
    function money(v) { return v === null ? self.DASH : formatCurrency(v); }
    $("#performance-body").html((rows || []).map(function (r) {
      return "<tr><td>" + escapeHtml(r.symbol) + "</td><td class=\"num\">" + r.fills + "</td><td class=\"num\">" + r.wins + "</td><td class=\"num\">" + r.losses + "</td><td class=\"num\">" + escapeHtml(r.winRate) + "</td><td class=\"num\">" + escapeHtml(money(r.avgWin)) + "</td><td class=\"num\">" + escapeHtml(money(r.avgLoss)) + "</td><td class=\"num\">" + escapeHtml(r.profitFactor) + "</td><td class=\"num\">" + escapeHtml(formatCurrency(r.realized)) + "</td></tr>";
    }).join(""));
  },

  // --- Ops: session summary -------------------------------------------------
  // --- Trader / Risk / Ops: what the bot is working right now ---------------
  renderOpenOrders: function (rows) {
    var self = this;
    rows = rows || [];
    var $body = $("#open-orders-body");
    if (!$body.length) return; // section not in the page yet
    $body.html(rows.length ? rows.map(function (r) {
      var age = r.ageS >= 60 ? Math.floor(r.ageS / 60) + "m " + (r.ageS % 60) + "s" : r.ageS + "s";
      return "<tr><td class=\"mono\" title=\"" + escapeHtml(r.id) + "\">" + escapeHtml(String(r.id || "").slice(0, 8)) + "</td><td class=\"num\">" + escapeHtml(age) + "</td><td>" + escapeHtml(r.symbol) + "</td><td class=\"side " + escapeHtml(String(r.side).toLowerCase()) + "\">" + escapeHtml(r.side) + "</td><td class=\"num\">" + r.quantity + "</td><td class=\"num\">" + r.filled + "</td><td class=\"num\">" + r.remaining + "</td><td class=\"num\">" + escapeHtml(formatCurrency(r.price)) + "</td><td><span class=\"order-status " + escapeHtml(String(r.status).toLowerCase()) + "\">" + escapeHtml(r.status) + "</span></td></tr>";
    }).join("") : "<tr><td colspan=\"9\" class=\"empty\">Nothing resting: the bot has no open orders on the exchange</td></tr>");
    var buys = rows.filter(function (r) { return r.side === "BUY"; }).length;
    $("#open-orders-meta").text(rows.length ? rows.length + " working · " + buys + " buy, " + (rows.length - buys) + " sell · from GET /orders" : "nothing resting");
  },

  renderSummary: function (s, sessionId, botLabel) {
    var self = this;
    var items = [
      ["Total P&L", formatCurrency(s.totalPnl)],
      ["Fills", String(s.fills)],
      ["Best", s.best || self.DASH],
      ["Worst", s.worst || self.DASH],
      ["Alerts", String(s.alerts)],
    ];
    $("#summary-grid").html(items.map(function (i) {
      return "<div class=\"stat\"><span class=\"label\">" + escapeHtml(i[0]) + "</span><span class=\"num\">" + escapeHtml(i[1]) + "</span></div>";
    }).join(""));
    $("#summary-meta").text("Window " + String(sessionId || "").slice(0, 8) + (botLabel ? " · bot " + botLabel : "") + " · as of " + formatSgt(new Date()));
  },
};
