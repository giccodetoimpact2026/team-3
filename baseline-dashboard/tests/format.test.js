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
