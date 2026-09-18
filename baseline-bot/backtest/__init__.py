"""Parameter-sweep backtest for the baseline bot.

Replays a recorded price series (see `record_feed.py`) through the *real*
strategy classes (via `main.build_strategy`), so the backtest is provably the
same signal logic as the live bot. It only substitutes the parts live infra
provides: price ticks, the held position, simulated fills, and P&L accounting.

This is hypothesis generation, not validation and not an auto-tuner. Results are
never written back into `config.yaml`. See the module docstrings in `fill.py`
(fill-model assumptions) and `engine.py` (P&L accounting) for the caveats.
"""
