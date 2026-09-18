"""Heartbeat liveness and the feed-stall watchdog."""
from unittest.mock import Mock

from utils.heartbeat import ALIVE, DEAD, DEGRADED, Heartbeat


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def _feed(last_event_age=None, connected=True):
    feed = Mock()
    feed.age.side_effect = (lambda now=None: last_event_age)
    feed.connected.return_value = connected
    feed.reconnects = 0
    return feed


def test_snapshot_reports_heartbeat_age_from_the_bots_clock():
    clock = FakeClock()
    hb = Heartbeat(_feed(1.0), clock=clock)
    hb.beat()
    clock.now += 7
    snap = hb.snapshot()
    assert snap["heartbeat_seq"] == 1
    assert snap["heartbeat_age_s"] == 7.0
    assert snap["feed_state"] == ALIVE
    assert snap["feed_age_s"] == 1.0


def test_feed_state_thresholds():
    hb = Heartbeat(_feed(), degraded_after=10, stall_after=20)
    assert hb.feed_state(None) == DEGRADED
    assert hb.feed_state(3) == ALIVE
    assert hb.feed_state(10) == ALIVE
    assert hb.feed_state(15) == DEGRADED
    assert hb.feed_state(21) == DEAD


def test_stalled_feed_is_restarted_once_per_beat():
    feed = _feed(45.0)
    hb = Heartbeat(feed, lifecycle={"value": "RUNNING"}, degraded_after=10, stall_after=20)
    assert hb.beat() == DEAD
    feed.restart.assert_called_once()
    assert hb.restarts == 1


def test_degraded_feed_is_not_restarted():
    feed = _feed(15.0)
    hb = Heartbeat(feed, degraded_after=10, stall_after=20)
    assert hb.beat() == DEGRADED
    feed.restart.assert_not_called()


def test_no_restart_while_halting():
    feed = _feed(45.0)
    hb = Heartbeat(feed, lifecycle={"value": "QUIESCING"}, stall_after=20)
    hb.beat()
    feed.restart.assert_not_called()


def test_restart_failure_does_not_kill_the_beat():
    feed = _feed(45.0)
    feed.restart.side_effect = RuntimeError("thread still alive")
    hb = Heartbeat(feed, stall_after=20)
    hb.beat()
    assert hb.snapshot()["heartbeat_seq"] == 1
    assert hb.restarts == 0
