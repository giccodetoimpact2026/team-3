// Dashboard controller. Polls the exchange for portfolio state and renders the
// P&L cards, the positions table, the connectivity dot, and the clock.
//
// See HANDOVER.md for what's rough — reliability (live data + reconnect), the
// connectivity signal, missing-instrument handling, and the timezone all need
// work.
const DashboardController = {
  init: function () {
    this.refresh();
    // Poll the REST API on a timer. In fast markets this lags and it never sees
    // MARKET_HALTED — the exchange publishes a live SSE feed; subscribe to that.
    setInterval(this.refresh, CONFIG.POLL_INTERVAL_MS);

    this.renderTimestamp();
    setInterval(this.renderTimestamp, 1000);
  },

  refresh: function () {
    ExchangeAPI.getPortfolio()
      .then(function (portfolio) {
        DashboardController.renderCards(portfolio);
        DashboardController.renderPositions(portfolio);
        DashboardController.setConnectivity(true);
      })
      .fail(function () {
        // The exchange didn't answer this cycle. We flip the dot but leave the
        // last numbers on screen, so they quietly go stale during a blip.
        DashboardController.setConnectivity(false);
      });
  },

  renderCards: function (portfolio) {
    $("#card-total-value .value").text(formatCurrency(portfolio.total_value));
    $("#card-realized-pnl .value").text(formatCurrency(portfolio.realized_pnl));
    $("#card-unrealized-pnl .value").text(formatCurrency(portfolio.unrealized_pnl));
  },

  renderPositions: function (portfolio) {
    var bySymbol = {};
    (portfolio.positions || []).forEach(function (p) {
      bySymbol[p.symbol] = p;
    });

    var rows = "";
    CONFIG.INSTRUMENTS_TO_SHOW.forEach(function (symbol) {
      var p = bySymbol[symbol] || {};
      rows +=
        "<tr>" +
        "<td>" + symbol + "</td>" +
        "<td>" + p.quantity + "</td>" +
        "<td>" + formatCurrency(p.avg_cost) + "</td>" +
        "<td>" + formatCurrency(p.market_value) + "</td>" +
        "<td>" + formatCurrency(p.unrealized_pnl) + "</td>" +
        "</tr>";
    });
    $("#positions-body").html(rows);
  },

  renderTimestamp: function () {
    $("#last-updated").text("Last updated: " + new Date().toUTCString());
  },

  setConnectivity: function (online) {
    var $dot = $("#connectivity-dot");
    $dot.removeClass("online offline").addClass(online ? "online" : "offline");
    $("#connectivity-label").text(online ? "ONLINE" : "OFFLINE");
  },
};

$(function () {
  DashboardController.init();
});
