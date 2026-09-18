// Assistant panel logic: request building, tool loop parsing, citations and confidence. No DOM, no network.
const test = require("node:test");
const assert = require("node:assert");
const S = require("../js/assistant.js");

test("TOOLS: declares the four MCP read tools and the two local tools with input schemas", () => {
  const names = S.TOOLS.map((t) => t.name).sort();
  assert.deepStrictEqual(names, ["get_forecast", "get_pnl", "get_portfolio", "get_positions", "get_recent_orders", "get_session_summary"]);
  S.TOOLS.forEach((t) => assert.ok(t.description && t.input_schema && t.input_schema.type === "object", t.name));
  assert.deepStrictEqual(S.MCP_TOOLS, ["get_portfolio", "get_positions", "get_pnl", "get_session_summary"]);
});

test("buildRequest: sends the question with the tools, a capped max_tokens and a guarded system prompt", () => {
  const body = S.buildRequest("Why are we losing money?", []);
  assert.strictEqual(body.model, "sonnet");
  assert.strictEqual(body.max_tokens, 512);
  assert.strictEqual(body.tools, S.TOOLS);
  assert.deepStrictEqual(body.messages, [{ role: "user", content: "Why are we losing money?" }]);
  assert.ok(/never place/i.test(body.system) && /cite/i.test(body.system) && /SGD/.test(body.system));
});

test("buildRequest: keeps prior turns ahead of the new question", () => {
  const prior = [{ role: "user", content: "hi" }, { role: "assistant", content: [{ type: "text", text: "hello" }] }];
  const body = S.buildRequest("and now?", prior);
  assert.strictEqual(body.messages.length, 3);
  assert.deepStrictEqual(body.messages[2], { role: "user", content: "and now?" });
});

test("toolUses: extracts tool_use blocks only when the model stopped for tools", () => {
  const resp = { stop_reason: "tool_use", content: [{ type: "text", text: "Let me check." }, { type: "tool_use", id: "toolu_1", name: "get_pnl", input: {} }] };
  assert.deepStrictEqual(S.toolUses(resp), [{ id: "toolu_1", name: "get_pnl", input: {} }]);
  assert.deepStrictEqual(S.toolUses({ stop_reason: "end_turn", content: [{ type: "text", text: "Done" }] }), []);
});

test("toolResultMessage: wraps each result as a tool_result with JSON content, errors flagged", () => {
  const msg = S.toolResultMessage([
    { id: "toolu_1", ok: true, result: { source: "GET /portfolio", as_of: "x", data: { cash: 1 } } },
    { id: "toolu_2", ok: false, error: "MCP tool get_pnl failed: 502" },
  ]);
  assert.strictEqual(msg.role, "user");
  assert.deepStrictEqual(msg.content[0], { type: "tool_result", tool_use_id: "toolu_1", content: JSON.stringify({ source: "GET /portfolio", as_of: "x", data: { cash: 1 } }) });
  assert.deepStrictEqual(msg.content[1], { type: "tool_result", tool_use_id: "toolu_2", content: "MCP tool get_pnl failed: 502", is_error: true });
});

test("finalText: joins the text blocks; empty when there are none", () => {
  assert.strictEqual(S.finalText({ content: [{ type: "text", text: "A" }, { type: "tool_use" }, { type: "text", text: "B" }] }), "A\nB");
  assert.strictEqual(S.finalText({ content: [] }), "");
  assert.strictEqual(S.finalText(null), "");
});

test("parseAsOf: reads the MCP SGT timestamp into epoch milliseconds", () => {
  assert.strictEqual(S.parseAsOf("2026-09-18 14:32:05.120 SGT"), Date.UTC(2026, 8, 18, 6, 32, 5, 120));
  assert.strictEqual(S.parseAsOf("garbage"), null);
});

