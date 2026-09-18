// Order lifecycle, session counters, cost basis and CSV export logic (P4/P5).
const test = require("node:test");
const assert = require("node:assert");
const {
  normalizeStatus,
  LifecycleHistory,
  counters,
  startingBasis,
  csvRows,
  toCsv,
  CSV_HEADER,
} = require("../js/trades.js");

// --- status normalization ---------------------------------------------------

test("normalizeStatus: exchange OPEN maps to Gerald's ACKNOWLEDGED", () => {
  assert.strictEqual(normalizeStatus("OPEN"), "ACKNOWLEDGED");
});

test("normalizeStatus: Gerald's other codes pass through unchanged", () => {
  ["PENDING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"].forEach((s) =>
    assert.strictEqual(normalizeStatus(s), s)
  );
});

test("normalizeStatus: lower-case input is upper-cased", () => {
  assert.strictEqual(normalizeStatus("filled"), "FILLED");
});

// --- lifecycle history ------------------------------------------------------

function memStorage() {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, _m: m };
}
const order = (over) =>
  Object.assign(
    { id: "o1", symbol: "NOVA", side: "buy", price: 10000, quantity: 10, remaining_qty: 10, status: "OPEN",
      created_at: "2026-09-18T01:00:00.000Z", updated_at: "2026-09-18T01:00:00.000Z" },
    over
  );

test("LifecycleHistory: first sighting records the observed status at the order's updated_at", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([order()]);
  assert.deepStrictEqual(h.entries(), [{ orderId: "o1", status: "ACKNOWLEDGED", at: "2026-09-18T01:00:00.000Z", remaining: 10 }]);
});

test("LifecycleHistory: observing the same status twice records one transition", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([order()]);
  h.observe([order({ updated_at: "2026-09-18T01:00:05.000Z" })]);
  assert.strictEqual(h.entries().length, 1);
});

test("LifecycleHistory: a status change appends a new transition", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([order()]);
  h.observe([order({ status: "FILLED", remaining_qty: 0, updated_at: "2026-09-18T01:00:05.000Z" })]);
  assert.deepStrictEqual(h.entries().map((e) => e.status), ["ACKNOWLEDGED", "FILLED"]);
  assert.strictEqual(h.entries()[1].at, "2026-09-18T01:00:05.000Z");
});

test("LifecycleHistory: does not invent a PENDING or ACKNOWLEDGED step it never saw", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([order({ status: "FILLED", remaining_qty: 0 })]);
  assert.deepStrictEqual(h.entries().map((e) => e.status), ["FILLED"]);
});

test("LifecycleHistory: persists to storage and reloads for the same session", () => {
  const storage = memStorage();
  new LifecycleHistory("s1", storage).observe([order()]);
  const again = new LifecycleHistory("s1", storage);
  assert.strictEqual(again.entries().length, 1);
});

test("LifecycleHistory: a different session id never sees another session's history", () => {
  const storage = memStorage();
  new LifecycleHistory("s1", storage).observe([order()]);
  assert.deepStrictEqual(new LifecycleHistory("s2", storage).entries(), []);
});

test("LifecycleHistory: entries are chronological across orders", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([
    order({ id: "late", created_at: "2026-09-18T01:00:09.000Z", updated_at: "2026-09-18T01:00:09.000Z" }),
    order({ id: "early", created_at: "2026-09-18T01:00:01.000Z", updated_at: "2026-09-18T01:00:01.000Z" }),
  ]);
  assert.deepStrictEqual(h.entries().map((e) => e.orderId), ["early", "late"]);
});

test("LifecycleHistory: tolerates a storage that throws", () => {
  const broken = { getItem: () => { throw new Error("private mode"); }, setItem: () => { throw new Error("quota"); } };
  const h = new LifecycleHistory("s1", broken);
  h.observe([order()]);
  assert.strictEqual(h.entries().length, 1);
});

// --- counters ---------------------------------------------------------------

test("counters: buy and sell count distinct orders by side", () => {
  const c = counters([order({ id: "a", side: "buy" }), order({ id: "b", side: "buy" }), order({ id: "c", side: "sell" })]);
  assert.strictEqual(c.buy, 2);
  assert.strictEqual(c.sell, 1);
});

test("counters: rejected counts orders in REJECTED state", () => {
  const c = counters([order({ id: "a", status: "REJECTED" }), order({ id: "b" })]);
  assert.strictEqual(c.rejected, 1);
});

test("counters: partial counts orders that reached PARTIALLY_FILLED, including ones since filled", () => {
  const h = new LifecycleHistory("s1", memStorage());
  h.observe([order({ id: "a", status: "PARTIALLY_FILLED", remaining_qty: 5 })]);
  const nowFilled = [order({ id: "a", status: "FILLED", remaining_qty: 0 }), order({ id: "b", status: "PARTIALLY_FILLED", remaining_qty: 1 })];
  h.observe(nowFilled);
  assert.strictEqual(counters(nowFilled, h).partial, 2);
});

