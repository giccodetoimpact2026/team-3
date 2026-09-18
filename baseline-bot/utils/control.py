"""Local control endpoint for the bot (kill switch, P0-14).

A tiny standard-library HTTP server on a daemon thread. It is bound to
localhost only and carries no authentication: the dashboard dev server
(baseline-dashboard/server.js) proxies /bot/* to it on the same machine.

    GET  /status  -> {"state": "RUNNING", "session_id": ..., "session_open": bool}
    POST /halt    -> {"state": "STOPPED", "unresolved": 0}

`/halt` runs the same halt() path as SIGINT/SIGTERM: block new intents,
cancel every open order, stop. Calling it twice is safe; the second call
reports the current state without cancelling anything.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils.logger import log


class ControlServer:
    """Serve /status and /halt for the given callbacks.

    status_fn() -> dict         current lifecycle snapshot
    halt_fn()   -> int | None   run the halt path; returns unresolved count
    """

    def __init__(self, status_fn, halt_fn, host="127.0.0.1", port=8092):
        self._status_fn = status_fn
        self._halt_fn = halt_fn
        self._halt_lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler_class())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="control", daemon=True
        )

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        self._thread.start()
        log(f"control: listening on http://{self._server.server_address[0]}:{self.port}")

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def _halt(self):
        # One halt at a time; a second click while cancelling waits and then
        # sees the STOPPED state instead of racing the cancellations.
        with self._halt_lock:
            unresolved = self._halt_fn()
        body = dict(self._status_fn())
        body["unresolved"] = int(unresolved or 0)
        return body

    def _handler_class(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep bot stdout clean
                pass

            def _send(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/status":
                    return self._send(200, outer._status_fn())
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if self.path == "/halt":
                    log("control: halt requested by dashboard")
                    return self._send(200, outer._halt())
                self._send(404, {"error": "not found"})

        return Handler
