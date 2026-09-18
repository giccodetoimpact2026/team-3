// The proxy error path must never throw after a response has started streaming
// (an SSE drop mid-stream took the whole dev server down with ERR_HTTP_HEADERS_SENT).
const test = require("node:test");
const assert = require("node:assert");
const { sendError } = require("../lib/respond.js");

function fakeRes(headersSent) {
  const r = { headersSent, status: null, headers: null, body: null, ended: false };
  r.writeHead = (s, h) => { if (r.headersSent) throw new Error("ERR_HTTP_HEADERS_SENT"); r.status = s; r.headers = h; r.headersSent = true; };
  r.end = (b) => { r.body = b; r.ended = true; };
  return r;
}

test("sendError: before headers, writes the JSON error envelope with the status", () => {
  const res = fakeRes(false);
  sendError(res, 502, "PROXY_ERROR", "socket hang up");
  assert.strictEqual(res.status, 502);
  assert.strictEqual(res.headers["content-type"], "application/json");
  assert.deepStrictEqual(JSON.parse(res.body), { status: "error", error: { code: "PROXY_ERROR", message: "socket hang up" } });
  assert.ok(res.ended);
});

test("sendError: after headers were sent, ends the response without writing a head", () => {
  const res = fakeRes(true);
  assert.doesNotThrow(() => sendError(res, 502, "PROXY_ERROR", "upstream closed"));
  assert.strictEqual(res.status, null);
  assert.ok(res.ended);
});

test("sendError: a response that is already finished is left alone", () => {
  const res = fakeRes(true);
  res.writableEnded = true;
  let calls = 0;
  res.end = () => { calls++; };
  sendError(res, 502, "PROXY_ERROR", "late");
  assert.strictEqual(calls, 0);
});
