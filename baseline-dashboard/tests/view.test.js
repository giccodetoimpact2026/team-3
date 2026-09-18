// Pure view-model logic: what the table shows for a given portfolio, and how
// the SSE cursor and session boundary behave. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const { positionRows, FeedCursor, botStatusView, haltResultText, guardRows, guardSummary } = require("../js/view.js");

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

test("botStatusView: RUNNING with a fresh heartbeat enables the halt button", () => {
  const v = botStatusView({ state: "RUNNING", heartbeat_age_s: 2.0, feed_state: "ALIVE", feed_age_s: 0.4, orders: { active_orders: 1, unknown_orders: 0 } });
  assert.strictEqual(v.label, "BOT RUNNING");
  assert.strictEqual(v.cls, "running");
  assert.strictEqual(v.canHalt, true);
  assert.match(v.detail, /feed alive \(0\.4s since last event\)/);
  assert.match(v.detail, /heartbeat 2s ago/);
  assert.match(v.detail, /1 resting, 0 unknown/);
});

test("botStatusView: more than 15 s without a heartbeat is DEAD even though the endpoint answered", () => {
  const v = botStatusView({ state: "RUNNING", heartbeat_age_s: 15.1, feed_state: "ALIVE" });
  assert.strictEqual(v.label, "BOT DEAD");
  assert.strictEqual(v.cls, "dead");
  assert.strictEqual(v.canHalt, true); // still try to cancel what can be cancelled
  assert.match(v.detail, /no heartbeat for 15s/);
  assert.strictEqual(botStatusView({ state: "RUNNING", heartbeat_age_s: 15.0 }).cls, "running");
});

test("botStatusView: a stalled or quiet feed shows on the pill without marking the bot dead", () => {
  assert.strictEqual(botStatusView({ state: "RUNNING", heartbeat_age_s: 1, feed_state: "DEGRADED" }).label, "BOT RUNNING · FEED DEGRADED");
  const v = botStatusView({ state: "RUNNING", heartbeat_age_s: 1, feed_state: "DEAD", feed_age_s: 33 });
  assert.strictEqual(v.label, "BOT RUNNING · FEED DEAD");
  assert.strictEqual(v.cls, "degraded");
});

test("botStatusView: an order-rate pause is shown with its reason", () => {
  const v = botStatusView({ state: "RUNNING", heartbeat_age_s: 1, feed_state: "ALIVE", orders: { active_orders: 0, unknown_orders: 0, paused: true, pause_reason: "order-rate anomaly: more than 20 submits in 60s", pause_remaining_s: 42 } });
  assert.strictEqual(v.label, "BOT PAUSED");
  assert.match(v.detail, /paused: order-rate anomaly.*42s left/);
});

test("botStatusView: a RUNNING bot without heartbeat fields (older bot) is still RUNNING", () => {
  assert.strictEqual(botStatusView({ state: "RUNNING" }).label, "BOT RUNNING");
});

test("botStatusView: QUIESCING and STOPPED disable the button", () => {
  const q = botStatusView({ state: "QUIESCING" });
  assert.deepStrictEqual([q.label, q.cls, q.canHalt], ["BOT HALTING…", "quiescing", false]);
  const s = botStatusView({ state: "STOPPED" });
  assert.deepStrictEqual([s.label, s.cls, s.canHalt], ["BOT HALTED", "stopped", false]);
});

test("botStatusView: no answer, unknown state or bad payload is OFFLINE", () => {
  [null, {}, { state: "weird" }].forEach((x) => {
    const v = botStatusView(x);
    assert.deepStrictEqual([v.label, v.cls, v.canHalt], ["BOT OFFLINE", "offline", false]);
  });
});

test("haltResultText: reports unresolved cancellations, singular and plural", () => {
  assert.strictEqual(haltResultText({ state: "STOPPED", unresolved: 0 }), "Halted. All open orders cancelled.");
  assert.strictEqual(haltResultText({ state: "STOPPED" }), "Halted. All open orders cancelled.");
  assert.strictEqual(haltResultText({ unresolved: 1 }), "Halted. 1 order could not be confirmed cancelled; check the exchange.");
  assert.strictEqual(haltResultText({ unresolved: 3 }), "Halted. 3 orders could not be confirmed cancelled; check the exchange.");
});

test("guardRows: one row per symbol with crash-guard state", () => {
  const status = {
    crash_guard: { drop_pct: 0.15, confirm_ticks: 3, floor_pct: 0.03, cooldown_seconds: 300,
      NOVA: { high: 15075, breaches: 0, cooldown: false, cooldown_remaining_s: 0 },
      DRFT: { high: 6000, breaches: 2, cooldown: false, cooldown_remaining_s: 0 },
      AXON: { high: 9000, breaches: 0, cooldown: true, cooldown_remaining_s: 120.4 } },
  };
  assert.deepStrictEqual(guardRows(status), [
    { symbol: "AXON", high: "$90.00", guard: "DUMPED · cooldown 120s" },
    { symbol: "DRFT", high: "$60.00", guard: "ARMED · 2 breaches" },
    { symbol: "NOVA", high: "$150.75", guard: "ARMED" },
  ]);
  assert.strictEqual(guardSummary(status), "crash guard: sell all on 15% drop over 3 ticks, floor 3%");
});

test("guardRows: older bot without the blocks renders nothing, not undefined", () => {
  assert.deepStrictEqual(guardRows({ state: "RUNNING" }), []);
  assert.deepStrictEqual(guardRows(null), []);
  assert.strictEqual(guardSummary(null), "crash guard: off");
});
