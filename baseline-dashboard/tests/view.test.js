// Pure view-model logic: what the table shows for a given portfolio, and how
// the SSE cursor and session boundary behave. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const { positionRows, FeedCursor, botStatusView, haltResultText } = require("../js/view.js");

test("positionRows: instrument absent from the portfolio shows 0 quantity and dashes", () => {
  const rows = positionRows(["NOVA", "AXON"], { positions: [{ symbol: "NOVA", quantity: 5, avg_cost: 100, market_value: 500, unrealized_pnl: -10 }] });
  assert.deepStrictEqual(rows, [
    { symbol: "NOVA", quantity: "5", avg_cost: "$1.00", market_value: "$5.00", unrealized_pnl: "-$0.10" },
    { symbol: "AXON", quantity: "0", avg_cost: "—", market_value: "—", unrealized_pnl: "—" },
  ]);
});

test("positionRows: follows the instrument list order, not the portfolio order", () => {
  const rows = positionRows(["B", "A"], { positions: [{ symbol: "A", quantity: 1 }, { symbol: "B", quantity: 2 }] });
  assert.deepStrictEqual(rows.map((r) => r.symbol), ["B", "A"]);
});

test("positionRows: tolerates a missing positions array", () => {
  assert.deepStrictEqual(positionRows(["X"], {}), [{ symbol: "X", quantity: "0", avg_cost: "—", market_value: "—", unrealized_pnl: "—" }]);
});

test("FeedCursor: snapshot frames without an id never move the cursor", () => {
  const c = new FeedCursor();
  assert.strictEqual(c.accept(""), true);
  assert.strictEqual(c.accept(undefined), true);
  assert.strictEqual(c.lastId, null);
});

test("FeedCursor: a replayed event id is rejected once seen", () => {
  const c = new FeedCursor();
  assert.strictEqual(c.accept("41"), true);
  assert.strictEqual(c.accept("42"), true);
  assert.strictEqual(c.accept("41"), false);
  assert.strictEqual(c.accept("42"), false);
  assert.strictEqual(c.lastId, "42");
});

test("FeedCursor: ids restarting from 1 after an exchange restart are accepted", () => {
  const c = new FeedCursor();
  c.accept("5000");
  assert.strictEqual(c.accept("1"), true);
});

test("FeedCursor: sessionChanged is true only when a different non-empty id arrives", () => {
  const c = new FeedCursor();
  assert.strictEqual(c.sessionChanged("s1"), false); // first sighting is not a change
  assert.strictEqual(c.sessionChanged("s1"), false);
  assert.strictEqual(c.sessionChanged(""), false);
  assert.strictEqual(c.sessionChanged("s2"), true);
  assert.strictEqual(c.sessionChanged("s2"), false);
});

const { escapeHtml } = require("../js/view.js");

test("escapeHtml: neutralises markup in values that come from the exchange", () => {
  assert.strictEqual(escapeHtml('<img src=x onerror="alert(1)">&'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;");
  assert.strictEqual(escapeHtml(undefined), "");
  assert.strictEqual(escapeHtml(42), "42");
});

// --- Kill switch (P0-14) ----------------------------------------------------

test("botStatusView: RUNNING enables the halt button", () => {
  assert.deepStrictEqual(botStatusView({ state: "RUNNING" }), { label: "BOT RUNNING", cls: "running", canHalt: true });
});

test("botStatusView: QUIESCING and STOPPED disable the button", () => {
  assert.deepStrictEqual(botStatusView({ state: "QUIESCING" }), { label: "BOT HALTING…", cls: "quiescing", canHalt: false });
  assert.deepStrictEqual(botStatusView({ state: "STOPPED" }), { label: "BOT HALTED", cls: "stopped", canHalt: false });
});

test("botStatusView: no answer, unknown state or bad payload is OFFLINE", () => {
  const offline = { label: "BOT OFFLINE", cls: "offline", canHalt: false };
  assert.deepStrictEqual(botStatusView(null), offline);
  assert.deepStrictEqual(botStatusView({}), offline);
  assert.deepStrictEqual(botStatusView({ state: "weird" }), offline);
});

test("botStatusView: state is case-insensitive", () => {
  assert.strictEqual(botStatusView({ state: "running" }).canHalt, true);
});

test("haltResultText: reports unresolved cancellations, singular and plural", () => {
  assert.strictEqual(haltResultText({ state: "STOPPED", unresolved: 0 }), "Halted. All open orders cancelled.");
  assert.strictEqual(haltResultText({ state: "STOPPED" }), "Halted. All open orders cancelled.");
  assert.strictEqual(haltResultText({ unresolved: 1 }), "Halted. 1 order could not be confirmed cancelled; check the exchange.");
  assert.strictEqual(haltResultText({ unresolved: 3 }), "Halted. 3 orders could not be confirmed cancelled; check the exchange.");
});
