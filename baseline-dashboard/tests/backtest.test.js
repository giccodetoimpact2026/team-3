// Unit tests for the pure backtest view-model helpers. Run with `node --test`.
const test = require("node:test");
const assert = require("node:assert");
const Backtest = require("../js/backtest.js");

function instrument() {
  return {
    symbol: "NOVA",
    results: [
      {
        rank: 1, is_winner: true, is_current: false,
        params: { lookback_samples: 5, momentum_threshold: 0.003 },
        metrics: {
          total_pnl_cents: 1146000, realized_pnl_cents: 0, unrealized_pnl_cents: 1146000,
          trade_count: 10, sell_count: 0, win_rate: null, max_drawdown_cents: -16000,
        },
        equity_curve: [{ t: 0, equity_cents: 0 }, { t: 1, equity_cents: 500 }],
      },
      {
        rank: 5, is_winner: false, is_current: true,
        params: { lookback_samples: 10, momentum_threshold: 0.008 },
        metrics: {
          total_pnl_cents: 1109500, realized_pnl_cents: 20000, unrealized_pnl_cents: 1089500,
          trade_count: 8, sell_count: 4, win_rate: 0.75, max_drawdown_cents: -32000,
        },
        equity_curve: [{ t: 0, equity_cents: 0 }, { t: 1, equity_cents: 100 }],
      },
    ],
  };
}

test("formatParams: renders lookback and the threshold key", () => {
  assert.strictEqual(
    Backtest.formatParams({ lookback_samples: 5, momentum_threshold: 0.003 }),
    "lookback 5, thr 0.003"
  );
  assert.strictEqual(
    Backtest.formatParams({ lookback_samples: 15, deviation_threshold: 0.004 }),
    "lookback 15, thr 0.004"
  );
  assert.strictEqual(Backtest.formatParams(null), "—");
});

test("formatWinRate: N/A when no sells closed a position", () => {
  assert.strictEqual(Backtest.formatWinRate(null, 0), "N/A");
  assert.strictEqual(Backtest.formatWinRate(0, 0), "N/A");
  assert.strictEqual(Backtest.formatWinRate(0.75, 4), "75%");
});

test("backtestRows: money from cents, tags, and row classes", () => {
  const rows = Backtest.backtestRows(instrument());
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].totalPnl, "$11,460.00");
  assert.strictEqual(rows[0].winRate, "N/A");
  assert.strictEqual(rows[0].tag, "Best");
  assert.strictEqual(rows[0].cls, "row-winner");
  assert.strictEqual(rows[1].winRate, "75%");
  assert.strictEqual(rows[1].tag, "Current");
  assert.strictEqual(rows[1].cls, "row-current");
  assert.strictEqual(rows[1].maxDd, "-$320.00");
});

test("comparisonSummary: current rank and delta vs best", () => {
  const cmp = Backtest.comparisonSummary(instrument());
  assert.strictEqual(cmp.hasCurrent, true);
  assert.strictEqual(cmp.currentRank, 5);
  assert.strictEqual(cmp.total, 2);
  assert.strictEqual(cmp.deltaVsBestCents, 1146000 - 1109500);
  assert.strictEqual(cmp.betterExists, true);
});

test("metaLine: ticks, sample label, and fill model", () => {
  const doc = { sample: { ticks: 650, is_bundled_sample: true },
                fill_model: { type: "next_tick_marketable", slippage_bps: 0 } };
  assert.strictEqual(
    Backtest.metaLine(doc),
    "650 ticks · bundled sample · fill: next_tick_marketable (0 bps slippage)"
  );
});

test("equityPolyline: empty curve -> empty string", () => {
  assert.strictEqual(Backtest.equityPolyline([], { w: 100, h: 100, pad: 10 }), "");
});

test("equityPolyline: max maps to top, min to bottom, one point per tick", () => {
  const curve = [{ t: 0, equity_cents: 0 }, { t: 1, equity_cents: 50 }, { t: 2, equity_cents: 100 }];
  const box = { w: 100, h: 100, pad: 10 };
  const pts = Backtest.equityPolyline(curve, box).split(" ");
  assert.strictEqual(pts.length, 3);
  // x spans pad..w-pad
  assert.strictEqual(pts[0].split(",")[0], "10");
  assert.strictEqual(pts[2].split(",")[0], "90");
  // min equity -> bottom (h-pad = 90); max equity -> top (pad = 10)
  assert.strictEqual(pts[0].split(",")[1], "90");
  assert.strictEqual(pts[2].split(",")[1], "10");
});

test("equityPolyline: flat curve draws along the vertical middle", () => {
  const curve = [{ t: 0, equity_cents: 5 }, { t: 1, equity_cents: 5 }];
  const pts = Backtest.equityPolyline(curve, { w: 100, h: 100, pad: 10 }).split(" ");
  assert.strictEqual(pts[0].split(",")[1], "50"); // h - pad - 0.5*inner = 90 - 40 = 50
});

test("equityPolyline: shared min/max override scales both curves together", () => {
  // Curve values 0..50, but scale forced to 0..100 -> top point lands mid-high.
  const curve = [{ t: 0, equity_cents: 0 }, { t: 1, equity_cents: 50 }];
  const pts = Backtest.equityPolyline(curve, { w: 100, h: 100, pad: 10 }, 0, 100).split(" ");
  // 50 of a 0..100 span -> frac 0.5 -> y = 90 - 0.5*80 = 50
  assert.strictEqual(pts[1].split(",")[1], "50");
});

test("instrumentList and findInstrument", () => {
  const doc = { instruments: [instrument(), { symbol: "ELXR", results: [] }] };
  assert.deepStrictEqual(Backtest.instrumentList(doc), ["NOVA", "ELXR"]);
  assert.strictEqual(Backtest.findInstrument(doc, "ELXR").symbol, "ELXR");
  assert.strictEqual(Backtest.findInstrument(doc, "NOPE"), null);
});
