// Persona analytics: heat map, fill rates, exposure, alerts, performance, summary. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const A = require("../js/analytics.js");
const Trades = require("../js/trades.js");

test("heatTiles: class follows the sign, intensity scales to the largest absolute P&L", () => {
  const by = { NOVA: { total_pnl: 100 }, ELXR: { total_pnl: -50 }, AXON: { total_pnl: 0 } };
  const tiles = A.heatTiles(by, ["NOVA", "ELXR", "AXON", "DRFT"]);
  assert.deepStrictEqual(tiles[0], { symbol: "NOVA", label: "$100.00", cls: "pos", intensity: 1 });
  assert.deepStrictEqual(tiles[1], { symbol: "ELXR", label: "-$50.00", cls: "neg", intensity: 0.5 });
  assert.deepStrictEqual(tiles[2], { symbol: "AXON", label: "$0.00", cls: "flat", intensity: 0 });
  assert.deepStrictEqual(tiles[3], { symbol: "DRFT", label: "—", cls: "flat", intensity: 0 });
});

test("fillRateRows: sent, filled, partial, rejected and the fill rate per instrument", () => {
  const orders = [
    { id: "a", symbol: "NOVA", status: "FILLED" },
    { id: "b", symbol: "NOVA", status: "OPEN" },
    { id: "c", symbol: "NOVA", status: "REJECTED" },
    { id: "d", symbol: "ELXR", status: "PARTIALLY_FILLED" },
  ];
  const history = new Trades.LifecycleHistory("s1", null);
  history.record([{ seq: 1, order_id: "e", symbol: "NOVA", status: "REJECTED", at: "2026-09-18T01:00:00Z", side: "buy", quantity: 1, price: 1 }]);
  const rows = A.fillRateRows(orders, history, ["NOVA", "ELXR", "AXON"]);
  assert.deepStrictEqual(rows[0], { symbol: "NOVA", sent: 4, filled: 1, partial: 0, rejected: 2, fillRate: "25%" });
  assert.deepStrictEqual(rows[1], { symbol: "ELXR", sent: 1, filled: 0, partial: 1, rejected: 0, fillRate: "0%" });
  assert.deepStrictEqual(rows[2], { symbol: "AXON", sent: 0, filled: 0, partial: 0, rejected: 0, fillRate: "—" });
});

test("exposureRows: quantity against the cap and market value share of total value", () => {
  const portfolio = { total_value: 174074480, positions: [{ symbol: "CRVX", quantity: 2252, market_value: 44064884 }] };
  const rows = A.exposureRows(portfolio, { CRVX: 1500, NOVA: 1500 }, ["CRVX", "NOVA"]);
  assert.deepStrictEqual(rows[0], { symbol: "CRVX", quantity: 2252, cap: 1500, pctOfCap: 150, overCap: true, marketValue: 44064884, share: 25.3 });
  assert.deepStrictEqual(rows[1], { symbol: "NOVA", quantity: 0, cap: 1500, pctOfCap: 0, overCap: false, marketValue: 0, share: 0 });
});

test("exposureRows: an unknown cap gives null pctOfCap and never flags overCap", () => {
  const rows = A.exposureRows({ total_value: 1000, positions: [{ symbol: "NOVA", quantity: 5, market_value: 500 }] }, {}, ["NOVA"]);
  assert.strictEqual(rows[0].pctOfCap, null);
  assert.strictEqual(rows[0].overCap, false);
});

test("netExposure: long market value as a share of total value", () => {
  const p = { total_value: 2000, positions: [{ market_value: 500 }, { market_value: 1000 }] };
  assert.deepStrictEqual(A.netExposure(p), { longValue: 1500, totalValue: 2000, pct: 75 });
  assert.deepStrictEqual(A.netExposure(null), { longValue: 0, totalValue: 0, pct: 0 });
});

test("evaluateAlerts: drawdown past 15% of starting capital is critical", () => {
  const alerts = A.evaluateAlerts({ portfolio: { total_value: 840 }, startingValue: 1000 });
  assert.strictEqual(alerts.length, 1);
  assert.strictEqual(alerts[0].level, "critical");
  assert.strictEqual(alerts[0].code, "DRAWDOWN");
  assert.deepStrictEqual(A.evaluateAlerts({ portfolio: { total_value: 900 }, startingValue: 1000 }), []);
});

test("evaluateAlerts: a position over its cap is a warning", () => {
  const alerts = A.evaluateAlerts({ portfolio: { total_value: 1000, positions: [{ symbol: "CRVX", quantity: 2252 }] }, startingValue: 1000, caps: { CRVX: 1500 } });
  assert.deepStrictEqual(alerts.map((a) => [a.level, a.code]), [["warning", "OVER_CAP"]]);
  assert.ok(alerts[0].message.includes("CRVX"));
});

