// Dashboard configuration. Switch sandbox <-> live by pointing API_BASE_URL at
// the right exchange and using the matching API_KEY. Treat sandbox as dev and
// live as prod — don't develop against the live session.
const CONFIG = {
  API_BASE_URL: "/exchange", // same-origin proxy in server.js -> https://api.codetoimpact.sg (adds the key, fixes CORS)
  API_KEY: "", // not needed in the browser; server.js injects it
  REFRESH_MIN_INTERVAL_MS: 1000, // portfolio re-read is triggered by SSE price events, coalesced to at most one per second (shared 300/30s read tier)
  MCP_BASE_URL: "http://localhost:8090", // provided read-only MCP server (M4) — 8090, not the exchange's 8080
};
