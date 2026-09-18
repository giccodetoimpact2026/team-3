"""Per-instrument risk profiles.

A profile turns the desk's description of an instrument (trending, follower,
thin, range-bound) into the numbers the risk model needs: how large it may be
traded relative to the reference name, how much of the visible book one order
may take, the volatility thresholds that define its regime, how far a stop sits
and how long the bot stays out after one fires.

Profiles are merged from three layers of `config.yaml`'s `risk_model` section:
`default_profile`, then `profiles.<SYMBOL>`, plus group membership derived from
`groups`. Instruments are discovered at runtime; a symbol without an entry gets
the defaults, so a new listing is traded small rather than not at all.
"""
from dataclasses import dataclass, fields


@dataclass
class InstrumentProfile:
    symbol: str
    personality: str = "unknown"
    size_multiplier: float = 0.5        # order size relative to the reference name
    liquidity: str = "liquid"           # "liquid" | "thin" (informational)
    depth_fraction: float = 0.5         # max share of visible ask depth per buy
    max_qty_without_book: int = 1000    # buy ceiling before any book_update was seen
    low_vol: float = 0.004              # ATR_pct below this -> LOW regime
    high_vol: float = 0.010             # ATR_pct above this -> HIGH regime
    stop_atr_mult: float = 4.0          # stop distance = mult x ATR ...
    min_stop_pct: float = 0.04          # ... bounded below by this fraction of cost
    max_stop_pct: float = 0.10          # ... and above by this
    confirm_ticks: int = 3              # consecutive breaching ticks before a stop fires
    cooldown_seconds: float = 120.0     # no new buys on the symbol after a stop
    group: str | None = None            # correlation group name, if any

    def __post_init__(self):
        self.size_multiplier = float(self.size_multiplier)
        self.depth_fraction = float(self.depth_fraction)
        self.max_qty_without_book = int(self.max_qty_without_book)
        self.low_vol = float(self.low_vol)
        self.high_vol = float(self.high_vol)
        self.stop_atr_mult = float(self.stop_atr_mult)
        self.min_stop_pct = float(self.min_stop_pct)
        self.max_stop_pct = float(self.max_stop_pct)
        self.confirm_ticks = max(1, int(self.confirm_ticks))
        self.cooldown_seconds = float(self.cooldown_seconds)


_OVERRIDABLE = {f.name for f in fields(InstrumentProfile)} - {"symbol", "group"}


def group_membership(cfg: dict) -> dict:
    """symbol -> group name, from `groups: {name: {members: [...]}}`."""
    out = {}
    for name, group in (cfg.get("groups") or {}).items():
        for member in (group or {}).get("members") or []:
            out[member] = name
    return out


def load_profiles(cfg: dict, symbols) -> dict:
    """Build one profile per discovered symbol from the `risk_model` config."""
    defaults = cfg.get("default_profile") or {}
    overrides = cfg.get("profiles") or {}
    membership = group_membership(cfg)
    profiles = {}
    for symbol in symbols:
        merged = {**defaults, **(overrides.get(symbol) or {})}
        kwargs = {k: v for k, v in merged.items() if k in _OVERRIDABLE}
        profiles[symbol] = InstrumentProfile(symbol=symbol, group=membership.get(symbol),
                                             **kwargs)
    return profiles
