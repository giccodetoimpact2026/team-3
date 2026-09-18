"""CLI: replay a recorded feed through a parameter sweep and write results.json.

    python run_backtest.py [--input FILE] [--output FILE] [--session SID]
                           [--slippage-bps N]

Defaults replay the bundled sample (recordings/sample_feed.jsonl) so the command
works with zero setup. The output JSON is the contract the dashboard reads; copy
it to baseline-dashboard/data/backtest.json to surface it in the UI.

Config comes from config.yaml directly (no exchange key needed -- a backtest
places no orders).
"""
import argparse
import json
import os
from datetime import datetime, timezone

import yaml

from backtest.loader import load_series
from backtest.sweep import run_all

HERE = os.path.dirname(__file__)
DEFAULT_INPUT = os.path.join(HERE, "recordings", "sample_feed.jsonl")
DEFAULT_OUTPUT = os.path.join(HERE, "recordings", "results.json")

CAVEATS = [
    "Single session, small sample - directional only, not statistically significant.",
    "Fills are simulated from a price-only series: no order book, no partial "
    "fills, no liquidity limit. Results are optimistic and flatter large sizes.",
    "Next-tick fill avoids look-ahead but cannot model real market impact.",
    "Hypothesis generation, not a recommendation. Sandbox data only; not advice.",
]


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


def _duration_seconds(series_by_symbol):
    stamps = []
    for series in series_by_symbol.values():
        for _, ts, _ in series:
            if ts:
                try:
                    stamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                except ValueError:
                    pass
    if len(stamps) < 2:
        return None
    return round((max(stamps) - min(stamps)).total_seconds())


def build_results(config, segment, source_file, slippage_bps):
    series_by_symbol = segment["series"]
    instruments = run_all(config, series_by_symbol, slippage_bps)
    total_ticks = sum(len(s) for s in series_by_symbol.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": os.path.basename(source_file),
        "session_id": segment["session_id"],
        "sample": {
            "ticks": total_ticks,
            "duration_seconds": _duration_seconds(series_by_symbol),
            "is_bundled_sample": os.path.abspath(source_file) == os.path.abspath(DEFAULT_INPUT),
        },
        "fill_model": {"type": "next_tick_marketable", "slippage_bps": slippage_bps},
        "caveats": CAVEATS,
        "instruments": instruments,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--session", default=None, help="session id to replay (default: last)")
    ap.add_argument("--slippage-bps", type=int, default=0)
    args = ap.parse_args()

    config = load_config()
    segment = load_series(args.input, args.session)
    if not segment["series"]:
        raise SystemExit(f"no price data found in {args.input}")

    results = build_results(config, segment, args.input, args.slippage_bps)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    n_inst = len(results["instruments"])
    print(f"backtest: {results['sample']['ticks']} ticks, {n_inst} instruments "
          f"-> {args.output}")
    for inst in results["instruments"]:
        winner = inst.get("winner_params")
        cur = next((r for r in inst["results"] if r["is_current"]), None)
        cur_pnl = cur["metrics"]["total_pnl_cents"] / 100 if cur else 0
        best = inst["results"][0]["metrics"]["total_pnl_cents"] / 100
        print(f"  {inst['symbol']:5} current ${cur_pnl:>9,.2f} | "
              f"best ${best:>9,.2f} @ {winner}")


if __name__ == "__main__":
    main()
