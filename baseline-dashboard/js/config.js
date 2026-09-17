// Dashboard configuration. Switch sandbox <-> live by pointing API_BASE_URL at
// the right exchange and using the matching API_KEY. Treat sandbox as dev and
// live as prod — don't develop against the live session.
const CONFIG = {
  API_BASE_URL: "http://exchange.gic-hackathon.internal",
  API_KEY: "REPLACE_WITH_TEAM_API_KEY",
  POLL_INTERVAL_MS: 30000, // 30s — intentionally slow; the exchange SSE feed is the fix (M1)
  MCP_BASE_URL: "http://localhost:8090", // provided read-only MCP server (M4) — 8090, not the exchange's 8080
  INSTRUMENTS_TO_SHOW: ["NOVA", "ELXR"], // hardcoded; the full set comes from GET /instruments
};
