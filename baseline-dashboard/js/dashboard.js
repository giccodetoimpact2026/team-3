// Dashboard controller. Subscribes to the exchange SSE feed for live prices,
// trades and session state, re-reads the exchange (throttled) when the market
// moves, and renders a seven-tab single-page app that shares one SSE
// connection and one data model across every tab:
//
//   Overview     P&L cards, portfolio heat map, session counters, positions,
//                per-instrument P&L (via the MCP read server, exchange fallback)
//   Strategy     per-instrument regime/strategy (scaffold — needs a bot feed) +
//                live news-strategy params with a two-step confirm
//   Execution    candlestick + VWAP (built client-side from the SSE buffers),
//                fill-rate table, order lifecycle
//   Performance  risk-adjusted analytics derived from the trade log
//   Risk         net/gross exposure, drawdown, correlation matrix; pause/stops
//                are a scaffold (no bot endpoint yet)
//   Ops / Logs   session summary (MCP), trade log + CSV export, audit log
//   Assistant    scaffold (Phase-3 LLM over MCP)
//
// Pure logic lives in format.js, view.js, trades.js and analytics.js
// (unit-tested in Node). Every exchange-supplied string rendered into HTML goes
// through escapeHtml.
const DashboardController = {
  instruments: [],
  cursor: null, // FeedCursor: SSE id dedup + session boundary
  feed: null, // EventSource
  history: null, // Trades.LifecycleHistory for the current session
  portfolio: null,
  orders: [],
  pnl: null, // last MCP get_pnl response
  priceBuf: {}, // symbol -> [{ t, price }] (cents) from SSE `price`
  tradeBuf: {}, // symbol -> [{ t, price, qty }] from SSE `trade`
  equity: [], // [{ t, value }] portfolio total_value snapshots (cents)
  audit: [], // [{ at, action, detail }] human actions this session
  chartSymbol: null,
  chartBucket: 60000,
  _newsConfig: null,
  _pendingParam: null, // staged param change awaiting confirm
  _pendingOrder: null, // staged manual buy/sell awaiting confirm
  _timers: { portfolio: null, all: null },
  _lastAt: { portfolio: 0, all: 0 },
  _chartAt: 0,
  BUF_CAP: 5000,

  init: function () {
    Tabs.init();
    this.cursor = new FeedCursor();
    this.history = new Trades.LifecycleHistory("", null); // unbound until a session id is known
    this.markUnavailable("Connecting…");
    this.renderTimestamp();
    setInterval(this.renderTimestamp, 1000);

    $("#export-csv").on("click", this.exportCsv.bind(this));
    $("#kill-switch").on("click", this.haltBot.bind(this));
    this.bindChartControls();
    this.bindNewsControls();
    this.bindManualControls();
    this.loadNewsConfig();
    this.pollBotStatus();
    setInterval(this.pollBotStatus.bind(this), CONFIG.BOT_STATUS_INTERVAL_MS);

    // Redraw panels that need a laid-out (non-zero) size when their tab opens.
    var self = this;
    window.addEventListener("tab:show", function (e) {
      if (e.detail.tab === "execution") self.renderChart();
      if (e.detail.tab === "risk") self.renderCorrelation();
      if (e.detail.tab === "ops") self.refreshSummary();
    });

    this.loadInstruments().always(function () {
      self.refreshAll();
      self.connectFeed();
    });
  },

  // --- Bot control: state pill + kill switch ------------------------------

  _halting: false,
  pollBotStatus: function () {
    var self = this;
    if (this._halting) return; // the halt response renders the final state
    BotAPI.getStatus()
      .then(function (status) { self.renderBotStatus(status); })
      .fail(function () { self.renderBotStatus(null); });
  },

  renderBotStatus: function (status) {
    var v = botStatusView(status);
    $("#bot-state").removeClass("running quiescing stopped offline").addClass(v.cls).text(v.label);
    $("#kill-switch").prop("disabled", !v.canHalt || this._halting);
  },

  // Emergency stop. One click, no confirmation dialog (backlog requirement).
  haltBot: function () {
    var self = this;
    if (this._halting) return;
    this._halting = true;
    this.logAudit("KILL SWITCH", "Emergency halt requested");
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

  // --- Instruments --------------------------------------------------------

  loadInstruments: function () {
    var self = this;
    return ExchangeAPI.getInstruments().then(
      function (list) {
        self.instruments = (list || []).map(function (i) { return i.symbol; });
        self.populateChartSymbols();
        self.renderStrategyScaffold();
        self.renderRiskControlsScaffold();
        self.renderManualControls();
        return self.instruments;
      },
      function () { return self.instruments; }
    );
  },

  // --- Live feed ----------------------------------------------------------

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
      // pass as current.
      self.setConnectivity(false);
      self.markUnavailable("Reconnecting…");
      self.renderSession(null);
    };
    es.addEventListener("price", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return; // replayed
      self.recordPrice(self.parse(e.data));
      self.requestRefresh("portfolio");
    });
    es.addEventListener("trade", function (e) {
      if (!self.cursor.accept(e.lastEventId)) return;
      self.recordTrade(self.parse(e.data));
      self.requestRefresh("all");
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

  // Accumulate the SSE buffers that back the Execution charts and the Risk
  // correlation matrix (there is no OHLCV/correlation endpoint).
  recordPrice: function (p) {
    if (!p || !p.symbol || p.last_price === undefined) return;
    var buf = this.priceBuf[p.symbol] || (this.priceBuf[p.symbol] = []);
    buf.push({ t: this.msOf(p.timestamp), price: p.last_price });
    if (buf.length > this.BUF_CAP) buf.shift();
    this.updateManualPrice(p.symbol, p.last_price);
    if (Tabs.current() === "execution" && p.symbol === this.chartSymbol) {
      var now = Date.now();
      if (now - this._chartAt > 1000) { this._chartAt = now; this.renderChart(); }
    }
  },

  recordTrade: function (t) {
    if (!t || !t.symbol || t.price === undefined) return;
    var buf = this.tradeBuf[t.symbol] || (this.tradeBuf[t.symbol] = []);
    buf.push({ t: this.msOf(t.timestamp), price: t.price, qty: t.qty || 0 });
    if (buf.length > this.BUF_CAP) buf.shift();
  },

  msOf: function (ts) {
    var n = ts ? Date.parse(ts) : NaN;
    return isFinite(n) ? n : Date.now();
  },

  // Coalesce bursts into at most one read per interval per kind.
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
    if (Tabs.current() === "ops") this.refreshSummary();
  },

  refreshPortfolio: function () {
    var self = this;
    this._lastAt.portfolio = Date.now();
    return ExchangeAPI.getPortfolio()
      .then(function (portfolio) {
        self.portfolio = portfolio;
        if (self.cursor.sessionChanged(portfolio.session_id)) self.onSessionChange(portfolio.session_id);
        else self.bindHistory(portfolio.session_id);
        self.recordEquity(portfolio);
        self.renderCards(portfolio);
        self.renderPositions(portfolio);
        self.renderHeatmap(portfolio);
        self.renderRisk(portfolio);
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
        self.renderOrderTrail();
        self.renderLiveQuotes();
        self.renderFillRate();
        self.renderAnalytics();
      })
      .fail(function () {
        self.markOrdersUnavailable("Unavailable");
      });
  },

  _pnlFailedAt: 0,
  refreshPnl: function () {
    var self = this;
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

  _summaryFailedAt: 0,
  refreshSummary: function () {
    var self = this;
    if (Date.now() - this._summaryFailedAt < CONFIG.MCP_RETRY_MS) {
      if (!this._summaryOk) this.renderSummaryFallback();
      return;
    }
    return MCP.getSessionSummary()
      .then(function (resp) {
        self._summaryOk = true;
        self._summaryFailedAt = 0;
        self.renderSummary(resp);
      })
      .fail(function () {
        self._summaryOk = false;
        self._summaryFailedAt = Date.now();
        self.renderSummaryFallback();
      });
  },

  bindHistory: function (sessionId) {
    if (!sessionId || this.history.key === "lifecycle:" + sessionId) return;
    this.history = new Trades.LifecycleHistory(sessionId, this.storage());
    if (this.orders.length) {
      this.history.observe(this.orders);
      this.renderOrders();
      this.renderFillRate();
    }
  },

  onSessionChange: function (sessionId) {
    var self = this;
    this.history = new Trades.LifecycleHistory(sessionId, this.storage());
    this.orders = [];
    this.pnl = null;
    this.priceBuf = {};
    this.tradeBuf = {};
    this.equity = [];
    this.audit = [];
    this._summaryOk = false;
    this.renderAnnouncement(null);
    this.renderOrders();
    this.renderOrderTrail();
    this.renderLiveQuotes();
    this.renderFillRate();
    this.renderAnalytics();
    this.renderAudit();
    this.markPnlUnavailable("Loading…");
    this.loadInstruments().always(function () {
      if (self.portfolio) self.renderPositions(self.portfolio);
      self.refreshOrders();
      self.refreshPnl();
    });
  },

  recordEquity: function (portfolio) {
    if (portfolio && portfolio.total_value !== undefined && portfolio.total_value !== null) {
      this.equity.push({ t: Date.now(), value: portfolio.total_value });
      if (this.equity.length > this.BUF_CAP) this.equity.shift();
    }
  },

  // --- Overview rendering -------------------------------------------------

  renderCards: function (portfolio) {
    $("#card-total-value .value").text(formatCurrency(portfolio.total_value));
    $("#card-realized-pnl .value").text(formatCurrency(portfolio.realized_pnl));
    $("#card-unrealized-pnl .value").text(formatCurrency(portfolio.unrealized_pnl));
    $(".cards .card .value").removeClass("stale");
  },

  renderPositions: function (portfolio) {
    var symbols = this.symbols(portfolio);
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

  // Heat map: one cell per instrument, colour = total P&L (green up / red
  // down), intensity scaled to the largest absolute P&L in the book. Uses the
  // MCP per-instrument P&L when present, else the exchange positions.
  renderHeatmap: function (portfolio) {
    var by = (this.pnl && this.pnl.data && this.pnl.data.by_instrument) || null;
    var symbols = this.symbols(portfolio);
    var items = symbols.map(function (s) {
      var total;
      if (by && by[s]) total = (by[s].total_pnl || 0) * 100; // dollars -> cents
      else {
        var pos = (portfolio.positions || []).filter(function (p) { return p.symbol === s; })[0] || {};
        total = (pos.realized_pnl || 0) + (pos.unrealized_pnl || 0);
      }
      return { symbol: s, total: total };
    });
    var max = items.reduce(function (m, it) { return Math.max(m, Math.abs(it.total)); }, 0) || 1;
    var html = items.map(function (it) {
      var mag = Math.min(1, Math.abs(it.total) / max);
      var cls = it.total > 0 ? "pos" : it.total < 0 ? "neg" : "flat";
      var alpha = it.total === 0 ? 0.08 : 0.15 + 0.6 * mag;
      var color = it.total > 0 ? "46,160,67" : "248,81,73";
      var bg = it.total === 0 ? "rgba(128,128,128,0.12)" : "rgba(" + color + "," + alpha.toFixed(3) + ")";
      return (
        '<div class="heat-cell ' + cls + '" style="background:' + bg + '">' +
        '<div class="heat-sym">' + escapeHtml(it.symbol) + "</div>" +
        '<div class="heat-val">' + escapeHtml(formatCurrency(it.total)) + "</div>" +
        "</div>"
      );
    }).join("");
    $("#heatmap").html(html || '<div class="empty">No instruments</div>').removeClass("stale");
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
    var rows = this.history.entries().slice().reverse().map(function (e) { // newest first
      var o = byId[e.orderId] || {};
      var remaining = e.remaining !== undefined && e.remaining !== null ? e.remaining : o.remaining_qty;
      return (
        "<tr>" +
        "<td class=\"mono\" title=\"" + escapeHtml(e.orderId) + "\">" + escapeHtml(String(e.orderId || "").slice(0, 8)) + "</td>" +
        "<td class=\"mono\">" + escapeHtml(formatSgt(e.at)) + "</td>" +
        "<td>" + escapeHtml(o.symbol) + "</td>" +
        "<td>" + escapeHtml(String(o.side || "").toUpperCase()) + "</td>" +
        "<td class=\"num\">" + escapeHtml(o.quantity) + "</td>" +
        "<td class=\"num\">" + escapeHtml(formatCurrencyOrDash(o.price)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(remaining === undefined ? "—" : remaining) + "</td>" +
        "<td><span class=\"order-status " + escapeHtml(e.status.toLowerCase()) + "\">" + escapeHtml(e.status) + "</span></td>" +
        "</tr>"
      );
    }).join("");
    var empty = "<tr><td colspan=\"8\" class=\"empty\">No orders observed this session</td></tr>";
    var body = rows || empty;
    var meta = this.history.entries().length + " transitions · " + this.orders.length + " orders";
    $("#lifecycle-body").html(body).removeClass("stale");
    $("#lifecycle-count").text(meta);
    $("#ops-log-body").html(body).removeClass("stale");
    $("#ops-log-count").text(meta);
    $("#export-csv").prop("disabled", this.orders.length === 0);
  },

  // Overview: the bot's live resting quotes — its open buy orders (bids) and
  // open sell orders (asks), read from the exchange. Empty when the window is
  // closed or the bot is flat; that is the honest state, not an error.
  renderLiveQuotes: function () {
    var q = Analytics.liveQuotes(this.orders);
    function rows(list) {
      return list.map(function (o) {
        return (
          "<tr>" +
          "<td class=\"mono\">" + escapeHtml(formatSgt(o.created_at)) + "</td>" +
          "<td>" + escapeHtml(o.symbol) + "</td>" +
          "<td class=\"num\">" + escapeHtml(formatCurrencyOrDash(o.price)) + "</td>" +
          "<td class=\"num\">" + escapeHtml(o.remaining_qty === undefined ? o.quantity : o.remaining_qty) + "</td>" +
          "<td><span class=\"order-status " + escapeHtml(Trades.normalizeStatus(o.status).toLowerCase()) + "\">" + escapeHtml(Trades.normalizeStatus(o.status)) + "</span></td>" +
          "</tr>"
        );
      }).join("");
    }
    $("#bids-body").html(rows(q.bids) || "<tr><td colspan=\"5\" class=\"empty\">No resting bids</td></tr>").removeClass("stale");
    $("#asks-body").html(rows(q.asks) || "<tr><td colspan=\"5\" class=\"empty\">No resting asks</td></tr>").removeClass("stale");
    $("#quotes-count").text(q.bids.length + " bids · " + q.asks.length + " asks resting");
  },

  // Overview: a running trail of the bot's placed orders, labelled by book
  // side — a buy order rests on the bid, a sell order on the ask — so the
  // desk can audit at a glance what the bot has been quoting. Newest first.
  renderOrderTrail: function () {
    var rows = this.orders.slice().sort(function (a, b) {
      var ax = a.created_at || "", bx = b.created_at || "";
      return ax < bx ? 1 : ax > bx ? -1 : 0; // newest first
    }).map(function (o) {
      var side = String(o.side || "").toLowerCase();
      var isBid = side === "buy";
      var filled = o.quantity !== undefined && o.remaining_qty !== undefined
        ? (o.quantity - o.remaining_qty) : undefined;
      return (
        "<tr>" +
        "<td class=\"mono\">" + escapeHtml(formatSgt(o.created_at)) + "</td>" +
        "<td>" + escapeHtml(o.symbol) + "</td>" +
        "<td><span class=\"order-side " + (isBid ? "bid" : "ask") + "\">" + (isBid ? "BID" : "ASK") + "</span></td>" +
        "<td class=\"num\">" + escapeHtml(formatCurrencyOrDash(o.price)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(o.quantity) + "</td>" +
        "<td class=\"num\">" + escapeHtml(filled === undefined ? "—" : filled) + "</td>" +
        "<td><span class=\"order-status " + escapeHtml(Trades.normalizeStatus(o.status).toLowerCase()) + "\">" + escapeHtml(Trades.normalizeStatus(o.status)) + "</span></td>" +
        "</tr>"
      );
    }).join("");
    $("#trail-body").html(rows || "<tr><td colspan=\"7\" class=\"empty\">No orders placed this session</td></tr>").removeClass("stale");
    $("#trail-count").text(this.orders.length + " orders");
  },

  // --- Execution: fill rate + chart ---------------------------------------

  renderFillRate: function () {
    var rows = Analytics.fillRate(this.orders, this.history);
    var html = rows.map(function (r) {
      return (
        "<tr>" +
        "<td>" + escapeHtml(r.symbol) + "</td>" +
        "<td class=\"num\">" + r.sent + "</td>" +
        "<td class=\"num\">" + r.filled + "</td>" +
        "<td class=\"num\">" + r.partial + "</td>" +
        "<td class=\"num\">" + r.rejected + "</td>" +
        "<td class=\"num\">" + (r.fillRate * 100).toFixed(1) + "%</td>" +
        "</tr>"
      );
    }).join("");
    $("#fillrate-body").html(html || "<tr><td colspan=\"6\" class=\"empty\">No orders yet</td></tr>");
  },

  populateChartSymbols: function () {
    var syms = this.instruments;
    if (!syms.length) return;
    if (!this.chartSymbol || syms.indexOf(this.chartSymbol) < 0) this.chartSymbol = syms[0];
    var self = this;
    var opts = syms.map(function (s) {
      return '<option value="' + escapeHtml(s) + '"' + (s === self.chartSymbol ? " selected" : "") + ">" + escapeHtml(s) + "</option>";
    }).join("");
    $("#chart-symbol").html(opts);
  },

  bindChartControls: function () {
    var self = this;
    $("#chart-symbol").on("change", function () { self.chartSymbol = this.value; self.renderChart(); });
    $(".seg-btn").on("click", function () {
      $(".seg-btn").removeClass("active");
      $(this).addClass("active");
      self.chartBucket = Number($(this).data("bucket")) || 60000;
      self.renderChart();
    });
  },

  renderChart: function () {
    var canvas = document.getElementById("price-chart");
    if (!canvas) return;
    var samples = this.priceBuf[this.chartSymbol] || [];
    var trades = this.tradeBuf[this.chartSymbol] || [];
    var candles = Analytics.candles(samples, this.chartBucket);
    var vwap = Analytics.vwapPoints(trades, this.chartBucket);
    Chart.candlestick(canvas, candles, vwap);
    $("#chart-source").html(
      "Symbol <strong>" + escapeHtml(this.chartSymbol || "—") + "</strong> · " +
      candles.length + " bars from " + samples.length + " price samples · VWAP from " + trades.length + " trades · client-side (no OHLCV endpoint)"
    );
  },

  // --- Performance analytics ----------------------------------------------

  renderAnalytics: function () {
    var p = this.portfolio || {};
    var csvRows = Trades.csvRows(this.orders, p.trades || [], p.positions || []);
    var stats = Analytics.performance(csvRows);
    var html = stats.map(function (s) {
      var pf = s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2);
      var sh = s.sharpe === null ? "—" : s.sharpe.toFixed(2);
      var pnlCls = s.totalPnl > 0 ? "pos" : s.totalPnl < 0 ? "neg" : "";
      return (
        "<tr>" +
        "<td>" + escapeHtml(s.symbol) + "</td>" +
        "<td class=\"num\">" + s.trades + "</td>" +
        "<td class=\"num\">" + (s.winRate * 100).toFixed(0) + "%</td>" +
        "<td class=\"num\">" + pf + "</td>" +
        "<td class=\"num\">" + formatDollars(s.avgWin) + "</td>" +
        "<td class=\"num\">" + formatDollars(s.avgLoss) + "</td>" +
        "<td class=\"num\">" + sh + "</td>" +
        "<td class=\"num\" title=\"No benchmark feed from the exchange\">—</td>" +
        "<td class=\"num " + pnlCls + "\">" + formatDollars(s.totalPnl) + "</td>" +
        "</tr>"
      );
    }).join("");
    $("#analytics-body").html(html || "<tr><td colspan=\"9\" class=\"empty\">No closed trades yet this session</td></tr>");
    $("#analytics-source").text("Derived from the session trade log (" + (p.trades || []).length + " fills). Sharpe is per-trade, not annualized.");
  },

  // --- Risk: exposure, drawdown, correlation ------------------------------

  renderRisk: function (portfolio) {
    var exp = Analytics.netExposure(portfolio.positions || []);
    $("#risk-net").text(formatCurrency(exp.net));
    $("#risk-gross").text(formatCurrency(exp.gross));
    var dd = Analytics.drawdown(this.equity);
    $("#risk-maxdd").text(this.equity.length > 1 ? (dd.maxDrawdown * 100).toFixed(2) + "%" : "—");
    $("#risk-curdd").text(this.equity.length > 1 ? (dd.currentDrawdown * 100).toFixed(2) + "%" : "—");
    $(".risk-tiles .card .value").removeClass("stale");
    if (Tabs.current() === "risk") this.renderCorrelation();
  },

  renderCorrelation: function () {
    var syms = this.instruments.length ? this.instruments : Object.keys(this.priceBuf);
    var corr = Analytics.correlation(this.priceBuf, syms);
    if (!corr.symbols.length) {
      $("#corr-matrix").html('<div class="empty">No price history yet</div>');
      return;
    }
    var head = "<tr><th></th>" + corr.symbols.map(function (s) { return "<th>" + escapeHtml(s) + "</th>"; }).join("") + "</tr>";
    var rows = corr.symbols.map(function (s, i) {
      var cells = corr.matrix[i].map(function (r) {
        if (r === null) return '<td class="corr-cell na">—</td>';
        var mag = Math.abs(r);
        var color = r >= 0 ? "46,160,67" : "248,81,73";
        var bg = "rgba(" + color + "," + (0.12 + 0.55 * mag).toFixed(3) + ")";
        return '<td class="corr-cell" style="background:' + bg + '">' + r.toFixed(2) + "</td>";
      }).join("");
      return "<tr><th>" + escapeHtml(s) + "</th>" + cells + "</tr>";
    }).join("");
    $("#corr-matrix").html("<table class=\"corr-table\">" + head + rows + "</table>");
  },

  // --- Ops: session summary + audit log -----------------------------------

  renderSummary: function (resp) {
    var d = (resp && resp.data) || {};
    $("#sum-pnl").text(d.total_pnl !== undefined ? formatDollars(d.total_pnl) : "—");
    $("#sum-trades").text(d.trade_count !== undefined ? d.trade_count : "—");
    $("#sum-best").text(this.instrumentLabel(d.best_instrument));
    $("#sum-worst").text(this.instrumentLabel(d.worst_instrument));
    $("#summary-source").text("Source: " + (resp.source || "MCP get_session_summary") + " · as of " + (resp.as_of || "—"));
    $(".summary-tiles .card .value").removeClass("stale");
  },

  // best/worst come back as either a bare symbol or a { symbol, pnl } object.
  instrumentLabel: function (v) {
    if (!v) return "—";
    if (typeof v === "string") return v;
    if (v.symbol === undefined) return "—";
    return v.pnl !== undefined ? v.symbol + " (" + formatDollars(v.pnl) + ")" : String(v.symbol);
  },

  renderSummaryFallback: function () {
    var p = this.portfolio || {};
    $("#sum-pnl").text(p.total_value !== undefined ? formatCurrency((p.realized_pnl || 0) + (p.unrealized_pnl || 0)) : "—");
    $("#sum-trades").text((p.trades || []).length || "—");
    $("#sum-best").text("—");
    $("#sum-worst").text("—");
    $("#summary-source").text("MCP get_session_summary unavailable — showing exchange figures.");
  },

  logAudit: function (action, detail) {
    this.audit.push({ at: new Date(), action: action, detail: detail || "" });
    this.renderAudit();
  },

  renderAudit: function () {
    var html = this.audit.slice().reverse().map(function (a) {
      return (
        "<tr><td class=\"mono\">" + escapeHtml(formatSgt(a.at)) + "</td>" +
        "<td>" + escapeHtml(a.action) + "</td>" +
        "<td>" + escapeHtml(a.detail) + "</td></tr>"
      );
    }).join("");
    $("#audit-body").html(html || "<tr><td colspan=\"3\" class=\"empty\">No human actions this session</td></tr>");
  },

  // --- Scaffolds (need bot endpoints) -------------------------------------

  renderStrategyScaffold: function () {
    var html = this.instruments.map(function (s) {
      return "<tr><td>" + escapeHtml(s) + "</td><td>—</td><td>—</td><td><span class=\"order-status pending\">PENDING FEED</span></td></tr>";
    }).join("");
    $("#strategy-body").html(html || "<tr><td colspan=\"4\" class=\"empty\">No instruments</td></tr>");
  },

  renderRiskControlsScaffold: function () {
    var html = this.instruments.map(function (s) {
      return (
        "<tr><td>" + escapeHtml(s) + "</td><td>—</td><td>—</td>" +
        "<td><button class=\"btn ghost\" type=\"button\" disabled title=\"Needs a bot pause/resume endpoint\">Pause</button></td></tr>"
      );
    }).join("");
    $("#risk-controls-body").html(html || "<tr><td colspan=\"4\" class=\"empty\">No instruments</td></tr>");
  },

  // --- Manual override: per-instrument buy / sell -------------------------

  // One row per instrument: a live price, a quantity box and Buy/Sell buttons.
  // The bot decides the (marketable) limit price; the desk decides size.
  renderManualControls: function () {
    var html = this.instruments.map(function (s) {
      var es = escapeHtml(s);
      return (
        "<tr data-symbol=\"" + es + "\">" +
        "<td>" + es + "</td>" +
        "<td class=\"num manual-price\" data-symbol=\"" + es + "\">—</td>" +
        "<td><input type=\"number\" class=\"manual-qty\" data-symbol=\"" + es + "\" min=\"1\" step=\"1\" placeholder=\"shares\" /></td>" +
        "<td class=\"manual-actions\">" +
        "<button class=\"btn manual-buy\" type=\"button\" data-symbol=\"" + es + "\">Buy</button> " +
        "<button class=\"btn ghost manual-sell\" type=\"button\" data-symbol=\"" + es + "\">Sell</button>" +
        "</td></tr>"
      );
    }).join("");
    $("#manual-body").html(html || "<tr><td colspan=\"4\" class=\"empty\">No instruments</td></tr>");
  },

  // Keep the live-price column current from the SSE feed (cents -> currency).
  updateManualPrice: function (symbol, priceCents) {
    if (priceCents === undefined || priceCents === null) return;
    $("#manual-body .manual-price[data-symbol='" + symbol + "']").text(formatCurrency(priceCents));
  },

  // Priya's one-click-to-propose, one-to-confirm rule (same as the news
  // params): every manual order is staged and only sent after an explicit
  // Confirm, so an override with no auth still can't fire on a single misclick.
  bindManualControls: function () {
    var self = this;

    function stage(symbol, side) {
      var qty = parseInt($("#manual-body .manual-qty[data-symbol='" + symbol + "']").val(), 10);
      if (!qty || qty <= 0) {
        $("#manual-result").addClass("error").text("Enter a positive quantity for " + symbol + " first.");
        return;
      }
      self._pendingOrder = { symbol: symbol, side: side, quantity: qty };
      $("#manual-result").removeClass("error").text("");
      $("#manual-confirm-text").text(side.toUpperCase() + " " + qty + " " + symbol + " at market now?");
      $("#manual-confirm").show();
    }

    $(document).on("click", ".manual-buy", function () { stage($(this).data("symbol"), "buy"); });
    $(document).on("click", ".manual-sell", function () { stage($(this).data("symbol"), "sell"); });

    $("#manual-confirm-no").on("click", function () {
      self._pendingOrder = null;
      $("#manual-confirm").hide();
    });

    $("#manual-confirm-yes").on("click", function () {
      var order = self._pendingOrder;
      if (!order) return;
      self._pendingOrder = null;
      $("#manual-confirm").hide();
      $("#manual-result").removeClass("error").text("Placing " + order.side + " " + order.quantity + " " + order.symbol + "…");
      BotAPI.submitOrder(order.symbol, order.side, order.quantity)
        .then(function (resp) {
          var msg = order.side.toUpperCase() + " " + resp.quantity + " " + order.symbol +
            " placed" + (resp.filled ? " (filled " + resp.filled + ")" : "") +
            (resp.order_id ? " · " + String(resp.order_id).slice(0, 8) : "");
          $("#manual-result").removeClass("error").text(msg);
          self.logAudit("MANUAL " + order.side.toUpperCase(), resp.quantity + " " + order.symbol +
            (resp.order_id ? " (" + String(resp.order_id).slice(0, 8) + ")" : ""));
          self.requestRefresh("all");
        })
        .fail(function (xhr) {
          var body = xhr && xhr.responseJSON;
          var reason = (body && (body.reason || body.error)) || (xhr && xhr.statusText) || "no response";
          $("#manual-result").addClass("error").text("Order refused: " + reason);
          self.logAudit("MANUAL " + order.side.toUpperCase(), order.quantity + " " + order.symbol + " REFUSED: " + reason);
        });
    });
  },

  // --- P&L (Overview) -----------------------------------------------------

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

  renderPnlFallback: function (label) {
    if (!this.portfolio || !this.portfolio.session_id) return this.markPnlUnavailable(label);
    this.renderPnl(Trades.pnlFromPortfolio(this.portfolio, this.instruments));
  },

  symbols: function (portfolio) {
    return this.instruments.length
      ? this.instruments
      : ((portfolio && portfolio.positions) || []).map(function (p) { return p.symbol; });
  },

  // --- Stale-value clearing -----------------------------------------------

  markUnavailable: function (label) {
    $(".cards .card .value").text(label).addClass("stale");
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
    $("#heatmap").addClass("stale");
    $("#last-updated").addClass("stale");
    this.markOrdersUnavailable(label);
    this.markPnlUnavailable(label);
  },

  markOrdersUnavailable: function (label) {
    $("#count-buy, #count-sell, #count-partial, #count-rejected").text("—");
    $("#ops-strip").addClass("stale");
    $("#lifecycle-body, #ops-log-body, #trail-body, #bids-body, #asks-body").addClass("stale");
    $("#lifecycle-count, #ops-log-count").text(label);
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

  // --- Strategy params: news config with two-step confirm ------------------

  loadNewsConfig: function () {
    var self = this;
    $.getJSON("/bot/news/config").done(function (cfg) {
      self._newsConfig = cfg;
      self.renderNewsConfig(cfg);
      $("#news-settings").show();
    }).fail(function () {
      $("#news-settings").hide();
    });
  },

  renderNewsConfig: function (cfg) {
    $("#news-enabled").prop("checked", cfg.enabled);
    $("#news-cooldown").val(cfg.cooldown_seconds);
    $("#news-ttl").val(cfg.directive_ttl_seconds);
    $("#news-default-mult").val(cfg.default_size_multiplier);
    $("#news-default-mult-val").text(Number(cfg.default_size_multiplier).toFixed(2));

    var $sym = $("#news-symbol-multipliers").empty();
    var mult = cfg.size_multiplier || {};
    Object.keys(mult).sort().forEach(function (symbol) {
      var id = "news-mult-" + symbol;
      var val = mult[symbol];
      $sym.append(
        '<div class="news-field">' +
          '<label for="' + id + '">' + escapeHtml(symbol) + '</label>' +
          '<input type="range" id="' + id + '" data-symbol="' + escapeHtml(symbol) + '"' +
          ' class="news-sym-slider" min="0" max="1.5" step="0.05" value="' + val + '" />' +
          '<span class="range-val" id="' + id + '-val">' + Number(val).toFixed(2) + '</span>' +
        '</div>'
      );
    });
  },

  // Priya's rule: one click to propose, one to confirm. Every change is staged
  // and only sent (and audited) after an explicit Confirm.
  bindNewsControls: function () {
    var self = this;

    function stage(patch, text) {
      self._pendingParam = patch;
      $("#param-confirm-text").text("Apply change: " + text + "?");
      $("#param-confirm").show();
    }

    $("#param-confirm-yes").on("click", function () {
      var patch = self._pendingParam;
      if (!patch) return;
      self._pendingParam = null;
      $("#param-confirm").hide();
      $.ajax({
        url: "/bot/news/config", method: "POST",
        contentType: "application/json", data: JSON.stringify(patch),
      }).done(function (cfg) {
        self._newsConfig = cfg;
        self.logAudit("PARAM CHANGE", JSON.stringify(patch));
      });
    });
    $("#param-confirm-no").on("click", function () {
      self._pendingParam = null;
      $("#param-confirm").hide();
      if (self._newsConfig) self.renderNewsConfig(self._newsConfig); // revert UI
    });

    $("#news-enabled").on("change", function () {
      stage({ enabled: this.checked }, "news strategy " + (this.checked ? "enabled" : "disabled"));
    });
    $("#news-cooldown").on("change", function () {
      stage({ cooldown_seconds: Number(this.value) }, "cooldown = " + this.value + "s");
    });
    $("#news-ttl").on("change", function () {
      stage({ directive_ttl_seconds: Number(this.value) }, "bias TTL = " + this.value + "s");
    });
    $("#news-default-mult").on("input", function () {
      $("#news-default-mult-val").text(Number(this.value).toFixed(2));
    }).on("change", function () {
      stage({ default_size_multiplier: Number(this.value) }, "default multiplier = " + Number(this.value).toFixed(2));
    });
    $(document).on("input", ".news-sym-slider", function () {
      $("#" + this.id + "-val").text(Number(this.value).toFixed(2));
    });
    $(document).on("change", ".news-sym-slider", function () {
      var sym = $(this).data("symbol");
      var patch = { size_multiplier: {} };
      patch.size_multiplier[sym] = Number(this.value);
      stage(patch, sym + " multiplier = " + Number(this.value).toFixed(2));
    });
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
    this.logAudit("CSV EXPORT", rows.length + " rows");
  },
};

$(function () {
  DashboardController.init();
});