test("evaluateAlerts: a dead bot feed or an order-rate pause is reported; a healthy bot is not", () => {
  const dead = A.evaluateAlerts({ bot: { state: "RUNNING", feed_state: "DEAD" } });
  assert.deepStrictEqual(dead.map((a) => [a.level, a.code]), [["critical", "BOT_DEAD"]]);
  const paused = A.evaluateAlerts({ bot: { state: "RUNNING", feed_state: "OK", orders: { paused: true, pause_reason: "order-rate anomaly" } } });
  assert.deepStrictEqual(paused.map((a) => [a.level, a.code]), [["warning", "BOT_PAUSED"]]);
  assert.deepStrictEqual(A.evaluateAlerts({ bot: { state: "RUNNING", feed_state: "OK", orders: { paused: false } } }), []);
  assert.deepStrictEqual(A.evaluateAlerts({ bot: null }), [["warning", "BOT_OFFLINE"]].map(([level, code]) => ({ level, code, message: "Bot is offline (no status from the control endpoint)" })));
});

test("evaluateAlerts: an instrument down more than 10% from its session start price is a warning", () => {
  const alerts = A.evaluateAlerts({ lastPrices: { CRVX: 8900, NOVA: 10100 }, startPrices: { CRVX: 10000, NOVA: 10000 } });
  assert.deepStrictEqual(alerts.map((a) => [a.level, a.code]), [["warning", "INSTRUMENT_DROP"]]);
  assert.ok(alerts[0].message.includes("CRVX") && alerts[0].message.includes("11.0%"));
});

test("performanceRows: fills, wins, losses, win rate, average win and loss, profit factor from sells", () => {
  const trades = [
    { trade_id: "t4", symbol: "NOVA", side: "sell", price: 9500, quantity: 100, timestamp: "2026-09-18T01:04:00Z" },
    { trade_id: "t3", symbol: "NOVA", side: "buy", price: 10000, quantity: 100, timestamp: "2026-09-18T01:03:00Z" },
    { trade_id: "t2", symbol: "NOVA", side: "sell", price: 11000, quantity: 100, timestamp: "2026-09-18T01:02:00Z" },
    { trade_id: "t1", symbol: "NOVA", side: "buy", price: 10000, quantity: 100, timestamp: "2026-09-18T01:01:00Z" },
  ];
  const rows = A.performanceRows(trades, [{ symbol: "NOVA", quantity: 0, avg_cost: 0 }], ["NOVA", "ELXR"]);
  assert.deepStrictEqual(rows[0], { symbol: "NOVA", fills: 4, wins: 1, losses: 1, winRate: "50%", avgWin: 100000, avgLoss: -50000, profitFactor: "2.00", realized: 50000 });
  assert.deepStrictEqual(rows[1], { symbol: "ELXR", fills: 0, wins: 0, losses: 0, winRate: "—", avgWin: null, avgLoss: null, profitFactor: "—", realized: 0 });
});

test("sessionSummary: total P&L, fill count, best and worst instrument, alert count", () => {
  const s = A.sessionSummary({
    portfolio: { realized_pnl: 836723, unrealized_pnl: -6193098, trades: [{}, {}, {}] },
    pnlByInstrument: { AXON: { total_pnl: 7817.71 }, CRVX: { total_pnl: -59193.8 }, ELXR: { total_pnl: 13649.08 } },
    alerts: [{ level: "critical" }],
  });
  assert.deepStrictEqual(s, { totalPnl: -5356375, fills: 3, best: "ELXR", worst: "CRVX", alerts: 1 });
});

test("openOrderRows: resting orders only, newest first, with filled quantity and age", () => {
  const now = Date.parse("2026-09-18T05:00:30Z");
  const orders = [
    { id: "a1", symbol: "NOVA", side: "buy", quantity: 100, remaining_qty: 100, price: 15000, status: "OPEN", created_at: "2026-09-18T05:00:00Z" },
    { id: "b2", symbol: "ELXR", side: "sell", quantity: 50, remaining_qty: 20, price: 30100, status: "PARTIALLY_FILLED", created_at: "2026-09-18T05:00:20Z" },
    { id: "c3", symbol: "ELXR", side: "buy", quantity: 50, remaining_qty: 0, price: 30000, status: "FILLED", created_at: "2026-09-18T05:00:10Z" },
    { id: "d4", symbol: "AXON", side: "buy", quantity: 10, remaining_qty: 10, price: 8000, status: "CANCELLED", created_at: "2026-09-18T05:00:05Z" },
  ];
  const rows = A.openOrderRows(orders, now);
  assert.deepStrictEqual(rows, [
    { id: "b2", symbol: "ELXR", side: "SELL", quantity: 50, filled: 30, remaining: 20, price: 30100, status: "PARTIALLY_FILLED", ageS: 10 },
    { id: "a1", symbol: "NOVA", side: "BUY", quantity: 100, filled: 0, remaining: 100, price: 15000, status: "ACKNOWLEDGED", ageS: 30 },
  ]);
  assert.deepStrictEqual(A.openOrderRows([], now), []);
});

