"""Read-only market watcher. Places no orders.

Prints, in plain English:
  * the session state and countdown (so you know when a live window starts),
  * every market announcement, with an LLM read of its likely impact,
  * every 30 s, the same signals the bot uses (momentum, mean-reversion, ATR regime)
    for every instrument, so you can compare your own judgement with the bot's.

Run from baseline-bot with the same .env the bot uses:
    set -a; . ./.env; set +a; python watch_market.py
"""
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta

import requests

from exchange.feed import SSEFeed

BASE_URL = os.environ.get("EXCHANGE_BASE_URL", "https://api.codetoimpact.sg").rstrip("/")
LLM_URL = os.environ.get("LLM_BASE_URL", "https://llm.codetoimpact.sg").rstrip("/")
API_KEY = os.environ.get("EXCHANGE_API_KEY", "")
SUMMARY_EVERY = float(os.environ.get("WATCH_SUMMARY_SECONDS", "30"))

SGT = timezone(timedelta(hours=8))

# Same parameters the bot uses (config.yaml) plus James Wee's page-3 defaults.
MOMENTUM_LOOKBACK = 10
MOMENTUM_THRESHOLD = 0.003
MEANREV_LOOKBACK = 15
MEANREV_THRESHOLD = 0.004
ATR_LOOKBACK = 14
ATR_LOW = 0.0015
ATR_HIGH = 0.004

prices = {}          # symbol -> deque of last prices (cents)
last_session = {}    # last /session payload
lock = threading.Lock()


def now():
    return datetime.now(SGT).strftime("%H:%M:%S")


def say(msg):
    print(f"[{now()} SGT] {msg}", flush=True)


def dollars(cents):
    return f"${cents / 100:,.2f}"


# --- session ---------------------------------------------------------------

def poll_session():
    last_status = None
    last_id = None
    while True:
        try:
            r = requests.get(f"{BASE_URL}/session", timeout=10)
            data = r.json().get("data")
        except Exception as exc:
            say(f"session check failed: {exc}")
            data = None
        if data is None:
            if last_status != "none":
                say("No trading window exists yet. Waiting.")
                last_status = "none"
        else:
            status, mode, sid = data.get("status"), data.get("mode"), data.get("id")
            remaining = data.get("remaining_seconds") or 0
            if sid != last_id:
                say(f"NEW WINDOW {sid} mode={mode.upper()} status={status}. "
                    f"Everything from the previous window is now stale; prices reset.")
                with lock:
                    for dq in prices.values():
                        dq.clear()
                last_id = sid
            if status != last_status:
                if mode == "live" and status == "running":
                    say("*** LIVE WINDOW IS OPEN. THIS IS SCORED. START THE BOT NOW. ***")
                elif status == "running":
                    say(f"Sandbox window running (practice). {remaining // 60} min left.")
                elif status == "paused":
                    say("Window PAUSED. New orders are rejected; cancels still work.")
                elif status == "ended":
                    say("Window ENDED. Scored at the closing prices.")
                last_status = status
            with lock:
                last_session.clear()
                last_session.update(data)
        time.sleep(5)


# --- announcements + LLM sentiment -------------------------------------------

