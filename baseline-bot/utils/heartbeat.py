"""Bot heartbeat and feed-stall watchdog (feature backlog: "true bot health").

Emits a heartbeat every ~`interval` seconds: one log line, and a snapshot the
control endpoint serves on GET /status. The dashboard treats more than 15 s
without a heartbeat as dead (stakeholder note 7), and reads the age from the
bot's own clock, so a hung trading thread shows up even while the HTTP thread
still answers.

The same thread is the feed watchdog. sseclient drops the exchange's keep-alive
comments, so `feed.age()` is the time since the last *event*:

    age <= degraded_after   ALIVE
    age <= stall_after      DEGRADED  (quiet market or a stalling socket)
    age >  stall_after      DEAD      -> restart the stream from its cursor

A restart is cheap (Last-Event-ID replay), so a false positive during a quiet
market costs one reconnect, while a half-open socket is caught in
`stall_after` seconds instead of the 30 s read timeout.
"""
import threading
import time

from utils.logger import log

ALIVE = "ALIVE"
DEGRADED = "DEGRADED"
DEAD = "DEAD"


class Heartbeat:
    def __init__(self, feed, worker=None, lifecycle=None, orders=None,
                 interval=5.0, degraded_after=10.0, stall_after=20.0,
                 clock=time.monotonic, restart=True):
        self._feed = feed
        self._worker = worker
        self._lifecycle = lifecycle if lifecycle is not None else {"value": "RUNNING"}
        self._orders = orders
        self.interval = float(interval)
        self.degraded_after = float(degraded_after)
        self.stall_after = float(stall_after)
        self._clock = clock
        self._restart = restart
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._seq = 0
        self._last_beat_at = None
        self.restarts = 0

    # --- lifecycle -------------------------------------------------------

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("heartbeat is already running")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self.beat()
            self._stop.wait(self.interval)

    # --- one beat --------------------------------------------------------

    def feed_state(self, age):
        if age is None:
            return DEGRADED  # connected but nothing received yet
        if age <= self.degraded_after:
            return ALIVE
        if age <= self.stall_after:
            return DEGRADED
        return DEAD

    def beat(self):
        """Record one heartbeat and run the watchdog. Safe to call directly."""
        now = self._clock()
        age = self._feed.age(now) if self._feed is not None else None
        state = self.feed_state(age)
        with self._lock:
            self._seq += 1
            self._last_beat_at = now
            seq = self._seq
        stalled = state == DEAD and self._feed is not None
        if stalled and self._restart and self._lifecycle.get("value") == "RUNNING":
            log(f"heartbeat #{seq}: feed stalled for {age:.0f}s; restarting stream")
            try:
                self._feed.restart()
                self.restarts += 1
            except Exception as exc:  # the next beat tries again
                log(f"heartbeat: feed restart failed: {exc}")
        else:
            age_txt = "n/a" if age is None else f"{age:.1f}s"
            log(f"heartbeat #{seq}: {self._lifecycle.get('value')} feed={state} "
                f"age={age_txt}")
        return state

    # --- snapshot for GET /status ---------------------------------------

    def snapshot(self):
        now = self._clock()
        with self._lock:
            seq = self._seq
            beat_age = None if self._last_beat_at is None else now - self._last_beat_at
        age = self._feed.age(now) if self._feed is not None else None
        return {
            "heartbeat_seq": seq,
            "heartbeat_age_s": None if beat_age is None else round(beat_age, 1),
            "heartbeat_interval_s": self.interval,
            "feed_state": self.feed_state(age),
            "feed_age_s": None if age is None else round(age, 1),
            "feed_connected": bool(self._feed is not None and self._feed.connected()),
            "feed_reconnects": getattr(self._feed, "reconnects", 0),
            "feed_restarts": self.restarts,
            "worker_alive": bool(self._worker is not None and self._worker.is_alive()),
        }
