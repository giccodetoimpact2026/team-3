"""Regression tests for decoupled event processing."""

import threading

from utils.event_worker import EventWorker


def test_worker_processes_events_in_order():
    processed = []
    ready = threading.Event()

    def handle(payload):
        processed.append(payload["sequence"])
        if len(processed) == 3:
            ready.set()

    worker = EventWorker({"price": handle}, max_queue_size=3)
    worker.start()
    for sequence in (1, 2, 3):
        worker.submit("price", {"sequence": sequence})

    assert ready.wait(1)
    worker.stop()
    worker.join(1)
    assert processed == [1, 2, 3]


def test_handler_failure_does_not_kill_worker():
    processed = []
    ready = threading.Event()

    def handle(payload):
        if payload["sequence"] == 1:
            raise RuntimeError("boom")
        processed.append(payload["sequence"])
        ready.set()

    worker = EventWorker({"price": handle})
    worker.start()
    worker.submit("price", {"sequence": 1})
    worker.submit("price", {"sequence": 2})

    assert ready.wait(1)
    assert worker.is_alive()
    worker.stop()
    worker.join(1)
    assert processed == [2]
