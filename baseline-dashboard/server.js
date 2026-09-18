// Local dev server: serves the static dashboard and proxies /exchange/* to the
// real exchange. The exchange sends no CORS headers, so a browser page cannot
// call it directly; going through this same-origin proxy fixes that for REST
// and for the SSE feed alike. The proxy injects X-API-Key from the environment
// (or ../baseline-bot/.env) so the key never reaches the browser.
//
//   EXCHANGE_API_KEY=... node server.js        # http://localhost:5500
//
// No dependencies. Node >= 18.
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT || 5500);
const EXCHANGE = new URL(process.env.EXCHANGE_BASE_URL || "https://api.codetoimpact.sg");
const ROOT = __dirname;

function loadKey() {
  if (process.env.EXCHANGE_API_KEY) return process.env.EXCHANGE_API_KEY;
  const envPath = path.join(ROOT, "..", "baseline-bot", ".env");
  try {
    const m = fs.readFileSync(envPath, "utf8").match(/^EXCHANGE_API_KEY=(.*)$/m);
    if (m) return m[1].trim().replace(/^["']|["']$/g, "");
  } catch (_) {}
  return "";
}
const API_KEY = loadKey();
if (!API_KEY) console.warn("WARN: no EXCHANGE_API_KEY found; authenticated calls will 401");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function proxy(req, res) {
  const upstreamPath = req.url.replace(/^\/exchange/, "") || "/";
  const headers = {
    "X-API-Key": API_KEY,
    accept: req.headers.accept || "*/*",
  };
  if (req.headers["last-event-id"]) headers["Last-Event-ID"] = req.headers["last-event-id"];
  if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];

  const up = https.request(
    { host: EXCHANGE.host, path: upstreamPath, method: req.method, headers },
    function (upRes) {
      const out = Object.assign({}, upRes.headers);
      delete out["content-length"]; // may be chunked after re-streaming
      if (upRes.headers["content-type"] && upRes.headers["content-type"].includes("text/event-stream")) {
        out["cache-control"] = "no-cache";
        out["x-accel-buffering"] = "no";
      }
      res.writeHead(upRes.statusCode, out);
      upRes.pipe(res);
    }
  );
  up.on("error", function (err) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: { code: "PROXY_ERROR", message: err.message } }));
  });
  res.on("close", function () { up.destroy(); }); // drop the upstream SSE when the tab goes (req "close" fires after the body is read, too early)
  req.pipe(up);
}

function serveStatic(req, res) {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p === "/") p = "/index.html";
  const file = path.normalize(path.join(ROOT, p));
  if (!file.startsWith(ROOT)) { res.writeHead(403); return res.end(); }
  fs.readFile(file, function (err, data) {
    if (err) { res.writeHead(404); return res.end("Not found"); }
    res.writeHead(200, { "content-type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(data);
  });
}

http
  .createServer(function (req, res) {
    if (req.url.startsWith("/exchange/") || req.url === "/exchange") return proxy(req, res);
    serveStatic(req, res);
  })
  .listen(PORT, function () {
    console.log("dashboard  http://localhost:" + PORT);
    console.log("proxying   /exchange/* -> " + EXCHANGE.origin + (API_KEY ? " (key loaded)" : " (NO KEY)"));
  });