test("counters: empty order list gives zeros", () => {
  assert.deepStrictEqual(counters([]), { buy: 0, sell: 0, partial: 0, rejected: 0 });
});

// --- cost basis and CSV -----------------------------------------------------

const fill = (over) =>
  Object.assign(
    { trade_id: "t1", order_id: "o1", symbol: "NOVA", side: "buy", price: 10000, quantity: 10, timestamp: "2026-09-18T01:00:01.000Z" },
    over
  );

test("startingBasis: walks fills backwards from the current position to the session-open basis", () => {
  // Opened with 100 @ $100, bought 10 @ $120 -> now 110 @ ~$101.82
  const positions = [{ symbol: "NOVA", quantity: 110, avg_cost: 10182 }];
  const trades = [fill({ side: "buy", price: 12000, quantity: 10 })];
  const basis = startingBasis(positions, trades);
  assert.strictEqual(basis.NOVA.quantity, 100);
  assert.ok(Math.abs(basis.NOVA.avg_cost - 10000) <= 2, "avg_cost within rounding of 10000, got " + basis.NOVA.avg_cost);
});

test("startingBasis: a sell does not change the average cost going backwards", () => {
  const positions = [{ symbol: "NOVA", quantity: 90, avg_cost: 10000 }];
  const trades = [fill({ side: "sell", price: 13000, quantity: 10 })];
  assert.deepStrictEqual(startingBasis(positions, trades).NOVA, { quantity: 100, avg_cost: 10000 });
});

test("csvRows: header order is exactly Gerald's", () => {
  assert.strictEqual(
    CSV_HEADER,
    "TradeID,Timestamp_SGT,Instrument,Direction,Quantity,LimitPrice,FillPrice,FillQuantity,RemainingQty,Status,PnLImpact,CumulativePnL"
  );
});

test("csvRows: a sell fill books (fill − basis) × qty as PnLImpact and runs CumulativePnL", () => {
  const orders = [order({ id: "o1", side: "sell", price: 12000, quantity: 20, remaining_qty: 0, status: "FILLED" })];
  const trades = [
    fill({ trade_id: "t2", side: "sell", price: 12000, quantity: 10, timestamp: "2026-09-18T01:00:02.000Z" }),
    fill({ trade_id: "t1", side: "sell", price: 11000, quantity: 10, timestamp: "2026-09-18T01:00:01.000Z" }),
  ];
  const positions = [{ symbol: "NOVA", quantity: 80, avg_cost: 10000 }];
  const rows = csvRows(orders, trades, positions).filter((r) => r.FillQuantity !== "");
  assert.deepStrictEqual(rows.map((r) => r.TradeID), ["t1", "t2"]); // chronological
  assert.deepStrictEqual(rows.map((r) => r.PnLImpact), ["100.00", "200.00"]);
  assert.deepStrictEqual(rows.map((r) => r.CumulativePnL), ["100.00", "300.00"]);
  assert.deepStrictEqual(rows.map((r) => r.RemainingQty), ["10", "0"]); // per-fill remaining
  assert.deepStrictEqual(rows.map((r) => r.Status), ["PARTIALLY_FILLED", "FILLED"]);
});

test("csvRows: a buy fill has zero PnLImpact and raises the basis for later sells", () => {
  const orders = [
    order({ id: "b", side: "buy", price: 20000, quantity: 100, remaining_qty: 0, status: "FILLED", created_at: "2026-09-18T01:00:00.000Z" }),
    order({ id: "s", side: "sell", price: 20000, quantity: 100, remaining_qty: 0, status: "FILLED", created_at: "2026-09-18T01:00:03.000Z" }),
  ];
  const trades = [
    fill({ trade_id: "ts", order_id: "s", side: "sell", price: 20000, quantity: 100, timestamp: "2026-09-18T01:00:04.000Z" }),
    fill({ trade_id: "tb", order_id: "b", side: "buy", price: 20000, quantity: 100, timestamp: "2026-09-18T01:00:01.000Z" }),
  ];
  // started 100 @ 100.00, bought 100 @ 200.00 (basis 150.00), sold 100 @ 200.00 -> +5000.00, now 100 @ 150.00
  const positions = [{ symbol: "NOVA", quantity: 100, avg_cost: 15000 }];
  const fills = csvRows(orders, trades, positions).filter((r) => r.FillQuantity !== "");
  assert.deepStrictEqual(fills.map((r) => r.PnLImpact), ["0.00", "5000.00"]);
});

