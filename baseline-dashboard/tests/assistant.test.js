const test = require("node:test");
const assert = require("node:assert");
const A = require("../js/assistant.js");

test("buildRequest carries model, cap, system and tools with the question", () => {
  const req = A.buildRequest("How's my P&L?", "haiku", 256);
  assert.equal(req.model, "haiku");
  assert.equal(req.max_tokens, 256);
  assert.equal(req.system, A.SYSTEM);
  assert.deepEqual(req.tools, A.TOOLS);
  assert.deepEqual(req.messages, [{ role: "user", content: "How's my P&L?" }]);
});

test("buildRequest coerces a null question to an empty string", () => {
  const req = A.buildRequest(null, "sonnet", 100);
  assert.equal(req.messages[0].content, "");
});

test("toolUsesOf extracts only tool_use blocks", () => {
  const msg = { content: [
    { type: "text", text: "let me check" },
    { type: "tool_use", id: "t1", name: "get_pnl", input: {} },
    { type: "tool_use", id: "t2", name: "get_positions" },
  ] };
  assert.deepEqual(A.toolUsesOf(msg), [
    { id: "t1", name: "get_pnl", input: {} },
    { id: "t2", name: "get_positions", input: {} },
  ]);
});

test("toolUsesOf tolerates string / missing content", () => {
  assert.deepEqual(A.toolUsesOf({ content: "hi" }), []);
  assert.deepEqual(A.toolUsesOf({}), []);
  assert.deepEqual(A.toolUsesOf(null), []);
});

test("textOf concatenates and trims text blocks", () => {
  assert.equal(A.textOf({ content: [{ type: "text", text: " up $3. " }] }), "up $3.");
  assert.equal(A.textOf({ content: [
    { type: "text", text: "a" },
    { type: "tool_use", id: "x", name: "get_pnl" },
    { type: "text", text: "b" },
  ] }), "ab");
  assert.equal(A.textOf({ content: [] }), "");
});

test("isToolName gates unknown tools", () => {
  assert.equal(A.isToolName("get_portfolio"), true);
  assert.equal(A.isToolName("place_order"), false);
});

test("toolResultBlock stringifies success and flags errors", () => {
  const ok = A.toolResultBlock("t1", { source: "GET /pnl", as_of: "now", data: { total_pnl: 5 } });
  assert.equal(ok.tool_use_id, "t1");
  assert.equal(ok.is_error, undefined);
  assert.deepEqual(JSON.parse(ok.content), { source: "GET /pnl", as_of: "now", data: { total_pnl: 5 } });

  const bad = A.toolResultBlock("t2", { error: "MCP get_pnl unavailable" });
  assert.equal(bad.is_error, true);
  assert.equal(bad.content, "MCP get_pnl unavailable");
});

test("toolResultMessage bundles results into a user turn", () => {
  const m = A.toolResultMessage([
    { id: "t1", result: { source: "GET /pnl", as_of: "t", data: {} } },
    { id: "t2", result: { error: "boom" } },
  ]);
  assert.equal(m.role, "user");
  assert.equal(m.content.length, 2);
  assert.equal(m.content[0].tool_use_id, "t1");
  assert.equal(m.content[1].is_error, true);
});

test("citationsFrom dedups source+as_of and skips errors", () => {
  const cites = A.citationsFrom([
    { source: "GET /pnl", as_of: "10:00" },
    { source: "GET /pnl", as_of: "10:00" }, // dup
    { error: "boom" }, // skipped
    { source: "GET /portfolio", as_of: "10:01" },
    { source: "GET /pnl" }, // no as_of, distinct key
  ]);
  assert.deepEqual(cites, [
    { source: "GET /pnl", as_of: "10:00" },
    { source: "GET /portfolio", as_of: "10:01" },
    { source: "GET /pnl", as_of: null },
  ]);
});

test("confidence: high with a good read, low if all failed, medium with no tools", () => {
  assert.equal(A.confidence([{ source: "GET /pnl", as_of: "t" }]), "high");
  assert.equal(A.confidence([{ error: "boom" }]), "low");
  assert.equal(A.confidence([{ error: "a" }, { source: "GET /pnl" }]), "high");
  assert.equal(A.confidence([]), "medium");
});

test("summarizeResult renders source or error for the audit", () => {
  assert.equal(
    A.summarizeResult("get_pnl", { source: "GET /pnl", as_of: "10:00 SGT" }),
    "get_pnl → GET /pnl @ 10:00 SGT"
  );
  assert.equal(
    A.summarizeResult("get_pnl", { error: "unavailable" }),
    "get_pnl → error: unavailable"
  );
});

test("sumUsage adds token counts across rounds", () => {
  assert.deepEqual(
    A.sumUsage([{ input_tokens: 10, output_tokens: 5 }, { input_tokens: 3, output_tokens: 8 }]),
    { input_tokens: 13, output_tokens: 13 }
  );
  assert.deepEqual(A.sumUsage([]), { input_tokens: 0, output_tokens: 0 });
});
