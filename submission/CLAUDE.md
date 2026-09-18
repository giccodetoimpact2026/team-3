# GIC #CODETOIMPACT 2026 — Team 3

Three-process architecture: trading bot (Python), monitoring dashboard (Node.js), MCP server (Python). The bot and dashboard consume exchange market data; the dashboard also uses the bot control endpoint, MCP tools, and an organizer-LLM proxy.

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
# Open http://localhost:5500

# MCP server (separate terminal)
cd baseline-mcp-server
pip install -r requirements.txt
MCP_EXCHANGE_API_KEY="your-team-key" python server.py
```

## Test

```bash
# Bot: run the same lint and test steps as CI
cd baseline-bot && ruff check . && pytest -q

# Dashboard: syntax checks and tests
cd baseline-dashboard && npm run check && npm test

# MCP server
cd baseline-mcp-server && pytest -q
```

Install the lint/test versions specified in the workflows before running these checks.
Test totals change as features are added; use the current runner output rather than
a fixed count in this document. Local checks do not establish that a GitHub run passed.

CI runs on every push via `.github/workflows/bot-ci.yml` and `dashboard-ci.yml`.

## Architecture

- **baseline-bot/** — Python trading bot. 5 instruments (NOVA, ELXR, CRVX, DRFT, AXON) across 3 strategy types (momentum, mean-reversion, pairs-follow). Regime-aware sizing, news/sentiment engine, DCA overlay, risk exits, order-book-aware entry sizing. Config in `config.yaml`. Control endpoint at localhost:8092.
- **baseline-dashboard/** — Node.js static server + reverse proxy. 7-tab SPA with live SSE feed, candlestick charts, performance analytics, risk metrics, CSV export, kill switch. API key never reaches the browser (key-injecting proxy).
- **baseline-mcp-server/** — FastMCP server with dual surface: MCP protocol at `/mcp` + REST facade at `/tools/{name}`. Read-only tools for portfolio, positions, P&L, session summary.

## Key constraints

- Prices are **integer cents** on the wire ($42.50 → `4250`).
- Orders are `limit` only; `side` lowercase; one `idempotency_key` per order.
- The exchange is **long-only** — sells only reduce/close held longs.
- Auth is `X-API-Key` on authenticated REST calls. `/health`, `/ready`, `/session`, and the SSE `/events` feed are public.
- Authenticated shared REST operations have a 300-request rolling 30-second limit; this is not an SSE event limit. Order submissions have a separate limit (see `docs/exchange-api-reference.md`).
- Never commit the team API key. The bot and dashboard accept `EXCHANGE_API_KEY` and load the Git-ignored `baseline-bot/.env`; exported values take precedence. The MCP server uses `MCP_EXCHANGE_API_KEY`. Keep the tracked config placeholder. Credentials previously committed must be rotated; removing them from the current file does not erase history.
- Future AI features must use the organizer LLM service (`https://llm.codetoimpact.sg`).
- The news/sentiment engine's Tier 1 LLM refinement uses the organizer service, with a configured cap of 60 calls per session. The dashboard also implements an LLM assistant through its server-side `/llm` proxy. These are application AI features, distinct from AI-assisted coding; actual usage and spend require service records.

## Bot strategy configuration

All strategy parameters are in `baseline-bot/config.yaml`. Crash-buy strategy is implemented but NOT assigned — a commented block shows starting parameters for CRVX. Regime detection is enabled with hysteresis (`confirm_samples=3`).

## Conventions

- SGT timestamps everywhere (YYYY-MM-DD HH:MM:SS.mmm SGT).
- Gerald's fixed CSV export schema — column order and formatting are exact specs, not suggestions.
- Dashboard HTML escaping on all exchange-sourced strings.
- One-active-order-per-symbol policy in the order manager.
- Exits (stop-loss, trailing take-profit) are per-instrument in `config.yaml` under `risk.exits`.

## AI-assisted development and review record

AI assistance in this development session included code investigation, regime
classifier/policy integration, merge-conflict resolution, runtime credential
handling, regression tests, CI fixes, and documentation. Later contributions
from teammates were integrated as well; this file is not a complete authorship
ledger. Use Git history and pull requests to attribute individual changes.

### What AI wrote vs what humans reviewed

| Area | AI-assisted work evidenced in this session | Human review focus / evidence needed |
|---|---|---|
| Regime integration | Separated classifier and sizing policy, added their adapter and integration tests | Confirm directional-label mapping, sizing tables, and hysteresis behavior against the desk requirements |
| Merge reconciliation | Combined DCA logic with the declining-trend guard; preserved teammate changes | Review entry/exit interactions and changed live configuration in the merge diff |
| Credentials | Local `.env` setup, runtime-key validation, removal of tracked config credentials | Verify deployment secrets and arrange rotation of any exposed key |
| Verification | Ran bot tests, Ruff, dashboard syntax checks and tests; repaired reproduced failures | Review assertions and actual GitHub check results; passing tests do not prove trading profitability |
| Documentation | Compared implementation with pre-reading and corrected stale claims | Confirm submission statements, measured results, and named reviewer attribution |

The review column identifies what needs human assessment; it does not assert
that each review occurred. User instructions authorized work and syncing, but
that is not evidence of line-by-line human review. Do not claim every generated
change was human-reviewed without a corresponding review record.

## Implemented behavior and limits

- Regime sizing is enabled. The adapter confirms a changed label over three
  consecutive observations; the configured immediate label is WARMUP. SHOCK
  is also debounced because the crash-buy policy permits shock entries.
- The news engine combines immediate keyword biases with asynchronous,
  confidence-filtered, per-symbol LLM views. Biases can modify or generate
  signals; forced risk exits take precedence. Model confidence is not a
  validated probability of profitable trading.
- SSE order-book updates populate a freshness-aware cache. Depth sums eligible
  resting orders within the limit price and excludes our orders when team
  identity is known. Strategy entries apply spread limits and depth sizing.
  Stale/missing books fall back to normal sizing; news-generated or enlarged
  buys do not pass through a final shared depth filter.
- DCA, trend following, and leader-follower rules express hypotheses from or
  extending the pre-reading. Unit tests establish code behavior, not measured
  excess returns. Do not claim a profitable or unique edge without comparative
  session results.
