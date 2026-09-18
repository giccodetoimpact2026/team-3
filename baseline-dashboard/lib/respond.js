// Error responses for the proxies in server.js. A proxied SSE stream can fail
// after its headers (and part of the body) have already gone to the browser;
// calling writeHead then throws ERR_HTTP_HEADERS_SENT inside an event handler
// and kills the whole process. So: write the JSON envelope only while the
// response is still fresh, otherwise just end it and let EventSource reconnect.
function sendError(res, status, code, message) {
  if (res.writableEnded) return;
  if (res.headersSent) return res.end();
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify({ status: "error", error: { code: code, message: message } }));
}

module.exports = { sendError };
