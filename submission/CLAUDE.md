# GIC #CODETOIMPACT 2026 — Team 3

Three-process architecture: trading bot (Python), monitoring dashboard (Node.js), MCP server (Python). All communicate via the exchange SSE feed and REST API.

## Run

```bash
# Bot
cd baseline-bot
pip install -r requirements.txt
export EXCHANGE_API_KEY="your-team-key"
python main.py

# Dashboard (separate terminal)
cd baseline-dashboard
npm install
EXCHANGE_API_KEY="your-team-key" node server.js
# Open http://localhost:3000

# MCP server (separate terminal)
cd baseline-mcp-server
pip install -r requirements.txt
EXCHANGE_API_KEY="your-team-key" python server.py
```

## Test

```bash
# Bot (337 tests)
cd baseline-bot && pytest -q

# Dashboard (67 tests)
cd baseline-dashboard && npm test

# MCP server
cd baseline-mcp-server && pytest -q
```

CI runs on every push via `.github/workflows/bot-ci.yml` and `dashboard-ci.yml`.

## Architecture

- **baseline-bot/** — Python trading bot. 5 instruments (NOVA, ELXR, CRVX, DRFT, AXON) across 3 strategy types (momentum, mean-reversion, pairs-follow). Regime-aware sizing, news/sentiment engine, DCA overlay, risk exits, order-book-aware entry sizing. Config in `config.yaml`. Control endpoint at localhost:8092.
- **baseline-dashboard/** — Node.js static server + reverse proxy. 7-tab SPA with live SSE feed, candlestick charts, performance analytics, risk metrics, CSV export, kill switch. API key never reaches the browser (key-injecting proxy).
- **baseline-mcp-server/** — FastMCP server with dual surface: MCP protocol at `/mcp` + REST facade at `/tools/{name}`. Read-only tools for portfolio, positions, P&L, session summary.

## Key constraints

- Prices are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — sells only reduce/close held longs.
- Auth is `X-API-Key` header on every call.
- SSE rate limit: 300 reads per 30 seconds.
- Never commit the team API key. It is injected via `EXCHANGE_API_KEY` env var.
- Future AI features must use the organizer LLM service (`https://llm.codetoimpact.sg`).
- The news/sentiment engine's Tier 1 LLM refinement already uses the organizer service (budget-capped at 60 calls).

## Bot strategy configuration

All strategy parameters are in `baseline-bot/config.yaml`. Crash-buy strategy is implemented but NOT assigned — a commented block shows starting parameters for CRVX. Regime detection is enabled with hysteresis (`confirm_samples=3`).

## Conventions

- SGT timestamps everywhere (YYYY-MM-DD HH:MM:SS.mmm SGT).
- Gerald's fixed CSV export schema — column order and formatting are exact specs, not suggestions.
- Dashboard HTML escaping on all exchange-sourced strings.
- One-active-order-per-symbol policy in the order manager.
- Exits (stop-loss, trailing take-profit) are per-instrument in `config.yaml` under `risk.exits`.
