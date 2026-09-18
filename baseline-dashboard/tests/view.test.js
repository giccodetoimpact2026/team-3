// Pure view-model logic: what the table shows for a given portfolio, and how
// the SSE cursor and session boundary behave. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const { positionRows, FeedCursor } = require("../js/view.js");

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
