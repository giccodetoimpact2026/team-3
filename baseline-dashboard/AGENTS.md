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

Dashboard — replace 30s polling with the exchange **SSE** feed, clear stale DOM on
reconnect, show **SGT** timestamps, be robust to missing instruments, add a
**heartbeat-based** health dot and an always-visible **kill switch**, a
conversational **MCP panel** (cite the tool + show confidence), and a predictive
view. See `HANDOVER.md` for what's rough and what's a stub.

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
