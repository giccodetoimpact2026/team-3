const test = require("node:test");
const assert = require("node:assert");
const A = require("../js/analytics.js");
const Trades = require("../js/trades.js");

test("performance: win rate, profit factor and totals from realizing fills", () => {
  // Two closed trades on NOVA: one +$4, one -$2.
  const rows = [
    { Instrument: "NOVA", Status: "FILLED", PnLImpact: "4.00" },
    { Instrument: "NOVA", Status: "FILLED", PnLImpact: "-2.00" },
    { Instrument: "NOVA", Status: "FILLED", PnLImpact: "0.00" }, // a buy fill: ignored
    { Instrument: "NOVA", Status: "ACKNOWLEDGED", PnLImpact: "" }, // not a fill
  ];
  const [nova] = A.performance(rows);
  assert.equal(nova.symbol, "NOVA");
  assert.equal(nova.trades, 2);
  assert.equal(nova.wins, 1);
  assert.equal(nova.losses, 1);
  assert.equal(nova.winRate, 0.5);
  assert.equal(nova.profitFactor, 2); // 4 / 2
  assert.equal(nova.avgWin, 4);
  assert.equal(nova.avgLoss, -2);
  assert.equal(nova.totalPnl, 2);
});

test("performance: sharpe is null with fewer than two trades or zero variance", () => {
  assert.equal(A.performance([{ Instrument: "X", Status: "FILLED", PnLImpact: "5.00" }])[0].sharpe, null);
  const flat = A.performance([
    { Instrument: "Y", Status: "FILLED", PnLImpact: "3.00" },
    { Instrument: "Y", Status: "FILLED", PnLImpact: "3.00" },
  ])[0];
  assert.equal(flat.sharpe, null); // zero stddev
});

test("fillRate: counts sent/filled/rejected and the fill-rate fraction", () => {
  const orders = [
    { id: "1", symbol: "NOVA", status: "FILLED" },
    { id: "2", symbol: "NOVA", status: "REJECTED" },
    { id: "3", symbol: "NOVA", status: "OPEN" },
  ];
  const history = new Trades.LifecycleHistory("s", null);
  const [nova] = A.fillRate(orders, history);
  assert.equal(nova.sent, 3);
  assert.equal(nova.filled, 1);
  assert.equal(nova.rejected, 1);
  assert.ok(Math.abs(nova.fillRate - 1 / 3) < 1e-9);
});

test("liveQuotes: only open orders, bids highest-first and asks lowest-first", () => {
  const orders = [
    { id: "1", side: "buy", price: 100, status: "OPEN" },
    { id: "2", side: "buy", price: 105, status: "PARTIALLY_FILLED" },
    { id: "3", side: "sell", price: 120, status: "OPEN" },
    { id: "4", side: "sell", price: 110, status: "PENDING" },
    { id: "5", side: "buy", price: 999, status: "FILLED" }, // terminal: excluded
    { id: "6", side: "sell", price: 999, status: "CANCELLED" }, // terminal: excluded
  ];
  const q = A.liveQuotes(orders);
  assert.deepEqual(q.bids.map(o => o.price), [105, 100]); // best bid first
  assert.deepEqual(q.asks.map(o => o.price), [110, 120]); // best ask first
});

test("candles: buckets price samples into OHLC bars", () => {
  const samples = [
    { t: 0, price: 100 },
    { t: 10, price: 110 },
    { t: 20, price: 90 },
    { t: 60000, price: 95 }, // next 1m bucket
  ];
  const bars = A.candles(samples, 60000);
  assert.equal(bars.length, 2);
  assert.deepEqual({ o: bars[0].open, h: bars[0].high, l: bars[0].low, c: bars[0].close }, { o: 100, h: 110, l: 90, c: 90 });
  assert.equal(bars[1].open, 95);
});

test("vwapPoints: running volume-weighted average per bucket", () => {
  const trades = [
    { t: 0, price: 100, qty: 1 },
    { t: 10, price: 200, qty: 3 }, // cum vwap = (100 + 600) / 4 = 175
  ];
  const pts = A.vwapPoints(trades, 60000);
  assert.equal(pts.length, 1);
  assert.equal(pts[0].vwap, 175);
});

test("correlation: perfectly correlated series -> r = 1, diagonal = 1", () => {
  const buffers = {
    A: [{ t: 0, price: 100 }, { t: 1, price: 110 }, { t: 2, price: 121 }, { t: 3, price: 133 }],
    B: [{ t: 0, price: 10 }, { t: 1, price: 11 }, { t: 2, price: 12.1 }, { t: 3, price: 13.3 }],
  };
  const { symbols, matrix } = A.correlation(buffers, ["A", "B"]);
  assert.deepEqual(symbols, ["A", "B"]);
  assert.equal(matrix[0][0], 1);
  assert.ok(matrix[0][1] > 0.99);
});

test("netExposure: signs short positions negative", () => {
  const exp = A.netExposure([
    { symbol: "A", quantity: 10, market_value: 1000 },
    { symbol: "B", quantity: -5, market_value: 500 },
  ]);
  assert.equal(exp.net, 500); // 1000 - 500
  assert.equal(exp.gross, 1500);
});

test("drawdown: max peak-to-trough drop as a fraction", () => {
  const dd = A.drawdown([
    { t: 0, value: 100 },
    { t: 1, value: 120 }, // peak
    { t: 2, value: 90 }, // -25% from peak
    { t: 3, value: 108 },
  ]);
  assert.ok(Math.abs(dd.maxDrawdown - 0.25) < 1e-9);
  assert.ok(Math.abs(dd.currentDrawdown - (120 - 108) / 120) < 1e-9);
});
