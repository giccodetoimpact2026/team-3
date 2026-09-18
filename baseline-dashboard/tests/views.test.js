// Persona views: which panels each view shows, and how a saved choice resolves.
const test = require("node:test");
const assert = require("node:assert");
const { VIEWS, resolveView, sectionsFor, isVisible } = require("../js/views.js");

test("VIEWS: five personas in display order, executive first", () => {
  assert.deepStrictEqual(VIEWS.map((v) => v.id), ["executive", "trader", "risk", "analyst", "ops"]);
  VIEWS.forEach((v) => assert.ok(v.label && v.audience, v.id + " needs a label and an audience"));
});

test("resolveView: a missing or unknown saved id falls back to executive", () => {
  assert.strictEqual(resolveView(undefined), "executive");
  assert.strictEqual(resolveView(null), "executive");
  assert.strictEqual(resolveView("nonsense"), "executive");
  assert.strictEqual(resolveView("risk"), "risk");
});

test("sectionsFor: executive shows the summary panels, not trader detail", () => {
  const s = sectionsFor("executive");
  ["cards", "heatmap", "briefing", "pnl"].forEach((id) => assert.ok(s.includes(id), "executive should show " + id));
  ["lifecycle", "chart", "positions", "risk-radar"].forEach((id) => assert.ok(!s.includes(id), "executive should hide " + id));
});

test("sectionsFor: trader shows the chart, positions, fill rates and counters", () => {
  const s = sectionsFor("trader");
  ["chart", "positions", "fillrate", "ops-strip", "pnl"].forEach((id) => assert.ok(s.includes(id), "trader should show " + id));
  assert.ok(!s.includes("lifecycle"));
});

test("sectionsFor: risk shows radar, alerts, correlation and guards", () => {
  const s = sectionsFor("risk");
  ["risk-radar", "alerts", "correlation", "guards"].forEach((id) => assert.ok(s.includes(id), "risk should show " + id));
});

test("sectionsFor: analyst shows forecast and performance; ops shows lifecycle and summary", () => {
  ["forecast", "performance"].forEach((id) => assert.ok(sectionsFor("analyst").includes(id)));
  ["lifecycle", "session-summary", "ops-strip"].forEach((id) => assert.ok(sectionsFor("ops").includes(id)));
  assert.ok(!sectionsFor("ops").includes("chart"));
});

test("sectionsFor: an unknown view resolves to the executive layout", () => {
  assert.deepStrictEqual(sectionsFor("bogus"), sectionsFor("executive"));
});

test("isVisible: the assistant is visible in every view; an unknown section is hidden", () => {
  VIEWS.forEach((v) => assert.strictEqual(isVisible(v.id, "assistant"), true, "assistant in " + v.id));
  assert.strictEqual(isVisible("trader", "no-such-panel"), false);
});

test("sectionsFor: open orders are visible to trader, risk and ops, not executive", () => {
  ["trader", "risk", "ops"].forEach((v) => assert.ok(sectionsFor(v).includes("open-orders"), v + " should show open-orders"));
  assert.ok(!sectionsFor("executive").includes("open-orders"));
});

test("sectionsFor: the health strip is in every view; announcements in every view but ops", () => {
  VIEWS.forEach((v) => assert.ok(isVisible(v.id, "health"), "health in " + v.id));
  ["executive", "trader", "risk", "analyst"].forEach((v) => assert.ok(isVisible(v, "announcements"), v));
  assert.ok(!isVisible("ops", "announcements"));
});
