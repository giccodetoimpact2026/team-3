// The conversational panel (M4) and the executive briefing: the only two
// places the dashboard talks to the LLM service, through the /llm proxy in
// server.js. Message shapes, citations and the confidence rule live in
// assistant.js (pure, tested); this file does the network and the DOM.
//
// Loop: question -> LLM (with tools) -> for each tool_use run the tool (MCP
// read tools over REST, or the two local tools) -> send tool_result -> repeat
// until the model answers in text, or CONFIG.LLM_MAX_TOOL_ROUNDS is reached.
const AssistantPanel = {
  ctx: null, // DashboardController (for the forecast and the order journal)
  transcript: [], // prior text turns, kept short to protect the credit budget
  busy: false,

  SUGGESTIONS: [
    "Are we making or losing money, and where?",
    "Which instrument is our biggest risk right now?",
    "What did the bot do on CRVX recently and why did orders fail?",
    "What is the 5-minute outlook for NOVA?",
  ],

  init: function (ctx) {
    var self = this;
    this.ctx = ctx;
    var $chips = $("#assistant-chips").empty();
    this.SUGGESTIONS.forEach(function (q) {
      $chips.append($("<button type=\"button\"></button>").text(q).on("click", function () { self.ask(q); }));
    });
    $("#assistant-form").on("submit", function (e) {
      e.preventDefault();
      var q = $.trim($("#assistant-input").val());
      if (q) self.ask(q);
    });
    $("#assistant-send").prop("disabled", false);
    $("#briefing-btn").prop("disabled", false).on("click", function () { self.brief(); });
  },

  llm: function (body) {
    return new Promise(function (resolve, reject) {
      $.ajax({
        url: CONFIG.LLM_BASE_URL + "/v1/messages",
        method: "POST",
        contentType: "application/json",
        dataType: "json",
        data: JSON.stringify(body),
        timeout: 60000,
      }).then(resolve, function (xhr) {
        var err = xhr && xhr.responseJSON && xhr.responseJSON.error;
        var code = (err && err.code) || (xhr && xhr.status) || "network";
        var msg = (err && err.message) || (xhr && xhr.statusText) || "no response";
        reject({ status: xhr && xhr.status, code: code, message: msg, retryAfter: xhr && xhr.getResponseHeader && xhr.getResponseHeader("retry-after") });
      });
    });
  },

  // One retry on 429 (every team fires at the same moments), with jitter.
  llmWithRetry: function (body) {
    var self = this;
    return this.llm(body).catch(function (e) {
      if (e.status !== 429) throw e;
      var wait = (Number(e.retryAfter) || 3) * 1000 + Math.random() * 1000;
      return new Promise(function (r) { setTimeout(r, wait); }).then(function () { return self.llm(body); });
    });
  },

  describeError: function (e) {
    if (e.status === 402 || e.code === "INSUFFICIENT_CREDIT") return "The team's LLM credit is exhausted (402). Ask an organiser for a top-up; every other panel keeps working without the assistant.";
    if (e.status === 429) return "The LLM service is rate-limiting us (429). Try again in a few seconds.";
    if (e.code === "LLM_UNREACHABLE" || e.status === 502) return "The LLM service is unreachable from the dashboard server (" + e.message + ").";
    return "LLM request failed: " + e.code + " " + e.message;
  },

  // --- tools ----------------------------------------------------------------

  runTool: function (name, input) {
    var self = this;
    input = input || {};
    var now = new Date();
    function local(source, data) {
      return Promise.resolve({ source: source, as_of: formatSgt(now), data: data });
    }
    var p;
    if (Assistant.MCP_TOOLS.indexOf(name) !== -1) {
      p = new Promise(function (resolve, reject) {
        MCP.callTool(name, {}).then(resolve, function (xhr) {
          reject(new Error("MCP tool " + name + " failed: " + ((xhr && xhr.status) || "unreachable") + " (is the MCP server running on " + CONFIG.MCP_BASE_URL + "?)"));
        });
      });
    } else if (name === "get_forecast") {
      var symbols = input.symbol ? [String(input.symbol).toUpperCase()] : self.ctx.instruments;
      var out = {};
      symbols.forEach(function (s) {
        var f = self.ctx.forecastFor ? self.ctx.forecastFor(s) : Prices.forecast(self.ctx.prices.samples(s), { minSamples: CONFIG.FORECAST_MIN_SAMPLES, horizonMs: CONFIG.FORECAST_HORIZON_MS });
        out[s] = f ? {
          last: f.last / 100, projected: f.projected / 100, band_90_low: f.lower / 100, band_90_high: f.upper / 100,
          horizon_minutes: f.horizonMs / 60000, samples: f.samples, confidence: f.confidence, method: f.method,
          note: "statistical model estimate, not a fact",
        } : { unavailable: "fewer than " + CONFIG.FORECAST_MIN_SAMPLES + " price samples yet" };
      });
      p = local("dashboard price buffer (SSE price events), forecast in SGD", out);
    } else if (name === "get_recent_orders") {
      var limit = Math.min(Number(input.limit) || 20, 50);
      var sym = input.symbol ? String(input.symbol).toUpperCase() : null;
      var events = self.ctx.history.entries().slice().reverse().filter(function (e) { return !sym || e.symbol === sym; }).slice(0, limit)
        .map(function (e) {
          return { order_id: String(e.orderId).slice(0, 8), at_sgt: formatSgt(e.at), symbol: e.symbol, side: e.side, quantity: e.quantity, limit_price: e.price === undefined ? null : e.price / 100, status: e.status, code: e.code || null, reported_by: e.source || "exchange" };
        });
      p = local("bot order journal (GET /bot/orders) and GET /orders, prices in SGD", { count: events.length, events: events, note: events.length ? undefined : "no order events observed this session" });
    } else {
      p = Promise.reject(new Error("unknown tool " + name));
    }
    return p.then(
      function (result) { return { name: name, ok: true, result: result, source: result && result.source, as_of: result && result.as_of }; },
      function (err) { return { name: name, ok: false, error: err.message || String(err) }; }
    );
  },

  // --- the question loop ----------------------------------------------------

  ask: function (question) {
    var self = this;
    if (this.busy) return;
    this.busy = true;
    $("#assistant-send").prop("disabled", true);
    $("#assistant-input").val("");
    var $log = $("#assistant-log");
    $log.append($("<div class=\"msg user\"></div>").text(question));
    var $thinking = $("<div class=\"msg assistant thinking\"></div>").text("Reading the tools…").appendTo($log);

    var body = Assistant.buildRequest(question, this.transcript, { model: CONFIG.LLM_MODEL, maxTokens: CONFIG.LLM_MAX_TOKENS });
    var citations = [];
    var rounds = 0;

    function step(resp) {
      var uses = Assistant.toolUses(resp);
      if (!uses.length || rounds >= CONFIG.LLM_MAX_TOOL_ROUNDS) return resp;
      rounds++;
      $thinking.text("Calling " + uses.map(function (u) { return u.name; }).join(", ") + "…");
      return Promise.all(uses.map(function (u) { return self.runTool(u.name, u.input); })).then(function (results) {
        results.forEach(function (r, i) { r.id = uses[i].id; citations.push(r); });
        body.messages.push({ role: "assistant", content: resp.content });
        body.messages.push(Assistant.toolResultMessage(results));
        return self.llmWithRetry(body).then(step);
      });
    }

    this.llmWithRetry(body).then(step).then(function (resp) {
      var text = Assistant.finalText(resp) || (Assistant.toolUses(resp).length ? "The assistant ran out of tool rounds before answering." : "(no answer)");
      var conf = Assistant.confidence(citations, Date.now());
      self.transcript.push({ role: "user", content: question }, { role: "assistant", content: text });
      if (self.transcript.length > 6) self.transcript.splice(0, self.transcript.length - 6);
      self.renderAnswer($thinking, text, citations, conf, resp.usage);
    }).catch(function (e) {
      $thinking.removeClass("thinking").addClass("error").text(self.describeError(e));
    }).then(function () {
      self.busy = false;
      $("#assistant-send").prop("disabled", false);
    });
  },

  renderAnswer: function ($el, text, citations, conf, usage) {
    $el.removeClass("thinking").text(text);
    var $meta = $("<div class=\"meta\"></div>");
    $meta.append($("<span class=\"conf " + escapeHtml(conf.label) + "\"></span>").text("confidence " + conf.label).attr("title", conf.reason));
    $meta.append($("<span></span>").text(conf.reason + "."));
    citations.forEach(function (c) {
      var label = c.ok ? c.name + " ← " + (c.source || "?") + (c.as_of ? " @ " + c.as_of : "") : c.name + " failed";
      $meta.append($("<span class=\"cite" + (c.ok ? "" : " failed") + "\"></span>").text(label).attr("title", c.ok ? "" : c.error));
    });
    if (usage) $meta.append($("<span></span>").text(CONFIG.LLM_MODEL + " · " + (usage.input_tokens || 0) + " in / " + (usage.output_tokens || 0) + " out tokens"));
    $el.append($meta);
  },

  // --- executive briefing ---------------------------------------------------

  // Facts for the briefing, in dollars, plus what the bot reports about itself.
  facts: function () {
    var ctx = this.ctx;
    var p = ctx.portfolio || {};
    var b = ctx.bot;
    var pnl = ctx.pnl && ctx.pnl.data ? ctx.pnl.data.by_instrument : (p.session_id ? Trades.pnlFromPortfolio(p, ctx.instruments).data.by_instrument : {});
    return {
      currency: "SGD",
      window_id: p.session_id || null,
      total_value: (p.total_value || 0) / 100,
      starting_value: ctx.startingValue ? ctx.startingValue / 100 : null,
      realized_pnl: (p.realized_pnl || 0) / 100,
      unrealized_pnl: (p.unrealized_pnl || 0) / 100,
      pnl_by_instrument: pnl,
      positions: (p.positions || []).map(function (x) { return { symbol: x.symbol, quantity: x.quantity, avg_cost: (x.avg_cost || 0) / 100, unrealized_pnl: (x.unrealized_pnl || 0) / 100 }; }),
      orders: Trades.counters(ctx.orders, ctx.history),
      bot: b ? { state: b.state, feed_state: b.feed_state, heartbeat_age_s: b.heartbeat_age_s, paused: !!(b.orders && b.orders.paused), resting_orders: b.orders && b.orders.active_orders, feed_reconnects: b.feed_reconnects } : "offline (no status from the control endpoint)",
      alerts: ctx.alerts,
    };
  },

  brief: function () {
    var self = this;
    var ctx = this.ctx;
    var $body = $("#briefing-body").removeClass("error").addClass("loading").text("Writing the briefing…");
    $("#briefing-btn").prop("disabled", true);
    var facts = ctx.briefingFacts ? ctx.briefingFacts() : this.facts();
    var summaryCall = new Promise(function (resolve) {
      MCP.callTool("get_session_summary", {}).then(function (r) { resolve(r); }, function () { resolve(null); });
    });
    summaryCall.then(function (summary) {
      facts.mcp_session_summary = summary ? summary.data : "unavailable (MCP server down)";
      facts.sources = ["MCP get_session_summary" + (summary ? " @ " + summary.as_of : " (unavailable)"), "MCP get_pnl / GET /portfolio", "GET /bot/status", "dashboard alert rules"];
      return self.llmWithRetry(Assistant.briefingRequest(facts));
    }).then(function (resp) {
      $body.removeClass("loading").text((Assistant.finalText(resp) || "(empty briefing)").replace(/\*\*|^#+\s*/gm, "").trim()); // plain text only: the model sometimes adds markdown despite the prompt
      $("#briefing-source").text("Written by the LLM service (haiku) from: " + facts.sources.join("; ") + ". Generated " + formatSgt(new Date()) + ". Figures are the tools' figures; the wording is the model's.");
    }).catch(function (e) {
      $body.removeClass("loading").addClass("error").text(self.describeError(e));
    }).then(function () {
      $("#briefing-btn").prop("disabled", false);
    });
  },
};
