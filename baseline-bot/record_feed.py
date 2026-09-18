"""Record the SSE price feed to a JSONL file for backtesting.

Nothing in the bot persists price history -- the strategy deques are wiped at
every session boundary. This read-only recorder subscribes to the same SSE feed
the bot uses and appends one line per `price` event, so a session can be replayed
later by `run_backtest.py`.

Run from baseline-bot with the same .env the bot uses:
    set -a; . ./.env; set +a; python record_feed.py

Output: recordings/feed-<UTC>-<session>.jsonl. Each line is either
    {"session_id", "symbol", "last_price", "timestamp", "seq"}
or, at a trading-window change,
    {"event": "session_boundary", "session_id", "timestamp", "seq"}
The `seq` is a local monotonic counter used only to preserve arrival order.
Stops on Ctrl-C or when the window ends; writes are line-buffered so a hard kill
still leaves a valid (if partial) file.
"""
import itertools
import json
import os
import threading
from datetime import datetime, timezone, timedelta

import requests

from exchange.feed import SSEFeed

BASE_URL = os.environ.get("EXCHANGE_BASE_URL", "https://api.codetoimpact.sg").rstrip("/")
API_KEY = os.environ.get("EXCHANGE_API_KEY", "")
RECORD_DIR = os.path.join(os.path.dirname(__file__), "recordings")

SGT = timezone(timedelta(hours=8))

_lock = threading.Lock()
_seq = itertools.count(1)
_state = {"session_id": None, "writer": None}


def _now_sgt():
    return datetime.now(SGT).strftime("%H:%M:%S")


def say(msg):
    print(f"[{_now_sgt()} SGT] {msg}", flush=True)


def open_writer(path: str):
    """Open a line-buffered append writer (buffering=1) at `path`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return open(path, "a", buffering=1)


def _default_path():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return os.path.join(RECORD_DIR, f"feed-{stamp}.jsonl")


def make_record(payload: dict, session_id, seq: int) -> dict:
    """Build one price JSONL record from an SSE `price` payload."""
    return {
        "session_id": session_id,
        "symbol": payload.get("symbol"),
        "last_price": int(payload.get("last_price")),
        "timestamp": payload.get("timestamp"),
        "seq": seq,
    }


def _write(record: dict):
    writer = _state["writer"]
    if writer is not None:
        writer.write(json.dumps(record) + "\n")


def on_price(payload):
    if payload.get("symbol") is None or payload.get("last_price") is None:
        return
    with _lock:
        record = make_record(payload, _state["session_id"], next(_seq))
        _write(record)


def on_session(payload):
    say(f"feed says session is now {payload.get('status')}")


def poll_session(stop: threading.Event):
    """Track the active window; emit a session_boundary sentinel on change."""
    last_id = None
    while not stop.is_set():
        try:
            r = requests.get(f"{BASE_URL}/session", timeout=10)
            data = r.json().get("data")
        except Exception as exc:
            say(f"session check failed: {exc}")
            data = None
        if data is not None:
            sid = data.get("id")
            if sid != last_id:
                say(f"NEW WINDOW {sid} (mode={data.get('mode')}). "
                    "Recording a session boundary; prices reset.")
                with _lock:
                    _state["session_id"] = sid
                    _write({
                        "event": "session_boundary",
                        "session_id": sid,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "seq": next(_seq),
                    })
                last_id = sid
            if data.get("status") == "ended":
                say("Window ENDED. Stopping recorder.")
                stop.set()
                return
        stop.wait(5)


def main():
    path = os.environ.get("RECORD_PATH") or _default_path()
    _state["writer"] = open_writer(path)
    say(f"recording {BASE_URL} -> {path} (read-only, no orders)")

    stop = threading.Event()
    threading.Thread(target=poll_session, args=(stop,), daemon=True).start()

    feed = SSEFeed(BASE_URL, API_KEY)
    feed.on("price", on_price)
    feed.on("session", on_session)
    feed.start()
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        say("stopping")
    finally:
        feed.stop()
        with _lock:
            if _state["writer"] is not None:
                _state["writer"].close()


if __name__ == "__main__":
    main()
