# HANDOVER.md — Platform / Engineering

This server is **provided and working** — you **run it yourself** and consume it; the protocol plumbing is written, so you don't have to build it.

**What's live**
- Read tools: `get_portfolio`, `get_positions`, `get_pnl`, `get_session_summary`.
  Each returns `{ source, as_of, data }` — `source` is the backing endpoint and
  `as_of` is an SGT timestamp, so your assistant can cite where an answer came from.
- A client adapter (`adapter/mcp-adapter.example.js`) shows one tool call end-to-end.
  Copy the pattern for the others.
- Two surfaces on the same port (default 8090): a genuine streamable-HTTP **MCP**
  endpoint at `/mcp` (for MCP-native clients), and a **REST facade**
  `POST /tools/{name}` for the browser dashboard (browsers don't speak raw MCP).

**Stubs (bonus to activate)**
- Control tools — `pause_instrument`, `resume_instrument`, `kill_switch` — exist but
  return a "not wired" notice. Making them act requires a channel to the bot and
  bot-side handling (graceful pause/shutdown). That's a stretch goal, not a requirement.

**Config**
- `MCP_BASE_URL` (default `http://localhost:8090`), plus the exchange base URL and your
  API key in `config.yaml`.

— Platform team
