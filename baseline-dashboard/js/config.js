// Dashboard configuration. Switch sandbox <-> live by pointing API_BASE_URL at
// the right exchange and using the matching API_KEY. Treat sandbox as dev and
// live as prod — don't develop against the live session.
const CONFIG = {
  API_BASE_URL: "/exchange", // same-origin proxy in server.js -> https://api.codetoimpact.sg (adds the key, fixes CORS)
  API_KEY: "", // not needed in the browser; server.js injects it
  REFRESH_MIN_INTERVAL_MS: 1000, // portfolio re-read is triggered by SSE price events, coalesced to at most one per second
  ORDERS_REFRESH_MIN_INTERVAL_MS: 5000, // orders + MCP P&L re-read is triggered by SSE trade events (market-wide, ~1/s); coalesced to one per 5s. Each cycle is 3 exchange reads (orders, portfolio, MCP→portfolio) against the 300/30s tier shared with the bot
  MCP_BASE_URL: "http://localhost:8091", // provided read-only MCP server (M4); it sends CORS headers so no proxy is needed. Default port is 8090; 8091 here because 8090 was taken on the dev machine (MCP_PORT env var)
  MCP_RETRY_MS: 10000, // after an MCP failure, wait this long before trying again
  BOT_BASE_URL: "/bot", // same-origin proxy in server.js -> the bot's local control endpoint (config.yaml `control`, default 127.0.0.1:8092)
  BOT_STATUS_INTERVAL_MS: 3000, // poll GET /bot/status; local only, no exchange rate-limit cost
  LLM_BASE_URL: "/llm", // same-origin proxy in server.js -> https://llm.codetoimpact.sg (adds the team key). Powers the M4 assistant
  LLM_MODEL: "sonnet", // organiser LLM alias: fable | haiku | sonnet (default) | opus. Smaller = cheaper credit
  LLM_MAX_TOKENS: 512, // cap per reply so a runaway answer can't burn the ~$25 credit grant
};
