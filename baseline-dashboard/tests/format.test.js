// Starter unit tests — run with `node --test` (no dependencies).
const test = require("node:test");
const assert = require("node:assert");
const { formatCurrency } = require("../js/format.js");

test("formatCurrency: cents render as dollars with grouping", () => {
  assert.strictEqual(formatCurrency(151000), "$1,510.00");
  assert.strictEqual(formatCurrency(32000), "$320.00");
});

test("formatCurrency: zero and sub-dollar", () => {
  assert.strictEqual(formatCurrency(0), "$0.00");
  assert.strictEqual(formatCurrency(5), "$0.05");
});

test("formatCurrency: negatives keep the sign", () => {
  assert.strictEqual(formatCurrency(-124050), "-$1,240.50");
});

test("formatCurrency: missing/undefined reads as zero, not NaN", () => {
  assert.strictEqual(formatCurrency(undefined), "$0.00");
  assert.strictEqual(formatCurrency(null), "$0.00");
});

const { formatSgt, formatCurrencyOrDash } = require("../js/format.js");

test("formatSgt: fixed UTC instant renders as YYYY-MM-DD HH:MM:SS.mmm SGT", () => {
  assert.strictEqual(formatSgt(new Date("2026-09-18T01:02:03.456Z")), "2026-09-18 09:02:03.456 SGT");
});

test("formatSgt: crosses the date line when UTC+8 rolls the day", () => {
  assert.strictEqual(formatSgt(new Date("2026-12-31T20:00:00.000Z")), "2027-01-01 04:00:00.000 SGT");
});

test("formatSgt: accepts an ISO string with sub-millisecond precision", () => {
  assert.strictEqual(formatSgt("2026-09-18T01:18:59.177387787Z"), "2026-09-18 09:18:59.177 SGT");
});

test("formatCurrencyOrDash: missing value renders as a dash, not $0.00", () => {
  assert.strictEqual(formatCurrencyOrDash(undefined), "—");
  assert.strictEqual(formatCurrencyOrDash(null), "—");
  assert.strictEqual(formatCurrencyOrDash(0), "$0.00");
  assert.strictEqual(formatCurrencyOrDash(151000), "$1,510.00");
});

const { centsToDecimal, formatDollars } = require("../js/format.js");

test("centsToDecimal: integer cents become a plain two-decimal string with a period", () => {
  assert.strictEqual(centsToDecimal(13628), "136.28");
  assert.strictEqual(centsToDecimal(5), "0.05");
  assert.strictEqual(centsToDecimal(-124050), "-1240.50");
  assert.strictEqual(centsToDecimal(0), "0.00");
});

test("formatDollars: MCP dollar floats render like formatCurrency", () => {
  assert.strictEqual(formatDollars(-1240.5), "-$1,240.50");
  assert.strictEqual(formatDollars(320), "$320.00");
  assert.strictEqual(formatDollars(undefined), "$0.00");
});
