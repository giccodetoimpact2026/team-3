# AGENTS.md
### Context for AI coding assistants (GitHub Copilot et al.)

> This file gives your AI assistant the context it needs to be useful here. Keep it
> current as you work — a good `AGENTS.md` is part of what's assessed (spec-driven,
> context-engineered, AI-assisted development with human oversight). Each baseline
> repo ships its own filled-in copy.

## What this project is

A trading **[bot / dashboard / MCP client]** for the GIC hackathon simulated exchange.
You are **extending an existing codebase**, not starting fresh. The authoritative specs
are:
- `student-opening-brief.md` — the scenario and what we're being asked to do
- `exchange-api-reference.md` — the API contract (this wins over old code comments)
- `james-wee-strategy-notes.md` — strategy logic (page 3 = regime detection)
- the bug tracker — 20 items (18 open + 2 guardrails); 7 must-fix

## What we're building (this repo)

`[Fill in: e.g., "Bot — fix reliability (reconnect, graceful shutdown), fix the ELXR
lookback, extend strategy, handle announcement events." / "Dashboard — SSE live data,
SGT, heartbeat health, kill switch, conversational MCP panel, predictive view."]`

## Ground rules (do not violate)

- Auth is `X-API-Key` on every **authenticated** call. Not `Authorization`, not `X-Team-Token`. (Only the SSE feed and the probes — `/health`, `/ready`, `/session` — take no key; browser `EventSource` can't send one anyway. `/instruments` and `/orderbook` need the key like every other read.)
- Market data comes from the **SSE feed**, not REST polling. Reconnect with `Last-Event-ID`.
- There is **no candles endpoint** — accumulate price history from `price` events.
- Every order is a limit order (there is no `type` field); `side` lowercase; one `idempotency_key` per order.
- User-facing times and exports are **SGT**, period decimal separator.
- Resolve instruments via `GET /instruments` — never hardcode names.
- Keep business logic in the bot; the dashboard displays and controls.

## Conventions

- `[language/runtime, e.g. Python 3.11 / Node]`
- Strategy code is modular: a `BaseStrategy` with subclasses, selected by a router —
  not a giant if-else.
- Small, reviewed commits with clear messages. **Review what the assistant generates;
  don't merge unread.**

## Judging documents — keep them in sync (do this by default)

Part of this submission is scored by an **AI judge that reads four documents, not the
code**. Treat them as living deliverables: **whenever you make a change in this repo,
update the affected judging documents in the same step**, without being asked, so they
never drift from what you actually built.

- `README.md` — team name, members, what works vs what's still aspirational, and your prioritisation
- `SPEC.md` — requirement interpretation, the target user, and what you descoped and why
- `CLAUDE.md` — your Claude / AI configuration and how you use AI in this repo
- `presentation.html` — a self-contained demo presentation (no external assets)

At submission these four go in a single zip named `<YourTeamName>.zip` — exactly these
four files at the root, nothing else, no source code, filenames exact and case-sensitive.
See `student-opening-brief.md` §9.

## How to run

```
[fill in: install command, run command for sandbox vs live]
```
Treat **sandbox as dev** and **live as prod** — config-switch between them; don't test
against the live session.

## Testing

```
[fill in: test command]
```
Cover the core logic that's easy to get wrong: strategy signals, P&L math, announcement
parsing, SGT/number formatting. A CI check (lint + tests on push) is expected.

## When you (the assistant) are unsure

Prefer asking for the spec reference over guessing. If a change touches orders, risk, or
shutdown behaviour, flag it for human review — those are the places a wrong autocomplete
costs real P&L.