def llm_sentiment(message, symbol):
    if not API_KEY:
        return "(no API key; cannot ask the LLM)"
    prompt = (
        "You are a trading desk analyst. The market has five instruments: "
        "NOVA (oil & gas, trending), AXON (freight; follows NOVA because freight "
        "margins follow energy costs), CRVX (semiconductors, growth), DRFT (defense, "
        "thin liquidity, moves on news), ELXR (pharma, defensive, range-bound).\n"
        f"Announcement about {symbol}: \"{message}\"\n"
        "Answer in at most 4 short lines: 1) sentiment for the named symbol "
        "(BULLISH/BEARISH/NEUTRAL and confidence 0-100%), 2) which other symbols "
        "are likely to move and which way, 3) what a cautious human trader would do "
        "in the next 5 minutes, 4) what could make this reading wrong."
    )
    try:
        r = requests.post(
            f"{LLM_URL}/v1/messages",
            headers={"X-API-Key": API_KEY, "content-type": "application/json"},
            json={"model": "haiku", "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        body = r.json()
        return "".join(b.get("text", "") for b in body.get("content", [])) or json.dumps(body)[:300]
    except Exception as exc:
        return f"(LLM call failed: {exc})"


def on_announcement(payload):
    sym = payload.get("affected_symbol")
    say(f"ANNOUNCEMENT #{payload.get('announcement_id')} about {sym}: {payload.get('message')}")
    say("LLM read:\n    " + llm_sentiment(payload.get("message", ""), sym).replace("\n", "\n    "))


def on_session(payload):
    say(f"feed says session is now {payload.get('status')}")


def on_price(payload):
    sym = payload.get("symbol")
    px = payload.get("last_price")
    if sym is None or px is None:
        return
    with lock:
        prices.setdefault(sym, deque(maxlen=200)).append(int(px))


# --- signal summary ----------------------------------------------------------

def regime(dq):
    if len(dq) < ATR_LOOKBACK + 1:
        return "warming up", None
    trs = [abs(dq[i] - dq[i - 1]) for i in range(len(dq) - ATR_LOOKBACK, len(dq))]
    atr_pct = (sum(trs) / len(trs)) / dq[-1]
    if atr_pct < ATR_LOW:
        return "QUIET/RANGING (momentum will bleed; prefer mean-reversion or sit out)", atr_pct
    if atr_pct > ATR_HIGH:
        return "TRENDING/VOLATILE (momentum is allowed)", atr_pct
    return "NEUTRAL (hold posture, smaller size)", atr_pct


def summarize():
    while True:
        time.sleep(SUMMARY_EVERY)
        with lock:
            snap = {s: list(dq) for s, dq in prices.items()}
            sess = dict(last_session)
        if not snap:
            say("no prices yet")
            continue
        remaining = sess.get("remaining_seconds")
        head = f"--- signals ({sess.get('mode', '?')}, {sess.get('status', '?')}"
        if remaining is not None:
            head += f", {int(remaining) // 60}m{int(remaining) % 60:02d}s left"
        say(head + ") ---")
        for sym in sorted(snap):
            dq = snap[sym]
            last = dq[-1]
            line = f"{sym:5} {dollars(last):>10}"
            if len(dq) >= MOMENTUM_LOOKBACK:
                first = dq[-MOMENTUM_LOOKBACK]
                mom = (last - first) / first
                tag = "BUY signal" if mom >= MOMENTUM_THRESHOLD else (
                    "SELL signal (if holding)" if mom <= -MOMENTUM_THRESHOLD else "no signal")
                line += f" | momentum {mom * 100:+.2f}% -> {tag}"
            else:
                line += f" | momentum warming up ({len(dq)}/{MOMENTUM_LOOKBACK})"
            if len(dq) >= MEANREV_LOOKBACK:
                window = dq[-MEANREV_LOOKBACK:]
                mean = sum(window) / len(window)
                dev = (last - mean) / mean
                tag = "BUY (below mean)" if dev <= -MEANREV_THRESHOLD else (
                    "SELL (above mean, if holding)" if dev >= MEANREV_THRESHOLD else "no signal")
                line += f" | mean-rev {dev * 100:+.2f}% -> {tag}"
            reg, atr = regime(dq)
            line += f" | regime: {reg}" + (f" (ATR {atr * 100:.2f}%)" if atr else "")
            say(line)


def main():
    say(f"watching {BASE_URL} (read-only, no orders will be placed)")
    threading.Thread(target=poll_session, daemon=True).start()
    threading.Thread(target=summarize, daemon=True).start()
    feed = SSEFeed(BASE_URL, API_KEY)
    feed.on("price", on_price)
    feed.on("session", on_session)
    feed.on("announcement", on_announcement)
    feed.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        say("stopping")
        feed.stop()


if __name__ == "__main__":
    main()
