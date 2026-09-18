// Conversational panel logic (M4): request bodies for the LLM service, the
// tool-use loop's message shapes, citations and a rule-derived confidence
// label. Pure and network-free; unit-tested in tests/assistant.test.js.
//
// Responsible-AI rules (Sophia: "citations and confidence, always"): the model
// may only state figures that came from a tool result; the panel shows every
// tool's `source` and `as_of`; confidence is computed here from what the tools
// returned (success, freshness), never asked of the model.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.Assistant = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  var MCP_TOOLS = ["get_portfolio", "get_positions", "get_pnl", "get_session_summary"];

  var TOOLS = [
    {
      name: "get_portfolio",
      description: "The team's portfolio for the current trading window: cash, total value, realised, unrealised and session P&L in SGD. Returns source and as_of.",
      input_schema: { type: "object", properties: {} },
    },
    {
      name: "get_positions",
      description: "Per-instrument position: quantity held, average entry price and unrealised P&L in SGD. Returns source and as_of.",
      input_schema: { type: "object", properties: {} },
    },
    {
      name: "get_pnl",
      description: "Aggregate and per-instrument realised, unrealised and total P&L in SGD. Returns source and as_of.",
      input_schema: { type: "object", properties: {} },
    },
    {
      name: "get_session_summary",
      description: "Session summary: total P&L, trade count, best and worst instrument. Returns source and as_of.",
      input_schema: { type: "object", properties: {} },
    },
    {
      name: "get_forecast",
      description: "The dashboard's short-horizon price forecast built from the live price feed: last price, projected price, 90% band, sample count, method and confidence. A statistical model estimate, not a fact.",
      input_schema: { type: "object", properties: { symbol: { type: "string", description: "Instrument ticker; omit for every instrument" } } },
    },
    {
      name: "get_recent_orders",
      description: "The bot's own recent order events from its journal: submits, fills, cancels and rejections with status codes and SGT timestamps, newest first. Use it to explain what the bot did and why an order failed.",
      input_schema: { type: "object", properties: { symbol: { type: "string", description: "Filter to one instrument" }, limit: { type: "integer", description: "Maximum events, default 20" } } },
    },
  ];

  var SYSTEM = [
    "You are the read-only assistant embedded in GIC's trading dashboard, answering traders and executives about the team's bot.",
    "You never place, cancel or modify orders and never change bot parameters; if asked, say the kill switch and the bot configuration are the only controls.",
    "Answer only from the tools provided. Every figure you state must come from a tool result; cite the tool name and its as_of time in the answer, and never invent or extrapolate numbers.",
    "Money is SGD; tool results are in dollars. Forecasts are model estimates with uncertainty, never facts, and must be described that way.",
    "If a tool fails or the data is missing, say exactly what is unavailable instead of guessing.",
    "Keep answers short and plain, readable at a glance: two to five sentences, no markdown headings.",
  ].join(" ");

  function buildRequest(question, transcript, opts) {
    opts = opts || {};
    return {
      model: opts.model || "sonnet",
      max_tokens: opts.maxTokens || 512,
      system: SYSTEM,
      tools: TOOLS,
      messages: (transcript || []).concat([{ role: "user", content: question }]),
    };
  }

  function toolUses(resp) {
    if (!resp || resp.stop_reason !== "tool_use") return [];
    return (resp.content || [])
      .filter(function (b) { return b.type === "tool_use"; })
      .map(function (b) { return { id: b.id, name: b.name, input: b.input || {} }; });
  }

  function toolResultMessage(results) {
    return {
      role: "user",
      content: (results || []).map(function (r) {
        return r.ok
          ? { type: "tool_result", tool_use_id: r.id, content: JSON.stringify(r.result) }
          : { type: "tool_result", tool_use_id: r.id, content: String(r.error || "tool failed"), is_error: true };
      }),
    };
  }

  function finalText(resp) {
    if (!resp || !resp.content) return "";
    return resp.content.filter(function (b) { return b.type === "text"; }).map(function (b) { return b.text; }).join("\n");
  }

  // "YYYY-MM-DD HH:MM:SS.mmm SGT" (the MCP as_of format) -> epoch ms.
  var SGT_OFFSET_MS = 8 * 60 * 60 * 1000;
  function parseAsOf(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))? SGT$/.exec(String(s || ""));
    if (!m) return null;
    var ms = m[7] ? Number((m[7] + "00").slice(0, 3)) : 0;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms) - SGT_OFFSET_MS;
  }

  var FRESH_MS = 30000;
  function confidence(citations, now) {
    citations = citations || [];
    if (!citations.length) return { label: "unverified", reason: "no tool result cited; treat as the model's opinion" };
    var failed = citations.filter(function (c) { return !c.ok; });
    if (failed.length) {
      return { label: "low", reason: failed.length + " tool call" + (failed.length === 1 ? "" : "s") + " failed: " + failed.map(function (c) { return c.name; }).join(", ") };
    }
    var cited = citations.length + " tool" + (citations.length === 1 ? "" : "s") + " cited";
    var ages = citations.map(function (c) { var t = parseAsOf(c.as_of); return t === null ? null : Math.max(0, now - t); })
      .filter(function (a) { return a !== null; });
    if (!ages.length) return { label: "medium", reason: cited + ", data age unknown" };
    var maxAge = Math.max.apply(null, ages);
    var secs = Math.round(maxAge / 1000);
    return { label: maxAge > FRESH_MS ? "medium" : "high", reason: cited + ", data " + secs + "s old" };
  }

  var BRIEFING_SYSTEM = "You write a 30-second executive briefing for GIC's Deputy COO from the JSON facts supplied. Answer four questions in order: did the bot make money, how much, which instruments drove it, was the system stable (bot state, feed, alerts). Use only the facts given, in SGD; never invent numbers. Plain sentences, at most 90 words, no markdown.";

  function briefingRequest(facts) {
    return {
      model: "haiku",
      max_tokens: 350,
      system: BRIEFING_SYSTEM,
      messages: [{ role: "user", content: "FACTS: " + JSON.stringify(facts || {}) }],
    };
  }

  // Announcement impact (Sophia: "reason across the supply chain, not a
  // keyword match"). One-shot, cheap model, strict JSON, advisory only.
  var ANNOUNCEMENT_SYSTEM = "You are a market analyst for a five-instrument simulated exchange. Given a market announcement and the instrument list with sectors, reason across the supply chain (energy costs drive freight; semiconductors, defense and pharma have their own links) and reply with ONLY a JSON object, no prose: {\"affected\":[{\"symbol\":\"...\",\"direction\":\"up|down|neutral\",\"magnitude\":\"low|medium|high\",\"reason\":\"one short clause\"}],\"uncertainty\":\"low|medium|high\",\"rationale\":\"one sentence\"}. Include indirectly affected instruments with their own reason. Keep it compact: at most 5 entries, each reason under 12 words, rationale under 25 words. This is advisory research only: it never places orders and must not claim certainty.";

  function announcementRequest(a, instruments) {
    a = a || {};
    var list = (instruments || []).map(function (i) { return i.symbol + " (" + (i.name || "") + ", " + (i.sector || "") + ")"; }).join("; ");
    return {
      model: "haiku",
      max_tokens: 600, // a five-entry verdict is ~300 tokens; headroom so the JSON is never cut off
      system: ANNOUNCEMENT_SYSTEM,
      messages: [{ role: "user", content: "ANNOUNCEMENT" + (a.announcement_id !== undefined ? " #" + a.announcement_id : "") + (a.affected_symbol ? " (named ticker: " + a.affected_symbol + ")" : "") + ": " + (a.message || "") + "\nINSTRUMENTS: " + list }],
    };
  }

  var DIR = { up: "up", bullish: "up", positive: "up", higher: "up", down: "down", bearish: "down", negative: "down", lower: "down", neutral: "neutral", flat: "neutral", none: "neutral" };
  function parseImpact(text, symbols) {
    if (!text) return null;
    var m = /\{[\s\S]*\}/.exec(String(text));
    if (!m) return null;
    var obj;
    try { obj = JSON.parse(m[0]); } catch (_) { return null; }
    if (!obj || typeof obj !== "object") return null;
    var known = {};
    (symbols || []).forEach(function (s) { known[String(s).toUpperCase()] = true; });
    var affected = (Array.isArray(obj.affected) ? obj.affected : []).map(function (x) {
      var sym = String(x && x.symbol || "").toUpperCase();
      return {
        symbol: sym,
        direction: DIR[String(x && x.direction || "").toLowerCase()] || "neutral",
        magnitude: String(x && x.magnitude || "low").toLowerCase(),
        reason: String(x && x.reason || ""),
      };
    }).filter(function (x) { return !symbols || known[x.symbol]; });
    return { affected: affected, uncertainty: String(obj.uncertainty || "high").toLowerCase(), rationale: String(obj.rationale || "") };
  }

  return { TOOLS: TOOLS, announcementRequest: announcementRequest, parseImpact: parseImpact, MCP_TOOLS: MCP_TOOLS, SYSTEM: SYSTEM, buildRequest: buildRequest, toolUses: toolUses, toolResultMessage: toolResultMessage, finalText: finalText, parseAsOf: parseAsOf, confidence: confidence, briefingRequest: briefingRequest };
});
