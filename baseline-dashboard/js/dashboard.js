// Dashboard controller. Subscribes to the exchange SSE feed for live prices,
// trades and session state, re-reads the exchange (throttled) when the market
// moves, and renders: P&L cards, positions, session counters, per-instrument
// P&L (via the MCP read server), the order lifecycle table, the connectivity
// dot, the session badge, the SGT clock, and the CSV export.
//
// Data flow:
//   /exchange/events (SSE)
//     price   ──▶ refreshPortfolio (coalesced, ≥1s apart)
//     trade   ──▶ refreshAll: portfolio + orders + MCP P&L (coalesced); when
//                 the bot reports nothing resting or unknown, only the portfolio
//     session ──▶ refreshAll; session id change resets session-scoped state
//   GET /instruments once at startup and again on session change.
//   /bot/status every 3 s: lifecycle + heartbeat + feed health -> bot pill
//   /bot/orders?since=N with it: the bot's order journal -> lifecycle table
//   (exact codes incl. REJECTED / UNKNOWN, which GET /orders never shows).
//
// Pure logic lives in format.js, view.js and trades.js (unit-tested in Node).
// Every exchange-supplied string rendered into HTML goes through escapeHtml.
const DashboardController = {
  instruments: [],
  cursor: null, // FeedCursor: SSE id dedup + session boundary
  feed: null, // EventSource
  history: null, // Trades.LifecycleHistory for the current session
  portfolio: null,
  orders: [],
  pnl: null, // last MCP get_pnl response
  bot: null, // last GET /bot/status payload, or null when the bot did not answer
  _journalSeq: 0, // cursor into the bot's order journal (per bot process)
  _timers: { portfolio: null, all: null },
  _lastAt: { portfolio: 0, all: 0 },

  init: function () {
    this.cursor = new FeedCursor();
    this.history = new Trades.LifecycleHistory("", null); // unbound until a session id is known
    this.markUnavailable("Connecting…");
    this.renderTimestamp();
    setInterval(this.renderTimestamp, 1000);
    $("#export-csv").on("click", this.exportCsv.bind(this));
    $("#kill-switch").on("click", this.haltBot.bind(this));
    this.pollBotStatus();
    setInterval(this.pollBotStatus.bind(this), CONFIG.BOT_STATUS_INTERVAL_MS);

    var self = this;
    this.loadInstruments().always(function () {
      self.refreshAll();
      self.connectFeed();
    });
  },

  // --- Bot control: state pill + kill switch (P0-14) ----------------------

  _halting: false,
  pollBotStatus: function () {
    var self = this;
    if (this._halting) return; // the halt response renders the final state
    BotAPI.getStatus()
      .then(function (status) {
        self.bot = status;
        self.renderBotStatus(status);
        self.pollBotOrders();
      })
      .fail(function () {
        self.bot = null;
        self.renderBotStatus(null);
      });
  },

  // The bot -> dashboard order channel. Entries are keyed by a per-process
  // sequence; a `seq` below our cursor means the bot restarted, so start over.
  pollBotOrders: function () {
    var self = this;
    BotAPI.getOrderEvents(this._journalSeq).then(function (page) {
      if (!page) return;
      var latest = Number(page.seq || 0);
      if (latest < self._journalSeq) {
        self._journalSeq = 0;
        return self.pollBotOrders();
      }
      var events = (page.events || []).filter(function (e) {
        // A journal can hold entries from the previous window; the history is per window.
        return !e.session_id || !self.history.sessionId || e.session_id === self.history.sessionId;
      });
      if (page.events && page.events.length) self._journalSeq = page.events[page.events.length - 1].seq;
      if (events.length && self.history.record(events)) self.renderOrders();
    });
  },

  renderBotStatus: function (status) {
    var v = botStatusView(status);
    $("#bot-state").removeClass("running degraded dead quiescing stopped offline").addClass(v.cls).text(v.label).attr("title", v.detail || "");
    $("#kill-switch").prop("disabled", !v.canHalt || this._halting);
    this.renderGuards(status);
  },

  // Crash guard + DCA per instrument, from the same /bot/status payload.
  // Hidden until the bot answers, so a dashboard without a bot has no empty table.
  renderGuards: function (status) {
    var rows = guardRows(status);
    if (!rows.length) { $("#guards").hide(); return; }
    $("#guards-summary").text(guardSummary(status));
    $("#guards-body").html(rows.map(function (r) {
      var cls = r.guard.indexOf("DUMPED") === 0 ? "guard-dumped" : (r.guard.indexOf("breach") > 0 ? "guard-breach" : "");
      return "<tr><td>" + escapeHtml(r.symbol) + "</td><td class=\"num\">" + escapeHtml(r.high) + "</td><td class=\"" + cls + "\">" + escapeHtml(r.guard) + "</td><td class=\"num\">" + escapeHtml(r.dca_buys) + "</td><td class=\"num\">" + escapeHtml(r.dca_next) + "</td></tr>";
    }).join(""));
    $("#guards").show();
  },

  // Emergency stop. One click, no confirmation dialog (backlog requirement):
  // the button greys out at once, the bot blocks new intents, cancels every
  // open order and stops. The outcome is shown in a banner; the pill goes to
  // HALTED, then OFFLINE once the process has exited.
  haltBot: function () {
    var self = this;
    if (this._halting) return;
    this._halting = true;
    this.renderBotStatus({ state: "QUIESCING" });
    $("#halt-result").removeClass("error").text("Halting bot… cancelling open orders").show();
    BotAPI.halt()
      .then(function (resp) {
        $("#halt-result").text(haltResultText(resp)).show();
        self.renderBotStatus(resp);
        self.requestRefresh("all"); // cancellations change the lifecycle table
      })
      .fail(function (xhr) {
        var body = xhr && xhr.responseJSON;
        var msg = body && body.error && body.error.message ? body.error.message : (xhr && xhr.statusText) || "no response";
        $("#halt-result").addClass("error").text("Halt request failed (" + msg + "). Stop the bot from its terminal with Ctrl-C.").show();
      })
      .always(function () {
        self._halting = false;
        self.pollBotStatus();
      });
  },

  storage: function () {
    try { return window.localStorage; } catch (_) { return null; }
  },

  // --- Instruments (must-fix #6) ------------------------------------------

  loadInstruments: function () {
    var self = this;
    return ExchangeAPI.getInstruments().then(
      function (list) {
        self.instruments = (list || []).map(function (i) { return i.symbol; });
        return self.instruments;
      },
      function () { return self.instruments; }
    );
  },

  // --- Live feed (must-fix #1, #4) ----------------------------------------

  connectFeed: function () {
    var self = this;
    if (this.feed) this.feed.close();
    var es = new EventSource(CONFIG.API_BASE_URL + "/events");
    this.feed = es;

    es.onopen = function () {
      self.setConnectivity(true);
      self.requestRefresh("all");
    };
    es.onerror = function () {
      // EventSource retries by itself. Until it is back, nothing on screen may
      // pass as current (must-fix #4).
      self.setConnectivity(false);
      self.markUnavailable("Reconnecting…");
      self.renderSession(null);
    };
    es.addEventListener("price", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return; // replayed
      self.requestRefresh("portfolio");
    });
    es.addEventListener("trade", function (e) {
      // Market-wide; it does not say whose fill it was. When the bot reports
      // nothing resting and nothing unknown, none of our orders can have
      // changed, so only the portfolio (marks) is re-read; otherwise treat it
      // as a signal that our orders/fills may have changed and re-read them.
      if (!self.cursor.accept(e.lastEventId)) return;
      var o = self.bot && self.bot.orders;
      var quiet = o && o.active_orders === 0 && o.unknown_orders === 0;
      self.requestRefresh(quiet ? "portfolio" : "all");
    });
    es.addEventListener("session", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return;
      var payload = self.parse(e.data);
      if (payload) self.renderSession(payload.status);
      self.requestRefresh("all");
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

  // Coalesce bursts into at most one read per REFRESH_MIN_INTERVAL_MS per
  // kind. /portfolio, /orders and /instruments share one 300-per-30s window
  // with the bot, so one read per tick would starve it.
  requestRefresh: function (kind) {
    var self = this;
    var run = kind === "all" ? this.refreshAll : this.refreshPortfolio;
    var min = kind === "all" ? CONFIG.ORDERS_REFRESH_MIN_INTERVAL_MS : CONFIG.REFRESH_MIN_INTERVAL_MS;
    var wait = min - (Date.now() - this._lastAt[kind]);
    if (wait <= 0) return run.call(this);
    if (this._timers[kind]) return;
    this._timers[kind] = setTimeout(function () {
      self._timers[kind] = null;
      run.call(self);
    }, wait);
  },

  refreshAll: function () {
    this._lastAt.all = Date.now();
    this.refreshPortfolio();
    this.refreshOrders();
    this.refreshPnl();
  },

  refreshPortfolio: function () {
    var self = this;
    this._lastAt.portfolio = Date.now();
    return ExchangeAPI.getPortfolio()
      .then(function (portfolio) {
        self.portfolio = portfolio;
        if (self.cursor.sessionChanged(portfolio.session_id)) self.onSessionChange(portfolio.session_id);
        else self.bindHistory(portfolio.session_id);
        self.renderCards(portfolio);
        self.renderPositions(portfolio);
        if (!self.pnl) self.renderPnlFallback("MCP unavailable"); // keep per-instrument P&L live from the exchange
        self.setConnectivity(true);
        $("#last-updated").removeClass("stale");
      })
      .fail(function () {
        self.setConnectivity(false);
        self.markUnavailable("Unavailable");
      });
  },

  refreshOrders: function () {
    var self = this;
    return ExchangeAPI.getOrders()
      .then(function (orders) {
        self.orders = orders || [];
        self.bindHistory(self.orders[0] && self.orders[0].session_id);
        self.history.observe(self.orders);
        self.renderOrders();
      })
      .fail(function () {
        self.markOrdersUnavailable("Unavailable");
      });
  },

  _pnlFailedAt: 0,
  refreshPnl: function () {
    var self = this;
    // A dead MCP server should not be hammered on every trade event.
    if (Date.now() - this._pnlFailedAt < CONFIG.MCP_RETRY_MS) return;
    return MCP.getPnl()
      .then(function (resp) {
        self.pnl = resp;
        self._pnlFailedAt = 0;
        self.renderPnl(resp);
      })
      .fail(function () {
        self.pnl = null;
        self._pnlFailedAt = Date.now();
        self.renderPnlFallback("MCP unavailable");
      });
  },

  // Bind the lifecycle history to a trading window once its id is known
  // (orders and portfolio both carry it). Persisted transitions for the same
  // window are reloaded; a different window starts empty.
  bindHistory: function (sessionId) {
    if (!sessionId || this.history.key === "lifecycle:" + sessionId) return;
    this.history = new Trades.LifecycleHistory(sessionId, this.storage());
    if (this.orders.length) {
      this.history.observe(this.orders);
      this.renderOrders();
    }
  },

  // New trading window: everything cached is stale. Session-scoped history,
  // counters and announcements start empty; instruments are re-resolved.
  onSessionChange: function (sessionId) {
    var self = this;
    this.history = new Trades.LifecycleHistory(sessionId, this.storage());
    this.orders = [];
    this.pnl = null;
    this.renderAnnouncement(null);
    this.renderOrders();
    this.markPnlUnavailable("Loading…");
    this.loadInstruments().always(function () {
      if (self.portfolio) self.renderPositions(self.portfolio);
      self.refreshOrders();
      self.refreshPnl();
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
          "<td>" + escapeHtml(r.symbol) + "</td>" +
          "<td>" + escapeHtml(r.quantity) + "</td>" +
          "<td>" + escapeHtml(r.avg_cost) + "</td>" +
          "<td>" + escapeHtml(r.market_value) + "</td>" +
          "<td>" + escapeHtml(r.unrealized_pnl) + "</td>" +
          "</tr>"
        );
      })
      .join("");
    $("#positions-body").html(rows).removeClass("stale");
  },

  renderOrders: function () {
    var c = Trades.counters(this.orders, this.history);
    $("#count-buy").text(c.buy);
    $("#count-sell").text(c.sell);
    $("#count-partial").text(c.partial);
    $("#count-rejected").text(c.rejected);
    $("#ops-strip").removeClass("stale");

    var byId = {};
    this.orders.forEach(function (o) { byId[o.id] = o; });
    var rows = this.history.entries().slice().reverse().map(function (e) { // newest first on screen
      // Exchange order when it exists; otherwise the bot journal's own copy
      // (a REJECTED or UNKNOWN submit has no exchange order to look up).
      var o = byId[e.orderId] || { symbol: e.symbol, side: e.side, quantity: e.quantity, price: e.price };
      var remaining = e.remaining !== undefined && e.remaining !== null ? e.remaining : o.remaining_qty;
      var title = (e.source === "bot" ? "reported by the bot" : "seen on the exchange") + (e.code ? " · " + e.code : "");
      return (
        "<tr>" +
        "<td class=\"mono\" title=\"" + escapeHtml(e.orderId) + "\">" + escapeHtml(String(e.orderId || "").slice(0, 8)) + "</td>" +
        "<td class=\"mono\">" + escapeHtml(formatSgt(e.at)) + "</td>" +
        "<td>" + escapeHtml(o.symbol) + "</td>" +
        "<td>" + escapeHtml(String(o.side || "").toUpperCase()) + "</td>" +
        "<td class=\"num\">" + escapeHtml(o.quantity) + "</td>" +
        "<td class=\"num\">" + escapeHtml(formatCurrencyOrDash(o.price)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(remaining === undefined || remaining === null ? "—" : remaining) + "</td>" +
        "<td title=\"" + escapeHtml(title) + "\"><span class=\"order-status " + escapeHtml(e.status.toLowerCase()) + "\">" + escapeHtml(e.status) + (e.code ? " <small>" + escapeHtml(e.code) + "</small>" : "") + "</span></td>" +
        "</tr>"
      );
    }).join("");
    $("#lifecycle-body").html(rows || "<tr><td colspan=\"8\" class=\"empty\">No orders observed this session</td></tr>").removeClass("stale");
    $("#lifecycle-count").text(this.history.entries().length + " transitions · " + this.orders.length + " orders");
    $("#export-csv").prop("disabled", this.orders.length === 0 && this.history.entries().length === 0);
  },

  renderPnl: function (resp) {
    var data = (resp && resp.data) || {};
    var by = data.by_instrument || {};
    var symbols = this.instruments.length ? this.instruments : Object.keys(by);
    var rows = symbols.map(function (s) {
      var r = by[s] || {};
      var known = by[s] !== undefined;
      function cell(v) { return known && v !== undefined ? formatDollars(v) : "—"; }
      return (
        "<tr>" +
        "<td>" + escapeHtml(s) + "</td>" +
        "<td class=\"num\">" + escapeHtml(cell(r.realized_pnl)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(cell(r.unrealized_pnl)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(cell(r.total_pnl)) + "</td>" +
        "</tr>"
      );
    }).join("");
    $("#pnl-body").html(rows).removeClass("stale");
    var agg = data.aggregate || {};
    $("#pnl-total").text(agg.total_pnl !== undefined ? formatDollars(agg.total_pnl) : "—").removeClass("stale");
    $("#pnl-source").text("Source: " + (resp.source || "MCP") + " · as of " + (resp.as_of || "—")).removeClass("stale");
  },

  // MCP is down: fall back to the exchange's own per-position figures, with
  // the source label saying so. Without a portfolio there is nothing to show.
  renderPnlFallback: function (label) {
    if (!this.portfolio || !this.portfolio.session_id) return this.markPnlUnavailable(label);
    this.renderPnl(Trades.pnlFromPortfolio(this.portfolio, this.instruments));
  },

  // Replace every figure with a placeholder so old numbers never pass as
  // current. Called on feed error, portfolio failure, and before first data.
  markUnavailable: function (label) {
    $(".card .value").text(label).addClass("stale");
    var symbols = this.instruments;
    if (symbols.length) {
      $("#positions-body").html(
        symbols.map(function (s) {
          var cells = "";
          for (var i = 0; i < 4; i++) cells += "<td>" + escapeHtml(label) + "</td>";
          return "<tr><td>" + escapeHtml(s) + "</td>" + cells + "</tr>";
        }).join("")
      ).addClass("stale");
    } else {
      $("#positions-body").html("").addClass("stale");
    }
    $("#last-updated").addClass("stale");
    this.markOrdersUnavailable(label);
    this.markPnlUnavailable(label);
  },

  markOrdersUnavailable: function (label) {
    $("#count-buy, #count-sell, #count-partial, #count-rejected").text("—");
    $("#ops-strip").addClass("stale");
    $("#lifecycle-body").addClass("stale");
    $("#lifecycle-count").text(label);
  },

  markPnlUnavailable: function (label) {
    var symbols = this.instruments;
    $("#pnl-body").html(
      symbols.map(function (s) {
        return "<tr><td>" + escapeHtml(s) + "</td><td>" + escapeHtml(label) + "</td><td>" + escapeHtml(label) + "</td><td>" + escapeHtml(label) + "</td></tr>";
      }).join("")
    ).addClass("stale");
    $("#pnl-total").text(label).addClass("stale");
    $("#pnl-source").text(label).addClass("stale");
  },

  renderSession: function (status) {
    var $b = $("#session-badge");
    $b.removeClass("open halted closed").addClass(status || "").text(status ? String(status).toUpperCase() : "—");
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

  // --- Export (Gerald's fixed schema) --------------------------------------

  exportCsv: function () {
    var p = this.portfolio || {};
    var rows = Trades.csvRows(this.orders, p.trades || [], p.positions || []);
    var csv = Trades.toCsv(rows);
    var stamp = formatSgt(new Date()).replace(/[: ]/g, "-").replace(/\.\d{3}-SGT$/, "");
    var name = "orders_" + String(p.session_id || "session").slice(0, 8) + "_" + stamp + ".csv";
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    $("#export-status").text(rows.length + " rows exported to " + name);
  },
};

$(function () {
  DashboardController.init();
});
