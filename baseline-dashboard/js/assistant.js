// Conversational assistant (M4) — pure, DOM-free logic so it can be unit-tested
// in Node (see tests/assistant.test.js) and loaded as a browser global.
//
// The panel wraps the organiser LLM service (Anthropic Messages shape, proxied
// same-origin via /llm so the API key stays server-side — see server.js) around
// the read-only MCP tools. The model decides which tool answers the question,
// the browser runs it against the MCP server, and the reply is rendered WITH the
// tool's `source` + `as_of` citation and a confidence indicator — the M4
// "no black box" responsible-AI requirement.
//
// This module owns only the request/response *shaping*: it never calls fetch or
// touches the DOM. The async loop and rendering live in dashboard.js.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.Assistant = api; // browser global
  }
})(typeof self !== "undefined" ? self : this, function () {
  // The MCP read tools exposed to the model. Names match the MCP REST facade
  // (POST /tools/{name}); descriptions steer tool choice. All take no input.
  const TOOLS = [
    { name: "get_portfolio", description: "Cash, total account value and realised/unrealised/session P&L for the whole team account.", input_schema: { type: "object", properties: {} } },
    { name: "get_positions", description: "Per-symbol open position: quantity held, average entry price and unrealised P&L. Use for exposure or 'how much of X do we hold' questions.", input_schema: { type: "object", properties: {} } },
    { name: "get_pnl", description: "Profit and loss, aggregate and broken down per instrument. Use for 'how are we doing on X' or winners/losers.", input_schema: { type: "object", properties: {} } },
    { name: "get_session_summary", description: "Session totals: overall P&L, trade count and the best/worst instrument.", input_schema: { type: "object", properties: {} } },
  ];
  const TOOL_NAMES = TOOLS.map(function (t) { return t.name; });

  const SYSTEM =
    "You are the trading desk's read-only analyst assistant. Answer questions about " +
    "the team's live portfolio, positions and P&L using ONLY the provided MCP read " +
    "tools — never invent numbers. Call a tool when a figure is needed. Money is in " +
    "US dollars. Be concise (2-4 sentences), state the figures that matter, and do " +
    "not give trade instructions or place orders. If the tools cannot answer, say so.";

  // Cap the tool-use round-trips so a confused model can't loop forever.
  const MAX_ROUNDS = 4;

  function isToolName(name) {
    return TOOL_NAMES.indexOf(name) !== -1;
  }

  // The first request body sent to POST /llm/v1/messages.
  function buildRequest(question, model, maxTokens) {
    return {
      model: model,
      max_tokens: maxTokens,
      system: SYSTEM,
      tools: TOOLS,
      messages: [{ role: "user", content: String(question == null ? "" : question) }],
    };
  }

  // The tool_use blocks the model is asking us to run (empty when it answered
  // directly). Guards against non-tool or unknown-name blocks.
  function toolUsesOf(message) {
    const content = (message && message.content) || [];
    if (!Array.isArray(content)) return [];
    return content
      .filter(function (b) { return b && b.type === "tool_use"; })
      .map(function (b) { return { id: b.id, name: b.name, input: b.input || {} }; });
  }

  // Concatenate the text blocks of an assistant message into the answer string.
  function textOf(message) {
    const content = (message && message.content) || [];
    if (typeof content === "string") return content;
    if (!Array.isArray(content)) return "";
    return content
      .filter(function (b) { return b && b.type === "text" && typeof b.text === "string"; })
      .map(function (b) { return b.text; })
      .join("")
      .trim();
  }

  // Echo the assistant's turn back verbatim so the follow-up request carries the
  // tool_use it issued (required by the Messages tool protocol).
  function assistantEcho(message) {
    return { role: "assistant", content: (message && message.content) || [] };
  }

  // One tool_result block. `result` is either an MCP response object
  // ({source, as_of, data}) or, on failure, { error: "message" }.
  function toolResultBlock(id, result) {
    if (result && result.error) {
      return { type: "tool_result", tool_use_id: id, is_error: true, content: String(result.error) };
    }
    return { type: "tool_result", tool_use_id: id, content: JSON.stringify(result) };
  }

  // Bundle the run tool results into the next user turn.
  function toolResultMessage(results) {
    return {
      role: "user",
      content: results.map(function (r) { return toolResultBlock(r.id, r.result); }),
    };
  }

  // Dedup the {source, as_of} pairs behind an answer, in call order — these are
  // the citations rendered under the reply.
  function citationsFrom(mcpResponses) {
    const seen = {};
    const out = [];
    (mcpResponses || []).forEach(function (r) {
      if (!r || r.error || !r.source) return;
      const key = r.source + "|" + (r.as_of || "");
      if (seen[key]) return;
      seen[key] = true;
      out.push({ source: r.source, as_of: r.as_of || null });
    });
    return out;
  }

  // Confidence heuristic for the indicator. An answer grounded in at least one
  // successful tool read is "high"; if every tool failed it's "low"; if the
  // model answered with no tool at all it's "medium" (plausible but ungrounded).
  function confidence(mcpResponses) {
    const rs = mcpResponses || [];
    if (rs.length === 0) return "medium";
    const ok = rs.filter(function (r) { return r && !r.error; }).length;
    if (ok === 0) return "low";
    return "high";
  }

  // A short, human-readable summary of one tool result for the audit trail.
  function summarizeResult(name, result) {
    if (result && result.error) return name + " → error: " + result.error;
    if (result && result.source) return name + " → " + result.source + (result.as_of ? " @ " + result.as_of : "");
    return name + " → ok";
  }

  // Token totals across every LLM round of one interaction (for the credit/audit
  // counters). Anthropic usage is per-response; we sum them.
  function sumUsage(usages) {
    return (usages || []).reduce(
      function (acc, u) {
        acc.input_tokens += (u && u.input_tokens) || 0;
        acc.output_tokens += (u && u.output_tokens) || 0;
        return acc;
      },
      { input_tokens: 0, output_tokens: 0 }
    );
  }

  return {
    TOOLS: TOOLS,
    TOOL_NAMES: TOOL_NAMES,
    SYSTEM: SYSTEM,
    MAX_ROUNDS: MAX_ROUNDS,
    isToolName: isToolName,
    buildRequest: buildRequest,
    toolUsesOf: toolUsesOf,
    textOf: textOf,
    assistantEcho: assistantEcho,
    toolResultBlock: toolResultBlock,
    toolResultMessage: toolResultMessage,
    citationsFrom: citationsFrom,
    confidence: confidence,
    summarizeResult: summarizeResult,
    sumUsage: sumUsage,
  };
});
