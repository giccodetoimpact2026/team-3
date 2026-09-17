# AGENTS.md
### Context for AI coding assistants (GitHub Copilot et al.)

> This file gives your AI assistant the context it needs to be useful here. Keep it
> current as you work — a good `AGENTS.md` is part of what's assessed (spec-driven,
> context-engineered, AI-assisted development with human oversight).

## What this project is

A trading **bot** for the GIC hackathon simulated exchange. You are **extending an
existing codebase**, not starting fresh. The authoritative specs are:
- `student-opening-brief.md` — the scenario and what we're being asked to do
- `exchange-api-reference.md` — the API contract (this wins over old code comments)
- `james-wee-strategy-notes.md` — strategy logic (page 3 = regime detection)
- the bug tracker — 20 items (18 open + 2 guardrails); 7 must-fix

## What we're building (this repo)

Bot — fix reliability (reconnect the SSE feed with `Last-Event-ID`, graceful
shutdown that cancels open orders), fix the ELXR lookback, defend and extend the
momentum / mean-reversion strategies, and handle `announcement` events (S1: an
LLM-assisted directional signal, gated on confidence). See `HANDOVER.md` for the
outgoing developer's notes on what works and what's half-finished.

## Ground rules (do not violate)

- Auth is `X-API-Key` on every **authenticated** call. Not `Authorization`, not `X-Team-Token`. (Only the SSE feed and the probes — `/health`, `/ready`, `/session` — take no key; browser `EventSource` can't send one anyway. `/instruments` and `/orderbook` need the key like every other read.)
- Market data comes from the **SSE feed**, not REST polling. Reconnect with `Last-Event-ID`.
- There is **no candles endpoint** — accumulate price history from `price` events.
- Every order is a limit order (there is no `type` field); `side` lowercase; one `idempotency_key` per order.
- **Prices/money are integer cents on the wire** ($42.50 → `4250`).
- The exchange is **long-only**: a SELL only reduces/closes a held long; the first
  action on any instrument must be a buy.
- User-facing times and exports are **SGT**, period decimal separator.
- Resolve instruments via `GET /instruments` — never hardcode names.

## Conventions

- Python 3.11.
- Strategy code is modular: a `BaseStrategy` with subclasses, selected in `main.py`
  — not a giant if-else.
- Small, reviewed commits with clear messages. **Review what the assistant
  generates; don't merge unread** — orders, risk, and shutdown are where a wrong
  autocomplete costs real P&L.

## How to run

```
pip install -r requirements.txt
# Point config.yaml at the sandbox exchange + your team api_key, then:
python main.py
```
Treat **sandbox as dev** and **live as prod** — config-switch via `config.yaml`;
don't test against the live session.

## Testing

```
pip install -r requirements.txt pytest
pytest -q
```
Cover the core logic that's easy to get wrong: strategy signals, P&L math,
announcement parsing, SGT/number formatting. CI (lint + tests on push) is in
`.github/workflows/ci.yml` — extend it.

## When you (the assistant) are unsure

Prefer asking for the spec reference over guessing. If a change touches orders,
risk, or shutdown behaviour, flag it for human review.
