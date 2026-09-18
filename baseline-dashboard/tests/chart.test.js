// The price chart is an SVG string built from samples, our fills and a forecast. No DOM.
const test = require("node:test");
const assert = require("node:assert");
const { chartSvg } = require("../js/chart.js");

const samples = [];
for (let i = 0; i < 10; i++) samples.push({ t: 1758182400000 + i * 1000, p: 10000 + i * 10 });

test("chartSvg: no samples renders a placeholder message and no price path", () => {
  const svg = chartSvg({ symbol: "NOVA", samples: [], width: 600, height: 200 });
  assert.ok(svg.includes("<svg"));
  assert.ok(/Waiting for price/.test(svg));
  assert.ok(!svg.includes('class="price"'));
});

test("chartSvg: draws one price path through the samples with dollar y-axis labels", () => {
  const svg = chartSvg({ symbol: "NOVA", samples, width: 600, height: 200 });
  assert.strictEqual((svg.match(/class="price"/g) || []).length, 1);
  assert.ok(svg.includes("$100.90"), "top label");
  assert.ok(svg.includes("$100.00"), "bottom label");
});

test("chartSvg: marks our fills as buy and sell dots with a tooltip", () => {
  const fills = [
    { t: 1758182402000, p: 10020, side: "buy", quantity: 100 },
    { t: 1758182405000, p: 10050, side: "sell", quantity: 50 },
  ];
  const svg = chartSvg({ symbol: "NOVA", samples, fills, width: 600, height: 200 });
  assert.ok(svg.includes('class="fill buy"'));
  assert.ok(svg.includes('class="fill sell"'));
  assert.ok(svg.includes("<title>BUY 100 @ $100.20"));
});

test("chartSvg: draws the forecast cone and projected line when a forecast is given", () => {
  const f = { last: 10090, projected: 10120, lower: 10050, upper: 10190, horizonMs: 60000, confidence: "medium" };
  const svg = chartSvg({ symbol: "NOVA", samples, forecast: f, width: 600, height: 200 });
  assert.ok(svg.includes('class="cone"'));
  assert.ok(svg.includes('class="projected"'));
  assert.ok(svg.includes("$101.90"), "y range grows to include the band top");
});

test("chartSvg: escapes the symbol", () => {
  const svg = chartSvg({ symbol: "<x>", samples, width: 600, height: 200 });
  assert.ok(!svg.includes("<x>"));
  assert.ok(svg.includes("&lt;x&gt;"));
});

test("chartSvg: draws each resting order as a dashed line at its limit price with a label", () => {
  const orders = [{ side: "buy", price: 10040, remaining: 100 }, { side: "sell", price: 10200, remaining: 50 }];
  const svg = chartSvg({ symbol: "NOVA", samples, orders, width: 600, height: 200 });
  assert.strictEqual((svg.match(/class="resting buy"/g) || []).length, 1);
  assert.strictEqual((svg.match(/class="resting sell"/g) || []).length, 1);
  assert.ok(svg.includes("BUY 100 @ $100.40"));
  assert.ok(svg.includes("$102.00"), "y range grows to include the resting sell");
});
