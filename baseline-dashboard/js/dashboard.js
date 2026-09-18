// Dashboard controller. Subscribes to the exchange SSE feed for live prices and
// session state, re-reads the portfolio (throttled) when the market moves, and
// renders the P&L cards, the positions table, the connectivity dot, the session
// badge, and the SGT clock.
//
// Data flow:  /exchange/events (SSE) ──price/session──▶ throttle ──▶ GET /portfolio
//             GET /instruments (once at startup, and again on session change)
//
// Pure logic lives in format.js and view.js so it can be unit-tested in Node.
const DashboardController = {
  instruments: [], // symbols from GET /instruments, in exchange order
  cursor: null, // FeedCursor: SSE id dedup + session boundary
  feed: null, // EventSource
  _refreshTimer: null,
  _refreshPending: false,
  _lastRefreshAt: 0,
  _lastGoodData: false,

  init: function () {
    this.cursor = new FeedCursor();
    this.markUnavailable("Connecting…");
    this.renderTimestamp();
    setInterval(this.renderTimestamp, 1000);

    var self = this;
    this.loadInstruments().always(function () {
      self.refresh();
      self.connectFeed();
    });
  },

  // --- Instruments (must-fix #6) ------------------------------------------

  loadInstruments: function () {
    var self = this;
    return ExchangeAPI.getInstruments().then(
      function (list) {
        self.instruments = (list || []).map(function (i) { return i.symbol; });
        return self.instruments;
      },
      function () {
        // Keep whatever we had (possibly empty); the table renders from the
        // portfolio alone until the list loads.
        return self.instruments;
      }
    );
  },

  // --- Live feed (must-fix #1, #4) ----------------------------------------

  connectFeed: function () {
    var self = this;
    if (this.feed) this.feed.close();
    // Same-origin via the dev proxy. The browser resends Last-Event-ID on its
    // own reconnects; a fresh page load starts from the exchange snapshot.
    var es = new EventSource(CONFIG.API_BASE_URL + "/events");
    this.feed = es;

    es.onopen = function () {
      self.setConnectivity(true);
      self.requestRefresh(); // reconnect: fresh numbers before anything is shown
    };
    es.onerror = function () {
      // EventSource retries by itself. Until it is back, nothing on screen may
      // pass as current (must-fix #4).
      self.setConnectivity(false);
      self.markUnavailable("Reconnecting…");
      self.renderSession(null); // session state is unknown until the feed is back
    };
    es.addEventListener("price", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return; // replayed
      self.requestRefresh();
    });
    es.addEventListener("session", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return;
      var payload = self.parse(e.data);
      if (payload) self.renderSession(payload.status);
      self.requestRefresh();
    });
    es.addEventListener("announcement", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return;
      var payload = self.parse(e.data);
      if (payload) self.renderAnnouncement(payload);
    });
  },

  parse: function (json) {
    try { return JSON.parse(json); } catch (_) { return null; }
  },

  // Coalesce bursts of price events into at most one portfolio read per
  // REFRESH_MIN_INTERVAL_MS. /portfolio shares a 300-per-30s window with the
  // bot, so one read per tick would starve it.
  requestRefresh: function () {
    var self = this;
    var wait = CONFIG.REFRESH_MIN_INTERVAL_MS - (Date.now() - this._lastRefreshAt);
    if (wait <= 0) return this.refresh();
    if (this._refreshTimer) return;
    this._refreshTimer = setTimeout(function () {
      self._refreshTimer = null;
      self.refresh();
    }, wait);
  },

  refresh: function () {
    var self = this;
    this._lastRefreshAt = Date.now();
    return ExchangeAPI.getPortfolio()
      .then(function (portfolio) {
        if (self.cursor.sessionChanged(portfolio.session_id)) {
          // New trading window: everything cached is stale. Re-resolve the
          // instrument list and drop the old announcement.
          self.renderAnnouncement(null);
          self.loadInstruments().always(function () { self.renderPositions(portfolio); });
        }
        self.renderCards(portfolio);
        self.renderPositions(portfolio);
        self.setConnectivity(true);
        self._lastGoodData = true;
        $("#last-updated").removeClass("stale");
      })
      .fail(function () {
        self.setConnectivity(false);
        self.markUnavailable("Unavailable");
      });
  },

  // --- Rendering -----------------------------------------------------------

  renderCards: function (portfolio) {
    $("#card-total-value .value").text(formatCurrency(portfolio.total_value));
    $("#card-realized-pnl .value").text(formatCurrency(portfolio.realized_pnl));
    $("#card-unrealized-pnl .value").text(formatCurrency(portfolio.unrealized_pnl));
    $(".card .value").removeClass("stale");
  },

  renderPositions: function (portfolio) {
    var symbols = this.instruments.length
      ? this.instruments
      : (portfolio.positions || []).map(function (p) { return p.symbol; });
    var rows = positionRows(symbols, portfolio)
      .map(function (r) {
        return (
          "<tr>" +
          "<td>" + r.symbol + "</td>" +
          "<td>" + r.quantity + "</td>" +
          "<td>" + r.avg_cost + "</td>" +
          "<td>" + r.market_value + "</td>" +
          "<td>" + r.unrealized_pnl + "</td>" +
          "</tr>"
        );
      })
      .join("");
    $("#positions-body").html(rows).removeClass("stale");
  },

  // Replace every figure with a placeholder so old numbers never pass as
  // current. Called on feed error, portfolio failure, and before first data.
  markUnavailable: function (label) {
    $(".card .value").text(label).addClass("stale");
    var cols = $("#positions-body tr:first td").length || 5;
    var symbols = this.instruments;
    if (symbols.length) {
      $("#positions-body").html(
        symbols.map(function (s) {
          var cells = "";
          for (var i = 1; i < cols; i++) cells += "<td>" + label + "</td>";
          return "<tr><td>" + s + "</td>" + cells + "</tr>";
        }).join("")
      ).addClass("stale");
    } else {
      $("#positions-body").html("").addClass("stale");
    }
    $("#last-updated").addClass("stale");
  },

  renderSession: function (status) {
    var $b = $("#session-badge");
    $b.removeClass("open halted closed").addClass(status || "").text(status ? status.toUpperCase() : "—");
  },

  renderAnnouncement: function (a) {
    var $el = $("#announcement");
    if (!a) return $el.text("").hide();
    $el.text(formatSgt(a.timestamp) + (a.affected_symbol ? " [" + a.affected_symbol + "] " : " ") + a.message).show();
  },

  renderTimestamp: function () {
    $("#last-updated").text("Last updated: " + formatSgt(new Date()));
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