test("csvRows: non-fill lifecycle rows use the order id, blank fill fields, and the normalized status", () => {
  const orders = [
    order({ id: "ack", status: "OPEN" }),
    order({ id: "rej", status: "REJECTED", created_at: "2026-09-18T01:00:02.000Z", updated_at: "2026-09-18T01:00:02.000Z" }),
    order({ id: "can", status: "CANCELLED", remaining_qty: 4, created_at: "2026-09-18T01:00:03.000Z", updated_at: "2026-09-18T01:00:09.000Z" }),
  ];
  const rows = csvRows(orders, [], []);
  const byId = Object.fromEntries(rows.map((r) => [r.TradeID + ":" + r.Status, r]));
  assert.ok(byId["ack:ACKNOWLEDGED"]);
  assert.ok(byId["rej:REJECTED"]);
  assert.ok(byId["can:ACKNOWLEDGED"], "a cancelled order was acknowledged first");
  assert.ok(byId["can:CANCELLED"]);
  assert.strictEqual(byId["can:CANCELLED"].RemainingQty, "4");
  assert.strictEqual(byId["can:CANCELLED"].Timestamp_SGT, "2026-09-18 09:00:09.000 SGT");
  assert.strictEqual(byId["ack:ACKNOWLEDGED"].FillPrice, "");
  assert.strictEqual(byId["ack:ACKNOWLEDGED"].FillQuantity, "");
  assert.strictEqual(byId["ack:ACKNOWLEDGED"].LimitPrice, "100.00");
  assert.strictEqual(byId["rej:REJECTED"].PnLImpact, "0.00");
});

test("csvRows: rows are chronological across orders and fills", () => {
  const orders = [
    order({ id: "o2", created_at: "2026-09-18T01:00:05.000Z", updated_at: "2026-09-18T01:00:05.000Z" }),
    order({ id: "o1", status: "FILLED", remaining_qty: 0, created_at: "2026-09-18T01:00:00.000Z", updated_at: "2026-09-18T01:00:01.000Z" }),
  ];
  const trades = [fill({ order_id: "o1", timestamp: "2026-09-18T01:00:01.000Z" })];
  const rows = csvRows(orders, trades, []);
  assert.deepStrictEqual(rows.map((r) => r.Timestamp_SGT), [
    "2026-09-18 09:00:00.000 SGT",
    "2026-09-18 09:00:01.000 SGT",
    "2026-09-18 09:00:05.000 SGT",
  ]);
});

test("toCsv: period decimal separators, CRLF lines, and quoting of fields with commas or quotes", () => {
  const csv = toCsv([
    { TradeID: 'x,"y"', Timestamp_SGT: "2026-09-18 09:00:00.000 SGT", Instrument: "NOVA", Direction: "BUY", Quantity: "10",
      LimitPrice: "1234.50", FillPrice: "", FillQuantity: "", RemainingQty: "10", Status: "ACKNOWLEDGED", PnLImpact: "0.00", CumulativePnL: "0.00" },
  ]);
  const lines = csv.split("\r\n");
  assert.strictEqual(lines[0], CSV_HEADER);
  assert.strictEqual(lines[1], '"x,""y""",2026-09-18 09:00:00.000 SGT,NOVA,BUY,10,1234.50,,,10,ACKNOWLEDGED,0.00,0.00');
  assert.ok(!/\d,\d\d\b/.test(lines[1].replace(/^"[^"]*"/, "")), "no comma decimals");
});

// --- P&L fallback -----------------------------------------------------------

const { pnlFromPortfolio } = require("../js/trades.js");

test("pnlFromPortfolio: converts cents to dollars in the MCP get_pnl shape and zero-fills untraded symbols", () => {
  const portfolio = {
    realized_pnl: -156050, unrealized_pnl: 32000,
    positions: [{ symbol: "NOVA", realized_pnl: 0, unrealized_pnl: 32000 }, { symbol: "ELXR", realized_pnl: -156050, unrealized_pnl: 0 }],
  };
  const out = pnlFromPortfolio(portfolio, ["NOVA", "ELXR", "AXON"], new Date("2026-09-18T01:02:03.456Z"));
  assert.deepStrictEqual(out.data.aggregate, { realized_pnl: -1560.5, unrealized_pnl: 320, total_pnl: -1240.5 });
  assert.deepStrictEqual(out.data.by_instrument.ELXR, { realized_pnl: -1560.5, unrealized_pnl: 0, total_pnl: -1560.5 });
  assert.deepStrictEqual(out.data.by_instrument.AXON, { realized_pnl: 0, unrealized_pnl: 0, total_pnl: 0 });
  assert.strictEqual(out.as_of, "2026-09-18 09:02:03.456 SGT");
  assert.match(out.source, /portfolio/);
});
