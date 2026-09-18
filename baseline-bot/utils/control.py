"""Local control endpoint for the bot (kill switch, P0-14).

A tiny standard-library HTTP server on a daemon thread. It is bound to
localhost only and carries no authentication: the dashboard dev server
(baseline-dashboard/server.js) proxies /bot/* to it on the same machine.

    GET  /status  -> {"state": "RUNNING", "session_id": ..., "session_open": bool}
    POST /halt    -> {"state": "STOPPED", "unresolved": 0}
    POST /order   -> {"ok": true, "order_id": ..., ...}  (manual override)

`/halt` runs the same halt() path as SIGINT/SIGTERM: block new intents,
cancel every open order, stop. Calling it twice is safe; the second call
reports the current state without cancelling anything.

`/order` is the desk overriding the bot: {"symbol", "side", "quantity"} places
a marketable limit through OrderManager's manual path. It answers 200 with the
placed order or 409 with a `reason` the dashboard shows the operator.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils.logger import log


class ControlServer:
    """Serve /status, /halt, and /news/config for the given callbacks.

    status_fn() -> dict         current lifecycle snapshot
    halt_fn()   -> int | None   run the halt path; returns unresolved count
    news_engine                 optional NewsEngine for live config reads/writes
    order_fn(symbol, side, quantity) -> dict
                                optional manual-override placer; returns a dict
                                with "ok" and either the order or a "reason"
    """

    def __init__(self, status_fn, halt_fn, host="127.0.0.1", port=8092,
                 news_engine=None, order_fn=None):
        self._status_fn = status_fn
        self._halt_fn = halt_fn
        self._news_engine = news_engine
        self._order_fn = order_fn
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
                if self.path == "/news/config" and outer._news_engine:
                    return self._send(200, outer._news_engine.get_config())
                self._send(404, {"error": "not found"})

            def _read_json(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                return json.loads(body)

            def do_POST(self):
                if self.path == "/halt":
                    log("control: halt requested by dashboard")
                    return self._send(200, outer._halt())
                if self.path == "/order" and outer._order_fn:
                    try:
                        req = self._read_json()
                    except (json.JSONDecodeError, ValueError):
                        return self._send(400, {"error": "invalid JSON"})
                    symbol = req.get("symbol")
                    side = str(req.get("side", "")).lower()
                    try:
                        quantity = int(req.get("quantity"))
                    except (TypeError, ValueError):
                        return self._send(400, {"error": "quantity must be an integer"})
                    if not symbol or side not in ("buy", "sell") or quantity <= 0:
                        return self._send(400, {"error": "symbol, side (buy/sell) "
                                                "and a positive quantity are required"})
                    log(f"control: manual {side} {quantity} {symbol} requested by dashboard")
                    result = outer._order_fn(symbol, side, quantity)
                    return self._send(200 if result.get("ok") else 409, result)
                if self.path == "/news/config" and outer._news_engine:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length) if length else b"{}"
                    try:
                        patch = json.loads(body)
                    except (json.JSONDecodeError, ValueError):
                        return self._send(400, {"error": "invalid JSON"})
                    outer._news_engine.update_config(patch)
                    return self._send(200, outer._news_engine.get_config())
                self._send(404, {"error": "not found"})

        return Handler
