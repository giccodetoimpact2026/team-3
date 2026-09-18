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
//     price   ──▶ Prices.PriceHistory (no candles endpoint) -> chart, sparklines,
//                 forecast, correlation, instrument-drop alerts (coalesced, ≥1s)
//   Persona views (views.js) hide/show [data-section] panels; the choice is
//   remembered per browser. Panel HTML is built in panels.js from the pure
//   helpers in analytics.js / prices.js / chart.js.
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
  _timers: { portfolio: null, all: null, panels: null },
  _lastAt: { portfolio: 0, all: 0, panels: 0 },
  view: "executive",
  prices: null, // Prices.PriceHistory, session-scoped
  chartSymbol: null,
  forecasts: {}, // symbol -> Prices.forecast() or null
  alerts: [],
  startPrices: {}, // first price seen per symbol this session (instrument-drop alert)
  startingValue: 0, // first total_value seen this session (drawdown alert)
  instrumentInfo: [], // full GET /instruments entries (symbol, name, sector) for the announcement analysis
  announcements: [], // {announcement, status: pending|done|failed, impact, usage, error}, newest first
  _lastEventAt: 0, // any SSE event
  _exchangeOkAt: 0, // last successful GET /portfolio or /orders
  _exchangeError: null, // HTTP status of the last failed exchange read, or null
  _mcpOkAt: 0,
  _prev: {}, // last rendered numeric values, for the change flash
  sessionStatus: null, // last SSE session status: open | halted | closed

  init: function () {
    this.cursor = new FeedCursor();
    this.history = new Trades.LifecycleHistory("", null); // unbound until a session id is known
    this.markUnavailable("Connecting…");
    this.renderTimestamp();
    var self0 = this;
    setInterval(function () { self0.renderTimestamp(); self0.renderHealth(); self0.tickAges(); self0.pollIfQuiet(); }, 1000);
    $(document).on("click", ".btn.why", function () {
      AssistantPanel.explainOrder($(this).attr("data-order"), $(this).attr("data-symbol"));
    });
    $("#export-csv").on("click", this.exportCsv.bind(this));
    $("#kill-switch").on("click", this.haltBot.bind(this));
    this.pollBotStatus();
    setInterval(this.pollBotStatus.bind(this), CONFIG.BOT_STATUS_INTERVAL_MS);

    this.prices = new Prices.PriceHistory(CONFIG.PRICE_HISTORY_CAPACITY);
    this.initViews();
    AssistantPanel.init(this);

    var self = this;
    this.loadInstruments().always(function () {
      self.refreshAll();
      self.connectFeed();
    });
  },

  // --- Persona views ------------------------------------------------------

  initViews: function () {
    var self = this;
    var st = this.storage();
    this.view = Views.resolveView(st && st.getItem("view"));
    Panels.renderViewSwitcher(this.view, function (id) {
      self.view = Panels.applyView(id);
      try { st && st.setItem("view", self.view); } catch (_) {}
      self.renderPanels();
    });
    Panels.applyView(this.view);
  },

  // Everything derived for the persona panels, from state already in memory
  // (no exchange reads). Coalesced because price events arrive several times a
  // second across the five instruments.
  requestPanels: function () {
    var self = this;
    var wait = CONFIG.REFRESH_MIN_INTERVAL_MS - (Date.now() - this._lastAt.panels);
    if (wait <= 0) return this.renderPanels();
    if (this._timers.panels) return;
    this._timers.panels = setTimeout(function () { self._timers.panels = null; self.renderPanels(); }, wait);
  },

  caps: function () {
    var b = this.bot || {};
    var src = (b.limits && b.limits.max_position) || (b.risk && b.risk.max_position) || {};
    return src;
  },

  symbolsForPanels: function () {
    if (this.instruments.length) return this.instruments;
    return this.prices ? this.prices.symbols() : [];
  },

  forecastFor: function (symbol) {
    return this.forecasts[symbol] || null;
  },

  // Facts for the executive briefing (assistant-panel.js adds the MCP session
  // summary itself). Money converted to SGD dollars for the model.
  briefingFacts: function () {
    var p = this.portfolio || {};
    var by = this.pnl && this.pnl.data ? this.pnl.data.by_instrument : Trades.pnlFromPortfolio(p, this.instruments).data.by_instrument;
    return {
      portfolio_sgd: { total_value: (p.total_value || 0) / 100, realized_pnl: (p.realized_pnl || 0) / 100, unrealized_pnl: (p.unrealized_pnl || 0) / 100, fills_this_window: (p.trades || []).length },
      pnl_by_instrument_sgd: by,
      bot: this.bot ? { state: this.bot.state, feed: this.bot.feed_state, heartbeat_age_s: this.bot.heartbeat_age_s, feed_reconnects: this.bot.feed_reconnects, paused: !!(this.bot.orders && this.bot.orders.paused) } : "offline (no status)",
      alerts: this.alerts,
      order_counts: Trades.counters(this.orders, this.history),
    };
  },

  renderPanels: function () {
    this._lastAt.panels = Date.now();
    var symbols = this.symbolsForPanels();
    var p = this.portfolio;
    var self = this;
    if (!symbols.length) return;

    // Forecasts (all symbols; the chart, the analyst cards and the assistant share them).
    this.forecasts = {};
    var lastPrices = {};
    symbols.forEach(function (s) {
      var samples = self.prices.samples(s);
      self.forecasts[s] = Prices.forecast(samples, { minSamples: CONFIG.FORECAST_MIN_SAMPLES, horizonMs: CONFIG.FORECAST_HORIZON_MS });
      var last = self.prices.last(s);
      if (last) lastPrices[s] = last.p;
    });

    // Alerts feed the Risk view, the summary and the briefing.
    var caps = this.caps();
    this.alerts = Analytics.evaluateAlerts({
      portfolio: p, startingValue: this.startingValue, caps: caps,
      lastPrices: lastPrices, startPrices: this.startPrices, bot: this.bot, thresholds: CONFIG.ALERT_THRESHOLDS,
    });
    var crit = this.alerts.filter(function (a) { return a.level === "critical"; }).length;
    Panels.renderAlerts(this.alerts, this.alerts.length ? this.alerts.length + " active · " + crit + " critical" : "all clear · drawdown limit " + CONFIG.ALERT_THRESHOLDS.drawdownPct + "% of starting capital");

    var by = this.pnl && this.pnl.data ? this.pnl.data.by_instrument : (p && p.session_id ? Trades.pnlFromPortfolio(p, symbols).data.by_instrument : {});
    Panels.renderHeatmap(Analytics.heatTiles(by, symbols), this.pnl ? "Source: " + this.pnl.source + " · as of " + this.pnl.as_of : (p && p.session_id ? "Source: GET /portfolio (MCP unavailable)" : "Waiting for data"));

    if (!this.chartSymbol || symbols.indexOf(this.chartSymbol) === -1) this.chartSymbol = symbols[0];
    Panels.renderChartTabs(symbols, this.chartSymbol, function (s) { self.chartSymbol = s; self.renderPanels(); });
    // Only fills inside the chart's time window are drawn: the price buffer
    // starts when the page opened, and fills before that would land off-axis.
    var chartSamples = this.prices.samples(this.chartSymbol);
    var windowStart = chartSamples.length ? chartSamples[0].t : Infinity;
    var strategyOf = this.history.strategies ? this.history.strategies() : {};
    var fills = ((p && p.trades) || []).filter(function (t) { return t.symbol === self.chartSymbol; }).map(function (t) {
      return { t: Date.parse(t.timestamp), p: t.price, side: t.side, quantity: t.quantity, strategy: strategyOf[t.order_id] };
    }).filter(function (f) { return f.t >= windowStart; });
    // Resting orders (what the bot is working right now) are drawn on the chart
    // as dashed limit lines and listed in the Open Orders panel.
    var open = Analytics.openOrderRows ? Analytics.openOrderRows(this.orders, Date.now()) : [];
    open.forEach(function (o) { o.strategy = strategyOf[o.id]; });
    var resting = open.filter(function (o) { return o.symbol === self.chartSymbol; }).map(function (o) { return { side: o.side.toLowerCase(), price: o.price, remaining: o.remaining }; });
    var silence = Analytics.feedSilenceNote ? Analytics.feedSilenceNote({ lastEventAt: this._lastEventAt, sessionStatus: this.sessionStatus }, Date.now()) : null;
    Panels.renderChart(this.chartSymbol, chartSamples, fills, this.forecasts[this.chartSymbol], resting, silence);
    if (Panels.renderOpenOrders) Panels.renderOpenOrders(open);

    Panels.renderFillRate(Analytics.fillRateRows(this.orders, this.history, symbols));
    Panels.renderExposure(Analytics.exposureRows(p, caps, symbols), Analytics.netExposure(p), Object.keys(caps).length > 0);
    Panels.renderCorrelation(Prices.correlationMatrix(this.prices, symbols, { bucketMs: CONFIG.CORRELATION_BUCKET_MS, buckets: CONFIG.CORRELATION_BUCKETS }));
    Panels.renderForecast(symbols.map(function (s) { return { symbol: s, forecast: self.forecasts[s], samples: self.prices.samples(s).length }; }));
    Panels.renderPerformance(Analytics.performanceRows((p && p.trades) || [], (p && p.positions) || [], symbols));

    var v = botStatusView(this.bot);
    Panels.renderSummary(Analytics.sessionSummary({ portfolio: p, pnlByInstrument: by, alerts: this.alerts }), p && p.session_id,
      v.label.replace(/^BOT /, "").toLowerCase() + (this.bot && typeof this.bot.feed_reconnects === "number" ? ", " + this.bot.feed_reconnects + " feed reconnects" : ""));
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
        self.requestPanels();
      })
      .fail(function () {
        self.bot = null;
        self.renderBotStatus(null);
        self.requestPanels();
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
        self.instrumentInfo = list || [];
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
      self._lastEventAt = Date.now();
      if (!self.cursor.accept(e.lastEventId)) return; // replayed
      var d = self.parse(e.data);
      if (d && d.symbol && d.last_price > 0) {
        var t = d.timestamp ? Date.parse(d.timestamp) : Date.now();
        self.prices.push(d.symbol, d.last_price, isNaN(t) ? Date.now() : t);
        if (!self.startPrices[d.symbol]) self.startPrices[d.symbol] = d.last_price;
        self.requestPanels();
      }
      self.requestRefresh("portfolio");
    });
    es.addEventListener("trade", function (e) {
      // Market-wide; it does not say whose fill it was. When the bot reports
      // nothing resting and nothing unknown, none of our orders can have
      // changed, so only the portfolio (marks) is re-read; otherwise treat it
      // as a signal that our orders/fills may have changed and re-read them.
      self._lastEventAt = Date.now();
      if (!self.cursor.accept(e.lastEventId)) return;
      var o = self.bot && self.bot.orders;
      var quiet = o && o.active_orders === 0 && o.unknown_orders === 0;
      self.requestRefresh(quiet ? "portfolio" : "all");
    });
    es.addEventListener("session", function (e) {
      self._lastEventAt = Date.now();
      if (!self.cursor.accept(e.lastEventId)) return;
      var payload = self.parse(e.data);
      if (payload) self.renderSession(payload.status);
      self.requestRefresh("all");
    });
    es.addEventListener("announcement", function (e) {
      self._lastEventAt = Date.now();
      if (!self.cursor.accept(e.lastEventId)) return;
      var payload = self.parse(e.data);
      if (payload) { self.renderAnnouncement(payload); self.onAnnouncement(payload); }
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
        self._exchangeOkAt = Date.now();
        self._exchangeError = null;
        self.rememberStartingValue(portfolio);
        self.renderCards(portfolio);
        self.renderPositions(portfolio);
        if (!self.pnl) self.renderPnlFallback("MCP unavailable"); // keep per-instrument P&L live from the exchange
        self.requestPanels();
        self.setConnectivity(true);
        $("#last-updated").removeClass("stale");
      })
      .fail(function (xhr) {
        self._exchangeError = (xhr && xhr.status) || "network";
        if (xhr && xhr.status === 429 && self.portfolio) {
          // Rate limited, not disconnected: keep the last good figures, but
          // mark them stale with their age rather than wiping the screen.
          $(".card .value, #positions-body").addClass("stale");
          $("#last-updated").addClass("stale").text("Rate limited (429): showing data from " + formatSgt(new Date(self._exchangeOkAt)));
          return;
        }
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
        self._exchangeOkAt = Date.now();
        self.renderOrders();
        self.requestPanels();
      })
      .fail(function (xhr) {
        self._exchangeError = (xhr && xhr.status) || "network";
        if (xhr && xhr.status === 429 && self.orders.length) {
          $("#lifecycle-body, #ops-strip").addClass("stale");
          $("#lifecycle-count").html("<span class=\"stale-note\">rate limited (429) · showing " + self.history.entries().length + " transitions from " + escapeHtml(formatSgt(new Date(self._exchangeOkAt))) + "</span>");
          return;
        }
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
        self._mcpOkAt = Date.now();
        self.renderPnl(resp);
        self.requestPanels();
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
    this.prices.reset();
    this.announcements = [];
    Panels.renderAnnouncements([]);
    this.startPrices = {};
    this.startingValue = 0;
    this.forecasts = {};
    this.renderAnnouncement(null);
    this.renderOrders();
    this.markPnlUnavailable("Loading…");
    this.loadInstruments().always(function () {
      if (self.portfolio) self.renderPositions(self.portfolio);
      self.refreshOrders();
      self.refreshPnl();
    });
  },

  // Starting capital for the drawdown alert (Priya: 15% of starting capital):
  // the first total value seen in this window, remembered per window so a
  // page reload mid-session does not reset the baseline.
  rememberStartingValue: function (portfolio) {
    if (!portfolio || !portfolio.session_id || !(portfolio.total_value > 0)) return;
    var key = "start:" + portfolio.session_id;
    var st = this.storage();
    var saved = 0;
    try { saved = Number(st && st.getItem(key)) || 0; } catch (_) {}
    if (!saved) {
      saved = portfolio.total_value;
      try { st && st.setItem(key, String(saved)); } catch (_) {}
    }
    this.startingValue = saved;
  },

  // --- Rendering -----------------------------------------------------------

  // Write a money value and flash the cell green or red when it moved since
  // the last render, so a live screen is visibly live.
  setLive: function (selector, key, cents) {
    var $el = $(selector);
    var prev = this._prev[key];
    $el.text(formatCurrency(cents));
    if (typeof prev === "number" && typeof cents === "number" && prev !== cents) {
      var cls = cents > prev ? "flash-up" : "flash-down";
      $el.removeClass("flash-up flash-down");
      void $el[0].offsetWidth; // restart the animation
      $el.addClass(cls);
    }
    this._prev[key] = cents;
  },

  renderCards: function (portfolio) {
    var pnl = (portfolio.realized_pnl || 0) + (portfolio.unrealized_pnl || 0);
    this.setLive("#card-total-pnl .value", "pnl", pnl);
    $("#card-total-pnl .value").toggleClass("pos", pnl > 0).toggleClass("neg", pnl < 0);
    this.setLive("#card-total-value .value", "total", portfolio.total_value);
    this.setLive("#card-realized-pnl .value", "realized", portfolio.realized_pnl);
    this.setLive("#card-unrealized-pnl .value", "unrealized", portfolio.unrealized_pnl);
    $(".card .value").removeClass("stale");
  },

  // System health strip: bot, feed, exchange reads, MCP, LLM. Re-rendered
  // every second so the ages tick, from state already in memory.
  renderHealth: function () {
    if (typeof Analytics === "undefined" || !Analytics.healthItems) return;
    var items = Analytics.healthItems({
      bot: this.bot === undefined ? undefined : this.bot,
      feedOnline: this._feedOnline,
      lastEventAt: this._lastEventAt,
      exchangeOkAt: this._exchangeOkAt,
      exchangeError: this._exchangeError,
      mcpOkAt: this._mcpOkAt,
      mcpFailedAt: this._pnlFailedAt,
      // AssistantPanel is a top-level const, so it is not a window property.
      llm: typeof AssistantPanel !== "undefined" && AssistantPanel.llmHealth ? AssistantPanel.llmHealth(Date.now()) : undefined,
    }, Date.now());
    Panels.renderHealth(items);
  },

  // Announcement impact: analyse each new announcement once, keep the last few.
  onAnnouncement: function (a) {
    var self = this;
    var id = a.announcement_id !== undefined ? String(a.announcement_id) : (a.timestamp || "") + "|" + (a.message || "");
    if (this.announcements.some(function (e) { return e.id === id; })) return;
    var entry = { id: id, announcement: a, status: "pending" };
    this.announcements.unshift(entry);
    this.announcements = this.announcements.slice(0, CONFIG.ANNOUNCEMENTS_KEPT);
    Panels.renderAnnouncements(this.announcements);
    AssistantPanel.analyseAnnouncement(a, this.instrumentInfo, this.instruments).then(function (r) {
      entry.status = "done"; entry.impact = r.impact; entry.usage = r.usage;
      Panels.renderAnnouncements(self.announcements);
    }, function (err) {
      entry.status = "failed"; entry.error = err && (err.message || err.code) || "LLM error";
      Panels.renderAnnouncements(self.announcements);
    });
  },

  renderPositions: function (portfolio) {
    var symbols = this.instruments.length
      ? this.instruments
      : (portfolio.positions || []).map(function (p) { return p.symbol; });
    var self = this;
    var now = Date.now();
    var raw = {};
    (portfolio.positions || []).forEach(function (p) { raw[p.symbol] = p.unrealized_pnl; });
    var rows = positionRows(symbols, portfolio)
      .map(function (r) {
        var last = self.prices ? self.prices.last(r.symbol) : null;
        var ageS = last ? Math.round((now - last.t) / 1000) : null;
        var ageHtml = ageS === null ? "" : "<small class=\"age" + (ageS > 20 ? " old" : "") + "\" title=\"seconds since the last price event for this instrument\">" + ageS + "s</small>";
        var prevU = self._prev["pos:" + r.symbol], curU = raw[r.symbol];
        var flash = typeof prevU === "number" && typeof curU === "number" && prevU !== curU ? (curU > prevU ? " flash-up" : " flash-down") : "";
        self._prev["pos:" + r.symbol] = curU;
        return (
          "<tr data-symbol=\"" + escapeHtml(r.symbol) + "\">" +
          "<td>" + escapeHtml(r.symbol) + ageHtml + "</td>" +
          "<td>" + escapeHtml(r.quantity) + "</td>" +
          "<td>" + escapeHtml(r.avg_cost) + "</td>" +
          "<td>" + escapeHtml(r.market_value) + "</td>" +
          "<td class=\"" + flash.trim() + "\">" + escapeHtml(r.unrealized_pnl) + "</td>" +
          Panels.sparklineCell(self.prices ? self.prices.samples(r.symbol) : []) +
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
    var strategyOf = this.history.strategies ? this.history.strategies() : {};
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
        "<td>" + (strategyOf[e.orderId] ? "<span class=\"strategy\">" + escapeHtml(strategyOf[e.orderId]) + "</span>" : "—") + "</td>" +
        "<td class=\"num\">" + escapeHtml(o.quantity) + "</td>" +
        "<td class=\"num\">" + escapeHtml(formatCurrencyOrDash(o.price)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(remaining === undefined || remaining === null || o.quantity === undefined ? "—" : Math.max(0, o.quantity - remaining)) + "</td>" +
        "<td class=\"num\">" + escapeHtml(remaining === undefined || remaining === null ? "—" : remaining) + "</td>" +
        "<td title=\"" + escapeHtml(title) + "\"><span class=\"order-status " + escapeHtml(e.status.toLowerCase()) + "\">" + escapeHtml(e.status) + (e.code ? " <small>" + escapeHtml(e.code) + "</small>" : "") + "</span></td>" +
        "<td><button type=\"button\" class=\"btn why\" data-order=\"" + escapeHtml(e.orderId) + "\" data-symbol=\"" + escapeHtml(o.symbol) + "\" title=\"Ask the assistant to explain this order from the bot journal\">Why?</button></td>" +
        "</tr>"
      );
    }).join("");
    $("#lifecycle-body").html(rows || "<tr><td colspan=\"11\" class=\"empty\">No orders observed this session</td></tr>").removeClass("stale");
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
          for (var i = 0; i < 5; i++) cells += "<td>" + escapeHtml(label) + "</td>";
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

  // Ages shown next to each position tick every second even when nothing
  // arrives, so a frozen "5s" can never pass as live.
  tickAges: function () {
    var self = this, now = Date.now();
    $("#positions-body tr[data-symbol]").each(function () {
      var last = self.prices ? self.prices.last($(this).attr("data-symbol")) : null;
      var $age = $(this).find(".age");
      if (!last || !$age.length) return;
      var s = Math.round((now - last.t) / 1000);
      $age.text(s >= 60 ? Math.floor(s / 60) + "m" : s + "s").toggleClass("old", s > 20);
    });
  },

  // Between windows the feed is silent and nothing triggers a read; one slow
  // portfolio read keeps the figures and the health strip honest.
  pollIfQuiet: function () {
    if (!Analytics.quietFeedPollDue) return;
    var due = Analytics.quietFeedPollDue({ feedOnline: this._feedOnline, lastEventAt: this._lastEventAt, lastReadAt: this._lastAt.portfolio }, Date.now(), { silenceMs: 20000, pollMs: CONFIG.QUIET_FEED_POLL_MS || 30000 });
    if (due) { this.refreshPortfolio(); this.requestPanels(); }
  },

  renderSession: function (status) {
    this.sessionStatus = status || null;
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
    this._feedOnline = !!online;
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
