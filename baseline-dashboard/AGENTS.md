# AGENTS.md
### Context for AI coding assistants (GitHub Copilot et al.)

> This file gives your AI assistant the context it needs to be useful here. Keep it
> current as you work — a good `AGENTS.md` is part of what's assessed (spec-driven,
> context-engineered, AI-assisted development with human oversight).

## What this project is

A trading **dashboard** for the GIC hackathon simulated exchange. You are **extending
an existing codebase**, not starting fresh. The authoritative specs are:
- `student-opening-brief.md` — the scenario and what we're being asked to do
- `exchange-api-reference.md` — the API contract (this wins over old code comments)
- `mcp-server-reference.md` — the read tools the assistant panel consumes
- the bug tracker — 20 items (18 open + 2 guardrails); 7 must-fix

## What we're building (this repo)

Dashboard. Done: SSE feed instead of polling, stale DOM cleared on reconnect,
SGT timestamps, dynamic instruments, session counters, per-instrument P&L via
MCP (with `/portfolio` fallback), order lifecycle table, Gerald's CSV export.
Also done: heartbeat-based bot pill, always-visible kill switch, the
conversational **MCP panel** (tool citations + rule-based confidence), the
5-minute forecast with a 90% band, price chart with fills, persona views,
heat map, fill rates, exposure vs caps, alerts, correlation, performance
breakdown, executive briefing. See `HANDOVER.md` for what's rough.

Module map: `format.js` (money/SGT), `view.js` (position rows, SSE cursor,
HTML escaping), `trades.js` (lifecycle history, counters, cost basis, CSV),
`views.js` (persona views: which sections each shows), `prices.js` (SSE price
history, 5-minute forecast, sparklines, correlation), `chart.js` (SVG price
chart with our fills, resting orders and the forecast cone), `analytics.js`
(heat map, fill rates, exposure vs caps, alerts, open orders, performance
breakdown, session summary),
`assistant.js` (LLM request shapes, tool loop, citations, rule-based
confidence), `mcp.js` (MCP REST facade), `api.js` (exchange REST), `panels.js`
+ `assistant-panel.js` (DOM for the persona panels and the M4 chat),
`dashboard.js` (controller). Keep new logic in the pure modules and test it in Node.

Persona views (brief §8 decision 5): Executive (default), Trader, Risk,
Analyst, Ops. A view only hides sections; every section keeps rendering. The
bot pill and kill switch sit outside every view.

LLM calls go through `server.js` `/llm/*` -> `https://llm.codetoimpact.sg`
(the key is injected server-side; only this service counts for AI scoring).
Assistant answers use `sonnet` with tools; the executive briefing is a
one-shot `haiku` call. Confidence shown in the panel is computed in
`assistant.js` from tool success and `as_of` freshness, never asked of the
model. Position caps for the Risk view come from `GET /bot/status`
`limits.max_position`.

## Ground rules (do not violate)

- Auth is `X-API-Key` on every **authenticated** call. Not `Authorization`, not `X-Team-Token`. (The SSE feed and the public GETs need no key; browser `EventSource` can't send one anyway.)
- Market data should come from the **SSE feed**, not REST polling. Reconnect with `Last-Event-ID`.
- There is **no candles endpoint** — accumulate price history from `price` events.
- **Prices/money are integer cents on the wire** ($42.50 → `4250`); format for display.
- User-facing times and exports are **SGT**, period decimal separator.
- Resolve instruments via `GET /instruments` — never hardcode names.
- Keep business logic in the bot; the dashboard displays and controls.

## Conventions

- Vanilla HTML/CSS/JS with jQuery (baseline). You may refactor to React.
- Small, reviewed commits with clear messages. **Review what the assistant generates;
  don't merge unread** — the kill switch and anything touching orders are where a
  wrong autocomplete costs real P&L.

## How to run

Open `index.html` in a browser (or serve the folder):

```
python -m http.server 5500   # then open http://localhost:5500
```
Edit `js/config.js`: `API_BASE_URL` (sandbox/live), `API_KEY`, and `MCP_BASE_URL`.
Treat **sandbox as dev** and **live as prod**.

## Testing

```
npm run check   # syntax check (node --check)
npm test        # unit tests (node --test)
```
Cover the logic that's easy to get wrong: P&L formatting, SGT conversion, missing-
instrument handling. CI (check + tests on push) is in `.github/workflows/ci.yml`.

## When you (the assistant) are unsure

Prefer asking for the spec reference over guessing. Flag anything touching the kill
switch, orders, or health signalling for human review.
