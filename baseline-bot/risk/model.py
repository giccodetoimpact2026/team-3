"""Instrument risk model: sizing, stop-loss, drawdown and exposure limits.

Design: docs/risk-model-design.md. The model is pure bookkeeping over data the
bot already has (SSE prices and books through `MarketStats`, the portfolio
through `OrderManager.refresh_positions`), so it adds no REST calls on the
tick path. It answers four questions:

  adjust_intent()  how much of a buy may go out, and why it was reduced
  on_price()       whether a held position has crossed its stop
  mode / paused()  whether the portfolio or one instrument is inside limits
  status()         what the desk should see

Sells are never reduced: cutting exposure is always allowed, subject only to
the long-only availability check that lives in RiskLimits.

Money and prices are integer cents. Percentages are fractions. All
window-scoped state (baseline equity, mode, pauses, cooldowns, alerts) resets
with the session id, not with the process.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from exchange.client import positions_by_symbol
from risk.profiles import load_profiles
from risk.stats import MarketStats
from utils.logger import log

NORMAL = "NORMAL"
DEFENSIVE = "DEFENSIVE"

ALERT_HISTORY = 100


@dataclass(frozen=True)
class StopTrigger:
    symbol: str
    price: int          # the tick that confirmed the breach
    level: int          # stop level in cents
    reference: int      # cost basis (or session-open price) the stop hangs from
    stop_pct: float     # stop distance as a fraction of the reference


class RiskModel:
    def __init__(self, profiles, stats, limits, cfg, clock=time.monotonic,
                 wall_clock=None, halt=None):
        self._profiles = dict(profiles)
        self._stats = stats
        self._limits = limits
        self.profiles = self._profiles
        self.stats = stats
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._halt = halt
        self._lock = threading.RLock()

        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.size_mode = str(cfg.get("size_mode", "cap"))
        self.risk_per_trade_pct = float(cfg.get("risk_per_trade_pct", 0.005))
        self.max_notional_pct = float(cfg.get("max_notional_per_symbol_pct", 0.35))
        self.max_gross_pct = float(cfg.get("max_gross_exposure_pct", 0.90))
        self.instrument_loss_pct = float(cfg.get("instrument_loss_limit_pct", 0.02))
        dd = cfg.get("drawdown") or {}
        self.drawdown_warn_pct = float(dd.get("warn_pct", 0.10))
        self.drawdown_limit_pct = float(dd.get("limit_pct", 0.15))
        self.drawdown_action = str(dd.get("action", "block_buys"))
        self.stop_loss_enabled = bool((cfg.get("stop_loss") or {}).get("enabled", True))
        self.correlation_alert = float(cfg.get("correlation_alert", 0.7))
        self._regime_factors = cfg.get("regime_factors") or {}
        self._groups = {
            name: {"members": list((g or {}).get("members") or []),
                   "cap_pct": float((g or {}).get("max_exposure_pct", 1.0))}
            for name, g in (cfg.get("groups") or {}).items()
        }
        self.reset_for_session()

    # --- lifecycle -------------------------------------------------------

    def reset_for_session(self) -> None:
        if self._stats is not None:
            self._stats.reset_for_session()
        with self._lock:
            # symbol -> quantity, reserved_qty, avg_cost, last_price, realized_pnl
            self._pos = {}
            self._marks = {}          # symbol -> last price seen through on_price()
            self._cash = 0
            self._baseline = None
            self._peak = None
            self._mode = NORMAL
            self._halted = False
            self._paused = {}         # symbol -> reason
            self._stop = {s: {"breaches": 0, "cooldown_until": 0.0, "level": None}
                          for s in self._profiles}
            self._alerts = deque(maxlen=ALERT_HISTORY)
            self._alert_seq = 0
            self._latched = set()     # (kind, key) alerts raised this window
            self._last_decision = {}

    # --- RiskLimits protocol (delegated) ---------------------------------

    def max_position(self, symbol: str) -> int:
        return self._limits.max_position(symbol)

    def allows_buy(self, symbol: str, projected_position: int, quantity: int) -> bool:
        return self._limits.allows_buy(symbol, projected_position, quantity)

    def allows_sell(self, symbol: str, available: int, quantity: int) -> bool:
        return self._limits.allows_sell(symbol, available, quantity)

    # Correlated-group notional caps (BUG-008) stay in RiskLimits; OrderManager
    # asks its risk policy for them, so the model passes them through.

    def group_for(self, symbol: str):
        return self._limits.group_for(symbol)

    def group_symbols(self, name: str) -> list:
        return self._limits.group_symbols(name)

    def max_group_notional(self, name: str):
        return self._limits.max_group_notional(name)

    def groups(self) -> dict:
        return self._limits.groups()

    def allows_group_buy(self, group: str, projected_notional: int,
                         added_notional: int) -> bool:
        return self._limits.allows_group_buy(group, projected_notional, added_notional)

    # --- inputs ----------------------------------------------------------

    def on_portfolio(self, portfolio: dict) -> None:
        """Called by OrderManager on every portfolio refresh (at most 1/s)."""
        if not self.enabled:
            return
        with self._lock:
            by_symbol = positions_by_symbol(portfolio)
            self._pos = {s: d for s, d in by_symbol.items()}
            self._cash = int((portfolio or {}).get("cash") or 0)
            equity = self._equity()
            if self._baseline is None and equity > 0:
                self._baseline = equity
                self._peak = equity
            self._evaluate()
            for symbol in list(self._pos):
                self._check_instrument_loss(symbol)

    def on_price(self, symbol: str, price):
        """Feed one price tick for `symbol` (after MarketStats has seen it).

        Re-marks equity, re-checks the drawdown and instrument-loss limits,
        and returns a StopTrigger when the symbol's stop is confirmed.
        """
        if not self.enabled:
            return None
        try:
            price = int(price)
        except (TypeError, ValueError):
            return None
        if price <= 0 or symbol not in self._profiles:
            return None
        with self._lock:
            self._marks[symbol] = price
            self._evaluate()
            self._check_instrument_loss(symbol)
            self._check_correlation(symbol)
            return self._check_stop(symbol, price)

    # --- sizing ----------------------------------------------------------

    def adjust_intent(self, symbol: str, side: str, quantity: int, price: int,
                      projected_position: int, source=None):
        """(quantity, reason). Reduced or refused buys carry the binding reason.

        Runs after OrderManager's own gates. Sells pass through unchanged.
        """
        if not self.enabled or side != "buy":
            return quantity, None
        with self._lock:
            qty, reason = self._size_buy(symbol, int(quantity), int(price),
                                         int(projected_position), source)
            self._last_decision[symbol] = {
                "side": side, "source": source, "requested": int(quantity),
                "quantity": qty, "reason": reason,
            }
            if qty == 0 or (reason and not reason.startswith(("size_multiplier", "regime"))):
                log(f"risk: {symbol} buy {quantity}@{price} -> {qty} ({reason})")
            return qty, reason

    def _size_buy(self, symbol, quantity, price, projected, source):
        profile = self._profiles.get(symbol)
        if profile is None:
            return quantity, None
        if self._mode == DEFENSIVE:
            return 0, "drawdown limit: buys blocked"
        if symbol in self._paused:
            return 0, f"instrument paused: {self._paused[symbol]}"
        if self._in_cooldown(symbol):
            return 0, "stop-loss cooldown"

        regime = self._stats.regime(symbol) if self._stats is not None else None
        factor = self._regime_factor(source, regime)
        if factor <= 0:
            return 0, f"regime {regime}: {source} suspended"

        equity = self._equity()
        stop_distance = self._stop_distance(symbol, price)
        vol_cap = (equity * self.risk_per_trade_pct * profile.size_multiplier / stop_distance
                   if stop_distance > 0 else 0.0)

        base_reasons = []
        if self.size_mode == "target":
            base = vol_cap * factor
            base_reasons.append(f"vol_cap={int(vol_cap)}")
        else:
            base = quantity * profile.size_multiplier * factor
            if profile.size_multiplier != 1.0:
                base_reasons.append(f"size_multiplier={profile.size_multiplier:g}")
        if factor != 1.0:
            base_reasons.append(f"regime {regime} x{factor:g}")

        depth = self._stats.depth(symbol) if self._stats is not None else None
        if depth is not None:
            depth_cap = profile.depth_fraction * depth["ask_depth"]
        else:
            depth_cap = float(profile.max_qty_without_book)

        notional_cap = (self.max_notional_pct * equity - projected * price) / price
        gross_cap = (self.max_gross_pct * equity - self._exposure(exclude=symbol)
                     - projected * price) / price

        candidates = [
            (", ".join(base_reasons) if base_reasons else None, base),
            (f"vol_cap={int(vol_cap)}", vol_cap),
            (f"depth_cap={int(depth_cap)}", depth_cap),
            (f"notional_cap={max(0, int(notional_cap))}", notional_cap),
        ]
        if profile.group in self._groups:
            group = self._groups[profile.group]
            others = sum(self._notional(m) for m in group["members"] if m != symbol)
            group_cap = (group["cap_pct"] * equity - others - projected * price) / price
            candidates.append((f"group_cap={max(0, int(group_cap))} ({profile.group})",
                               group_cap))
        candidates.append((f"gross_cap={max(0, int(gross_cap))}", gross_cap))

        reason, bound = candidates[0]
        for cand_reason, cand in candidates[1:]:
            if cand < bound:
                reason, bound = cand_reason, cand
        qty = max(0, int(bound))
        if qty >= quantity and self.size_mode != "target":
            return quantity, None
        return qty, reason

    def _regime_factor(self, source, regime) -> float:
        if regime is None:
            return 1.0
        table = self._regime_factors.get(source) if source else None
        if table is None:
            table = self._regime_factors.get("default") or {}
        try:
            return float(table.get(regime, 1.0))
        except (TypeError, ValueError):
            return 1.0

    # --- equity and exposure ------------------------------------------------

    def _mark(self, symbol) -> int:
        price = self._stats.last_price(symbol) if self._stats is not None else None
        if price is None:
            price = self._marks.get(symbol)
        if price is None:
            price = (self._pos.get(symbol) or {}).get("last_price") or 0
        return int(price)

    def _quantity(self, symbol) -> int:
        return int((self._pos.get(symbol) or {}).get("quantity") or 0)

    def _notional(self, symbol) -> int:
        return self._quantity(symbol) * self._mark(symbol)

    def _exposure(self, exclude=None) -> int:
        return sum(self._notional(s) for s in self._pos if s != exclude)

    def _equity(self) -> int:
        return self._cash + self._exposure()

    def equity(self) -> int:
        with self._lock:
            return self._equity()

    @property
    def baseline_equity(self):
        return self._baseline

    @property
    def peak_equity(self):
        return self._peak

    @property
    def mode(self) -> str:
        return self._mode

    # --- drawdown ------------------------------------------------------------

    def _evaluate(self) -> None:
        if self._baseline is None:
            return
        equity = self._equity()
        self._peak = max(self._peak or equity, equity)
        drawdown = (self._baseline - equity) / self._baseline
        if drawdown >= self.drawdown_warn_pct:
            self._alert("drawdown_warn", None, "warn",
                        f"portfolio drawdown {drawdown:.1%} of starting equity "
                        f"(warn at {self.drawdown_warn_pct:.0%})")
        if drawdown >= self.drawdown_limit_pct and ("drawdown_limit", None) not in self._latched:
            self._alert("drawdown_limit", None, "critical",
                        f"portfolio drawdown {drawdown:.1%} of starting equity "
                        f"(limit {self.drawdown_limit_pct:.0%}); action {self.drawdown_action}")
            if self.drawdown_action in ("block_buys", "halt"):
                self._mode = DEFENSIVE
            if self.drawdown_action == "halt" and self._halt is not None and not self._halted:
                self._halted = True
                try:
                    self._halt()
                except Exception as exc:  # the alert already records the breach
                    log(f"risk: halt callback failed: {exc}")

    # --- instrument loss limit -----------------------------------------------

    def _session_pnl(self, symbol) -> int:
        pos = self._pos.get(symbol) or {}
        realized = int(pos.get("realized_pnl") or 0)
        qty = int(pos.get("quantity") or 0)
        if qty <= 0:
            return realized
        ref = self._reference(symbol)
        if ref is None:
            return realized
        return realized + qty * (self._mark(symbol) - ref)

    def _check_instrument_loss(self, symbol) -> None:
        if self._baseline is None or symbol in self._paused or symbol not in self._profiles:
            return
        pnl = self._session_pnl(symbol)
        limit = self.instrument_loss_pct * self._baseline
        if pnl <= -limit:
            reason = (f"session loss {pnl / 100:,.2f} beyond "
                      f"{self.instrument_loss_pct:.0%} of starting equity")
            self._paused[symbol] = reason
            self._alert("instrument_loss", symbol, "critical",
                        f"{symbol} {reason}; new buys paused")

    def paused(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._paused

    def pause(self, symbol: str, reason: str = "manual") -> None:
        with self._lock:
            self._paused[symbol] = reason

    def resume(self, symbol: str) -> None:
        with self._lock:
            self._paused.pop(symbol, None)

    # --- stop-loss -------------------------------------------------------

    def _reference(self, symbol):
        """Cost basis, or the session-open price for a granted position with none."""
        pos = self._pos.get(symbol) or {}
        avg_cost = int(pos.get("avg_cost") or 0)
        if avg_cost > 0:
            return avg_cost
        if self._stats is not None:
            open_price = self._stats.session_open_price(symbol)
            if open_price:
                return int(open_price)
        return None

    def _stop_distance(self, symbol, reference) -> float:
        profile = self._profiles[symbol]
        atr = self._stats.atr(symbol) if self._stats is not None else None
        floor = profile.min_stop_pct * reference
        ceiling = profile.max_stop_pct * reference
        if atr is None:
            return ceiling
        return min(max(profile.stop_atr_mult * atr, floor), ceiling)

    def _stop_level(self, symbol):
        if not self.stop_loss_enabled or self._quantity(symbol) <= 0:
            return None
        ref = self._reference(symbol)
        if ref is None:
            return None
        return int(ref - self._stop_distance(symbol, ref)), ref

    def stop_level(self, symbol: str):
        with self._lock:
            found = self._stop_level(symbol)
            return None if found is None else found[0]

    def _in_cooldown(self, symbol) -> bool:
        st = self._stop.get(symbol)
        return bool(st) and self._clock() < st["cooldown_until"]

    def allows_entry(self, symbol: str) -> bool:
        """False while the symbol is paused, cooling down, or buys are blocked."""
        if not self.enabled:
            return True
        with self._lock:
            return (self._mode != DEFENSIVE and symbol not in self._paused
                    and not self._in_cooldown(symbol))

    def _check_stop(self, symbol, price):
        st = self._stop.get(symbol)
        if st is None or self._in_cooldown(symbol):
            return None
        found = self._stop_level(symbol)
        if found is None:
            st["breaches"] = 0
            st["level"] = None
            return None
        level, ref = found
        st["level"] = level
        if price > level:
            st["breaches"] = 0
            return None
        st["breaches"] += 1
        profile = self._profiles[symbol]
        if st["breaches"] < profile.confirm_ticks:
            return None
        st["breaches"] = 0
        st["cooldown_until"] = self._clock() + profile.cooldown_seconds
        stop_pct = (ref - level) / ref
        self._alert("stop_loss", symbol, "critical",
                    f"{symbol} at {price} is below its stop {level} "
                    f"({stop_pct:.1%} under cost {ref}); flattening, "
                    f"{profile.cooldown_seconds:.0f}s cooldown")
        return StopTrigger(symbol, price, level, ref, stop_pct)

    # --- correlation alert -------------------------------------------------

    def _check_correlation(self, symbol) -> None:
        if self._stats is None or self._quantity(symbol) <= 0:
            return
        group = self._profiles[symbol].group
        for other in self._pos:
            if other == symbol or other not in self._profiles or self._quantity(other) <= 0:
                continue
            if group is not None and self._profiles[other].group == group:
                continue
            order = list(self._profiles)
            key = "/".join(sorted((symbol, other), key=order.index))
            if ("correlation", key) in self._latched:
                continue
            corr = self._stats.correlation(symbol, other)
            if corr is not None and corr >= self.correlation_alert:
                self._alert("correlation", key, "warn",
                            f"{key} return correlation {corr:.2f} while both are held "
                            f"and not in a configured group")

    # --- alerts ----------------------------------------------------------

    def _alert(self, kind, key, level, message) -> None:
        if (kind, key) in self._latched:
            return
        self._latched.add((kind, key))
        self._alert_seq += 1
        entry = {
            "seq": self._alert_seq,
            "at": self._wall_clock().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "kind": kind,
            "symbol": key if key and "/" not in key else None,
            "message": message,
        }
        self._alerts.append(entry)
        log(f"risk alert [{level}] {message}")

    def alerts(self, since: int = 0) -> list:
        with self._lock:
            return [dict(a) for a in self._alerts if a["seq"] > int(since)]

    # --- status ----------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            equity = self._equity()
            exposure = self._exposure()
            risk_at_stops = 0
            instruments = {}
            now = self._clock()
            for symbol, profile in self._profiles.items():
                found = self._stop_level(symbol)
                level = ref = None
                stop_pct = None
                qty = self._quantity(symbol)
                if found is not None:
                    level, ref = found
                    stop_pct = (ref - level) / ref
                    risk_at_stops += qty * (ref - level)
                mark = self._mark(symbol)
                st = self._stop[symbol]
                instruments[symbol] = {
                    "personality": profile.personality,
                    "size_multiplier": profile.size_multiplier,
                    "liquidity": profile.liquidity,
                    "group": profile.group,
                    "regime": self._stats.regime(symbol) if self._stats is not None else None,
                    "atr_pct": self._stats.atr_pct(symbol) if self._stats is not None else None,
                    "quantity": qty,
                    "mark": mark or None,
                    "reference": ref,
                    "stop_level": level,
                    "stop_pct": stop_pct,
                    "loss_pct": ((ref - mark) / ref if ref and mark and qty > 0 else None),
                    "session_pnl": self._session_pnl(symbol),
                    "paused": symbol in self._paused,
                    "pause_reason": self._paused.get(symbol),
                    "cooldown_remaining_s": round(max(0.0, st["cooldown_until"] - now), 1),
                    "last_decision": self._last_decision.get(symbol),
                }
            groups = {}
            for name, group in self._groups.items():
                held = sum(self._notional(m) for m in group["members"])
                groups[name] = {
                    "members": list(group["members"]),
                    "exposure_pct": held / equity if equity else None,
                    "cap_pct": group["cap_pct"],
                }
            return {
                "enabled": self.enabled,
                "mode": self._mode,
                "size_mode": self.size_mode,
                "equity": equity,
                "baseline_equity": self._baseline,
                "peak_equity": self._peak,
                "drawdown_start_pct": ((self._baseline - equity) / self._baseline
                                       if self._baseline else None),
                "drawdown_peak_pct": ((self._peak - equity) / self._peak
                                      if self._peak else None),
                "drawdown_warn_pct": self.drawdown_warn_pct,
                "drawdown_limit_pct": self.drawdown_limit_pct,
                "drawdown_action": self.drawdown_action,
                "gross_exposure_pct": exposure / equity if equity else None,
                "max_gross_exposure_pct": self.max_gross_pct,
                "risk_at_stops_pct": risk_at_stops / equity if equity else None,
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "instrument_loss_limit_pct": self.instrument_loss_pct,
                "stop_loss_enabled": self.stop_loss_enabled,
                "groups": groups,
                # Share caps from RiskLimits, so the dashboard's Risk view reads
                # one payload (Priya: parameters visible, not buried in config).
                "max_position": {s: self._limits.max_position(s) for s in self._profiles},
                "instruments": instruments,
                "correlation": (self._stats.correlation_matrix()
                                if self._stats is not None else {}),
                "alerts": [dict(a) for a in self._alerts],
            }


def build_risk_model(cfg: dict, symbols, limits, clock=time.monotonic, halt=None) -> RiskModel:
    """Wire profiles, MarketStats and the model from the `risk_model` config."""
    cfg = cfg or {}
    profiles = load_profiles(cfg, symbols)
    stats = MarketStats(
        symbols,
        bucket_seconds=cfg.get("bucket_seconds", 10),
        atr_buckets=cfg.get("atr_buckets", 14),
        regime_thresholds={s: (p.low_vol, p.high_vol) for s, p in profiles.items()},
        regime_confirm_buckets=cfg.get("regime_confirm_buckets", 3),
        correlation_buckets=cfg.get("correlation_buckets", 30),
        correlation_min_samples=cfg.get("correlation_min_samples", 20),
        clock=clock,
    )
    return RiskModel(profiles, stats, limits, cfg, clock=clock, halt=halt)
