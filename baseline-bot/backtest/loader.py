"""Load a recorded JSONL feed into ordered per-symbol price series.

The recorder (`record_feed.py`) writes one JSON object per line. Price lines are
`{"session_id", "symbol", "last_price"(int cents), "timestamp", "seq"}`; a new
trading window emits a sentinel `{"event": "session_boundary", ...}`.

A backtest must not look across a session boundary (the live bot's price deques
are wiped at each window edge), so the loader splits the stream into per-session
*segments* and, by default, the caller replays a single segment.
"""
import json

# Each tick is (seq, timestamp, last_price_cents).
Tick = tuple


def _iter_lines(path: str):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A hard-killed recorder can leave one truncated final line.
                continue


def load_segments(path: str) -> list[dict]:
    """Return per-session segments in file order.

    Each segment is {"session_id": str|None, "series": {symbol: [ticks...]}},
    where ticks are sorted by `seq`. A `session_boundary` sentinel starts a new
    segment.
    """
    segments: list[dict] = []
    current = {"session_id": None, "series": {}}
    started = False

    for obj in _iter_lines(path):
        if obj.get("event") == "session_boundary":
            if started:
                segments.append(current)
            current = {"session_id": obj.get("session_id"), "series": {}}
            started = False
            continue

        symbol = obj.get("symbol")
        price = obj.get("last_price")
        if symbol is None or price is None:
            continue
        started = True
        if current["session_id"] is None:
            current["session_id"] = obj.get("session_id")
        tick = (int(obj.get("seq", 0)), obj.get("timestamp"), int(price))
        current["series"].setdefault(symbol, []).append(tick)

    if started:
        segments.append(current)

    for seg in segments:
        for series in seg["series"].values():
            series.sort(key=lambda t: t[0])
    return segments


def load_series(path: str, session_id: str | None = None) -> dict:
    """Load one session's per-symbol series.

    With no `session_id`, use the last (most recent) segment that has prices.
    Returns {"session_id": str|None, "series": {symbol: [ticks...]}}.
    """
    segments = load_segments(path)
    if not segments:
        return {"session_id": None, "series": {}}
    if session_id is not None:
        for seg in segments:
            if seg["session_id"] == session_id:
                return seg
        return {"session_id": session_id, "series": {}}
    return segments[-1]


def sessions(path: str) -> list[str]:
    """List distinct session ids seen in the file, in order."""
    seen = []
    for seg in load_segments(path):
        sid = seg["session_id"]
        if sid is not None and sid not in seen:
            seen.append(sid)
    return seen