test("confidence: high when every citation succeeded with data under 30 s old", () => {
  const now = Date.UTC(2026, 8, 18, 6, 32, 20);
  const c = S.confidence([{ name: "get_pnl", ok: true, as_of: "2026-09-18 14:32:05.120 SGT" }], now);
  assert.deepStrictEqual(c, { label: "high", reason: "1 tool cited, data 15s old" });
});

test("confidence: medium when a citation is older than 30 s", () => {
  const now = Date.UTC(2026, 8, 18, 6, 33, 20);
  const c = S.confidence([{ name: "get_pnl", ok: true, as_of: "2026-09-18 14:32:05.120 SGT" }], now);
  assert.strictEqual(c.label, "medium");
  assert.ok(/75s old/.test(c.reason));
});

test("confidence: low when any tool call failed; unverified when nothing was cited", () => {
  assert.strictEqual(S.confidence([{ name: "get_pnl", ok: false }], 0).label, "low");
  const u = S.confidence([], 0);
  assert.strictEqual(u.label, "unverified");
  assert.ok(/no tool/i.test(u.reason));
});

test("briefingRequest: one-shot on the cheaper model, no tools, figures embedded", () => {
  const body = S.briefingRequest({ summary: { total_pnl: -53563.75 }, pnl: { CRVX: { total_pnl: -59193.8 } }, bot: { state: "STOPPED" }, alerts: [] });
  assert.strictEqual(body.model, "haiku");
  assert.ok(!body.tools);
  assert.ok(body.max_tokens <= 400);
  assert.ok(body.messages[0].content.includes("-53563.75") && body.messages[0].content.includes("CRVX"));
  assert.ok(/30 seconds|executive/i.test(body.system));
});

test("announcementRequest: one-shot haiku, no tools, asks for a JSON verdict across the supply chain", () => {
  const a = { message: "Sanctions announced on freight operators", affected_symbol: "AXON", announcement_id: 7 };
  const instruments = [{ symbol: "NOVA", name: "Nova Energy", sector: "Oil & Gas" }, { symbol: "AXON", name: "Axon Freight", sector: "Logistics & Shipping" }];
  const body = S.announcementRequest(a, instruments);
  assert.strictEqual(body.model, "haiku");
  assert.ok(!body.tools);
  assert.ok(body.max_tokens >= 500 && body.max_tokens <= 800, "enough room for the JSON verdict, still cheap");
  assert.ok(/supply chain/i.test(body.system) && /JSON/.test(body.system) && /advisory|no orders|never place/i.test(body.system));
  const user = body.messages[0].content;
  assert.ok(user.includes("Sanctions announced on freight operators") && user.includes("AXON") && user.includes("Oil & Gas"));
});

test("parseImpact: reads the JSON verdict, tolerating prose and code fences, and normalises directions", () => {
  const text = "Here is the verdict:\n```json\n{\"affected\":[{\"symbol\":\"axon\",\"direction\":\"DOWN\",\"magnitude\":\"high\",\"reason\":\"direct hit\"},{\"symbol\":\"NOVA\",\"direction\":\"bearish\",\"magnitude\":\"low\",\"reason\":\"ripple\"},{\"symbol\":\"ZZZZ\",\"direction\":\"up\"}],\"uncertainty\":\"medium\",\"rationale\":\"freight follows energy\"}\n```";
  const out = S.parseImpact(text, ["NOVA", "AXON", "CRVX"]);
  assert.deepStrictEqual(out.affected, [
    { symbol: "AXON", direction: "down", magnitude: "high", reason: "direct hit" },
    { symbol: "NOVA", direction: "down", magnitude: "low", reason: "ripple" },
  ]);
  assert.strictEqual(out.uncertainty, "medium");
  assert.strictEqual(out.rationale, "freight follows energy");
});

test("parseImpact: garbage returns null", () => {
  assert.strictEqual(S.parseImpact("no json here", ["NOVA"]), null);
  assert.strictEqual(S.parseImpact("", ["NOVA"]), null);
});
