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
// The organiser LLM service (Anthropic Messages shape). /llm/* is forwarded here
// with the same team key injected, so the conversational assistant (M4) can run
// from the browser without the key ever reaching the page. See docs/llm-api-reference.md.
const LLM = new URL(process.env.LLM_BASE_URL || "https://llm.codetoimpact.sg");
// The bot's local control endpoint (kill switch). /bot/* is forwarded here so
// the browser never talks cross-origin and the bot can stay bound to localhost.
const BOT = new URL(process.env.BOT_CONTROL_URL || "http://127.0.0.1:8092");
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

// Forward /bot/<path> to the bot's control server. Only GET /status and
// POST /halt exist upstream; anything else is answered 404 by the bot itself.
// A bot that is not running answers 502 so the page can show OFFLINE.
function proxyBot(req, res) {
  const upstreamPath = req.url.replace(/^\/bot/, "") || "/";
  // Forward the body framing headers so a POST body actually reaches the bot.
  // Without content-length, piping sends the body chunked, which the bot's
  // stdlib HTTP handler (it reads Content-Length) sees as empty — silently
  // dropping the JSON for /order and /news/config.
  const headers = { accept: "application/json" };
  if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];
  if (req.headers["content-length"]) headers["content-length"] = req.headers["content-length"];
  const up = http.request(
    { host: BOT.hostname, port: BOT.port || 80, path: upstreamPath, method: req.method, headers: headers },
    function (upRes) {
      res.writeHead(upRes.statusCode, { "content-type": upRes.headers["content-type"] || "application/json", "cache-control": "no-store" });
      upRes.pipe(res);
    }
  );
  up.setTimeout(10000, function () { up.destroy(new Error("bot control timeout")); });
  up.on("error", function (err) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: { code: "BOT_UNREACHABLE", message: err.message } }));
  });
  req.pipe(up);
}

// Forward /llm/<path> to the organiser LLM service, injecting X-API-Key (the
// same exchange key the exchange proxy uses — the LLM service shares it). The
// service rejects streaming, so this is plain request/response with no SSE
// special-casing. Only POST /llm/v1/messages and GET /llm/health are meaningful
// upstream; anything else the service answers itself.
function proxyLlm(req, res) {
  const upstreamPath = req.url.replace(/^\/llm/, "") || "/";
  const headers = {
    "X-API-Key": API_KEY,
    accept: req.headers.accept || "application/json",
  };
  if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];
  if (req.headers["content-length"]) headers["content-length"] = req.headers["content-length"];
  const up = https.request(
    { host: LLM.host, path: upstreamPath, method: req.method, headers: headers },
    function (upRes) {
      const out = Object.assign({}, upRes.headers);
      delete out["content-length"]; // may be chunked after re-streaming
      res.writeHead(upRes.statusCode, out);
      upRes.pipe(res);
    }
  );
  up.setTimeout(30000, function () { up.destroy(new Error("llm service timeout")); });
  up.on("error", function (err) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "error", error: { code: "LLM_UNREACHABLE", message: err.message } }));
  });
  res.on("close", function () { up.destroy(); });
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
    if (req.url.startsWith("/bot/")) return proxyBot(req, res);
    if (req.url.startsWith("/llm/")) return proxyLlm(req, res);
    serveStatic(req, res);
  })
  .listen(PORT, function () {
    console.log("dashboard  http://localhost:" + PORT);
    console.log("proxying   /exchange/* -> " + EXCHANGE.origin + (API_KEY ? " (key loaded)" : " (NO KEY)"));
    console.log("proxying   /bot/*      -> " + BOT.origin + " (bot control: status + kill switch)");
    console.log("proxying   /llm/*      -> " + LLM.origin + (API_KEY ? " (key loaded)" : " (NO KEY)") + " (assistant M4)");
  });
