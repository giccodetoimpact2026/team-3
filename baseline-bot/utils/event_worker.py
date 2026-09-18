"""Bounded worker that keeps trading callbacks out of the SSE reader thread."""

import logging
import queue
import threading


logger = logging.getLogger(__name__)
_STOP = object()


class EventWorker:
    def __init__(self, handlers, max_queue_size=1_000):
        self._handlers = dict(handlers)
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("event worker is already running")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, event_type, payload):
        # Backpressure is deliberate: losing market events is less safe than
        # slowing ingestion and allowing the SSE replay path to recover.
        self._queue.put((event_type, payload))

    def stop(self):
        self._queue.put(_STOP)

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                event_type, payload = item
                handler = self._handlers.get(event_type)
                if handler is not None:
                    handler(payload)
            except Exception:
                logger.exception("event worker handler failed")
            finally:
                self._queue.task_done()
