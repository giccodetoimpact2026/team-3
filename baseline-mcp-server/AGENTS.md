# AGENTS.md
### Context for AI coding assistants (GitHub Copilot et al.)

> This file gives your AI assistant the context it needs to be useful here. Keep it
> current as you work — a good `AGENTS.md` is part of what's assessed (spec-driven,
> context-engineered, AI-assisted development with human oversight).

## What this project is

The **read-only MCP server** for the GIC hackathon simulated exchange. It is
**provided and working** — you **run it yourself** and *consume* it from your
dashboard's conversational panel (requirement **M4**); the protocol plumbing is
written, so you don't have to build it. The authoritative specs are:
- `mcp-server-reference.md` — how the server is used (dual surface, tool contract)
- `exchange-api-reference.md` — the exchange API the tools read from (this wins over
  old code comments)
- `student-opening-brief.md` — the scenario and what we're being asked to do

## What we're building (this repo)

The four **read tools** (`get_portfolio`, `get_positions`, `get_pnl`,
`get_session_summary`) are already working. Your work is **bonus**: add a new read
tool, wire the inert **control tools** (`pause_instrument` / `resume_instrument` /
`kill_switch`) so the bot honours them, or stand up a second (e.g. research/news)
MCP server.

## Ground rules (do not violate)

- **Read-only.** The server reports state; it never places or cancels orders.
- Auth to the exchange is `X-API-Key` (the server's configured team key). Not
  `Authorization`, not `X-Team-Token`.
- Every tool returns `{ source, as_of, data }` — `source` names the backing
  endpoint (cite it), `as_of` is an **SGT** timestamp.
- Money is **integer cents** on the exchange wire; tools return **dollars** for the
  human-facing panel.
- **Dual surface, one port (default 8090):** the streamable-HTTP MCP endpoint at
  `/mcp` (MCP-native clients) and the REST facade `POST /tools/{name}` (the browser
  dashboard — it can't speak raw MCP).
- Instruments absent from `/portfolio` are returned as **zeroed** entries, not
  omitted (BUG-019 handled server-side) — keep that when adding tools.
- Control tools stay **inert** (return the not-wired notice) unless you're doing the
  bonus that actually wires them to the bot.

## Conventions

- Python 3.11, `fastmcp`. Keep tool *logic* in `tools.py` (pure, unit-testable),
  separate from the MCP/HTTP wiring in `server.py`.
- Small, reviewed commits with clear messages. **Review what the assistant
  generates; don't merge unread.**

## How to run

```
pip install -r requirements.txt
# Edit config.yaml: exchange.base_url (sandbox/live) + your team api_key.
python server.py            # serves on :8090
```
Treat **sandbox as dev** and **live as prod** — config-switch via `config.yaml`.

## Testing

```
pip install -r requirements.txt
pytest -q
```
Cover the tool contract (`{source, as_of, data}`), the cents→dollars conversion, and
missing-instrument zero-fill. A CI check (lint + tests on push) is in
`.github/workflows/ci.yml` — extend it.

## When you (the assistant) are unsure

Prefer asking for the spec reference over guessing. If a change would make the
server place orders or act on the bot (control tools), flag it for human review —
this server is read-only by design.
