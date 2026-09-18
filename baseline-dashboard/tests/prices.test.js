// Price history from SSE, the short-horizon forecast, sparklines and correlation. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const { PriceHistory, forecast, sparklinePath, resample, correlation, correlationMatrix } = require("../js/prices.js");

function series(n, fn, stepMs) {
  const out = [];
  for (let i = 0; i < n; i++) out.push({ t: i * (stepMs || 1000), p: fn(i) });
  return out;
}

test("PriceHistory: keeps the newest `capacity` samples per symbol, oldest first", () => {
  const h = new PriceHistory(3);
  [1, 2, 3, 4].forEach((p, i) => h.push("NOVA", p * 100, i));
  assert.deepStrictEqual(h.samples("NOVA"), [{ t: 1, p: 200 }, { t: 2, p: 300 }, { t: 3, p: 400 }]);
  assert.deepStrictEqual(h.samples("ELXR"), []);
});

test("PriceHistory: ignores missing or non-positive prices", () => {
  const h = new PriceHistory(10);
  h.push("NOVA", 0, 1);
  h.push("NOVA", undefined, 2);
  h.push("NOVA", -5, 3);
  assert.deepStrictEqual(h.samples("NOVA"), []);
});

test("PriceHistory: last returns the newest sample or null; reset clears every symbol", () => {
  const h = new PriceHistory(10);
  assert.strictEqual(h.last("NOVA"), null);
  h.push("NOVA", 100, 5);
  h.push("AXON", 50, 6);
  assert.deepStrictEqual(h.last("NOVA"), { t: 5, p: 100 });
  assert.deepStrictEqual(h.symbols(), ["AXON", "NOVA"]);
  h.reset();
  assert.deepStrictEqual(h.symbols(), []);
});

test("forecast: null below the minimum sample count", () => {
  assert.strictEqual(forecast(series(5, () => 10000), { minSamples: 20, horizonMs: 60000 }), null);
});

test("forecast: a flat series projects the last price with a zero-width band", () => {
  const f = forecast(series(30, () => 10000), { minSamples: 20, horizonMs: 60000 });
  assert.strictEqual(f.last, 10000);
  assert.strictEqual(f.projected, 10000);
  assert.strictEqual(f.lower, 10000);
  assert.strictEqual(f.upper, 10000);
});

test("forecast: a steadily rising series projects above the last price inside its band", () => {
  const f = forecast(series(60, (i) => Math.round(10000 * Math.pow(1.001, i))), { minSamples: 20, horizonMs: 60000 });
  assert.ok(f.projected > f.last, "projected " + f.projected + " should exceed last " + f.last);
  assert.ok(f.lower <= f.projected && f.projected <= f.upper);
});

test("forecast: the band widens with the horizon", () => {
  const s = series(80, (i) => Math.round(10000 * (1 + 0.01 * Math.sin(i))));
  const short = forecast(s, { minSamples: 20, horizonMs: 60000 });
  const long = forecast(s, { minSamples: 20, horizonMs: 300000 });
  assert.ok(long.upper - long.lower > short.upper - short.lower);
});

test("forecast: reports horizon, method, sample count and a categorical confidence", () => {
  const f = forecast(series(40, () => 10000), { minSamples: 20, horizonMs: 60000 });
  assert.strictEqual(f.horizonMs, 60000);
  assert.strictEqual(f.samples, 40);
  assert.ok(/drift/i.test(f.method));
  assert.ok(["high", "medium", "low"].includes(f.confidence));
});

test("forecast: a short noisy history is low confidence; a long calm one is high", () => {
  const noisy = forecast(series(25, (i) => (i % 2 ? 10300 : 10000)), { minSamples: 20, horizonMs: 60000 });
  assert.strictEqual(noisy.confidence, "low");
  const calm = forecast(series(200, (i) => 10000 + (i % 3)), { minSamples: 20, horizonMs: 60000 });
  assert.strictEqual(calm.confidence, "high");
});

test("sparklinePath: maps samples onto the box, min at the bottom, max at the top", () => {
  assert.strictEqual(sparklinePath([{ t: 0, p: 1 }, { t: 1, p: 2 }, { t: 2, p: 3 }], 100, 20), "M0,20 L50,10 L100,0");
});

test("sparklinePath: fewer than two samples gives an empty path; a flat series sits mid-height", () => {
  assert.strictEqual(sparklinePath([], 100, 20), "");
  assert.strictEqual(sparklinePath([{ t: 0, p: 5 }], 100, 20), "");
  assert.strictEqual(sparklinePath([{ t: 0, p: 5 }, { t: 1, p: 5 }], 100, 20), "M0,10 L100,10");
});

test("resample: last price per bucket, carried forward through empty buckets", () => {
  const s = [{ t: 0, p: 1 }, { t: 1000, p: 2 }, { t: 7000, p: 3 }];
  assert.deepStrictEqual(resample(s, 2000, 4, 8000), [2, 2, 2, 3]);
});

test("resample: buckets before the first sample are null", () => {
  assert.deepStrictEqual(resample([{ t: 5000, p: 9 }], 2000, 4, 8000), [null, null, 9, 9]);
});

test("correlation: identical return series is 1, mirrored is -1, too short is null", () => {
  const a = [1, 2, 3, 5, 4, 6];
  const b = [2, 4, 6, 10, 8, 12];
  const c = [6, 5, 4, 2, 3, 1];
  assert.ok(Math.abs(correlation(a, b) - 1) < 1e-9);
  assert.ok(Math.abs(correlation(a, c) + 1) < 1e-9);
  assert.strictEqual(correlation([1, 2], [2, 3]), null);
});

test("correlationMatrix: symmetric with a unit diagonal, null where a series is too short", () => {
  const h = new PriceHistory(500);
  for (let i = 0; i < 40; i++) {
    const p = 10000 + Math.round(100 * Math.sin(i));
    h.push("NOVA", p, i * 1000);
    h.push("AXON", p * 2, i * 1000);
  }
  h.push("DRFT", 5000, 39000);
  const m = correlationMatrix(h, ["NOVA", "AXON", "DRFT"], { bucketMs: 1000, buckets: 30, now: 39000 });
  assert.deepStrictEqual(m.symbols, ["NOVA", "AXON", "DRFT"]);
  assert.strictEqual(m.matrix[0][0], 1);
  assert.ok(Math.abs(m.matrix[0][1] - 1) < 1e-6);
  assert.strictEqual(m.matrix[0][1], m.matrix[1][0]);
  assert.strictEqual(m.matrix[0][2], null);
});