test("healthItems: five components in order, each with a state and a detail", () => {
  const now = 1000000;
  const items = A.healthItems({
    bot: { state: "RUNNING", feed_state: "OK", heartbeat_age_s: 2, orders: { paused: false } },
    feedOnline: true, lastEventAt: now - 3000,
    exchangeOkAt: now - 2000, exchangeError: null,
    mcpOkAt: now - 5000, mcpFailedAt: 0,
    llm: { state: "ok", detail: "answered 12s ago" },
  }, now);
  assert.deepStrictEqual(items.map((i) => [i.id, i.state]), [["bot", "ok"], ["feed", "ok"], ["exchange", "ok"], ["mcp", "ok"], ["llm", "ok"]]);
  items.forEach((i) => assert.ok(i.label && typeof i.detail === "string", i.id));
});

test("healthItems: offline bot is bad, paused bot and halted bot are warn", () => {
  assert.strictEqual(A.healthItems({ bot: null }, 0)[0].state, "bad");
  assert.strictEqual(A.healthItems({ bot: { state: "RUNNING", orders: { paused: true, pause_reason: "order-rate anomaly" } } }, 0)[0].state, "warn");
  assert.strictEqual(A.healthItems({ bot: { state: "STOPPED" } }, 0)[0].state, "warn");
  assert.strictEqual(A.healthItems({ bot: { state: "RUNNING", feed_state: "DEAD" } }, 0)[0].state, "bad");
});

test("healthItems: feed offline is bad; a silent feed is warn with the silence in the detail", () => {
  const now = 100000;
  assert.strictEqual(A.healthItems({ feedOnline: false }, now)[1].state, "bad");
  const silent = A.healthItems({ feedOnline: true, lastEventAt: now - 45000 }, now)[1];
  assert.strictEqual(silent.state, "warn");
  assert.ok(silent.detail.includes("45s"));
});

test("healthItems: exchange rate-limited is warn, never read is unknown; MCP failure after success is bad", () => {
  const now = 100000;
  const rl = A.healthItems({ exchangeOkAt: now - 40000, exchangeError: 429 }, now)[2];
  assert.strictEqual(rl.state, "warn");
  assert.ok(/429|rate/i.test(rl.detail));
  assert.strictEqual(A.healthItems({}, now)[2].state, "unknown");
  assert.strictEqual(A.healthItems({ mcpOkAt: now - 60000, mcpFailedAt: now - 1000 }, now)[3].state, "bad");
  assert.strictEqual(A.healthItems({}, now)[3].state, "unknown");
  assert.strictEqual(A.healthItems({ llm: { state: "warn", detail: "rate limited" } }, now)[4].state, "warn");
  assert.strictEqual(A.healthItems({}, now)[4].state, "unknown");
});

test("quietFeedPollDue: only when the feed is connected but silent, and the last read is old", () => {
  const now = 1000000;
  const opts = { silenceMs: 20000, pollMs: 30000 };
  assert.strictEqual(A.quietFeedPollDue({ feedOnline: true, lastEventAt: now - 25000, lastReadAt: now - 31000 }, now, opts), true);
  assert.strictEqual(A.quietFeedPollDue({ feedOnline: true, lastEventAt: now - 5000, lastReadAt: now - 31000 }, now, opts), false, "events flowing: event-driven reads suffice");
  assert.strictEqual(A.quietFeedPollDue({ feedOnline: true, lastEventAt: now - 25000, lastReadAt: now - 10000 }, now, opts), false, "read is recent");
  assert.strictEqual(A.quietFeedPollDue({ feedOnline: false, lastEventAt: now - 25000, lastReadAt: now - 31000 }, now, opts), false, "disconnected: EventSource reconnect handles it");
  assert.strictEqual(A.quietFeedPollDue({ feedOnline: true, lastEventAt: 0, lastReadAt: 0 }, now, opts), false, "nothing seen yet");
});

test("feedSilenceNote: explains a quiet feed, naming the session state when known", () => {
  const now = Date.UTC(2026, 8, 18, 6, 6, 49);
  const last = Date.UTC(2026, 8, 18, 6, 1, 38);
  const closed = A.feedSilenceNote({ lastEventAt: last, sessionStatus: "closed" }, now);
  assert.ok(closed.includes("14:01:38"), closed);
  assert.ok(/closed/i.test(closed) && /between .*windows|next window/i.test(closed), closed);
  const open = A.feedSilenceNote({ lastEventAt: last, sessionStatus: "open" }, now);
  assert.ok(/5m 11s|311s/.test(open) && /open/i.test(open), open);
  assert.strictEqual(A.feedSilenceNote({ lastEventAt: now - 3000, sessionStatus: "open" }, now), null, "not silent");
  assert.strictEqual(A.feedSilenceNote({ lastEventAt: 0 }, now), null, "never had an event");
});
