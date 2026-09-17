# GIC #CODETOIMPACT 2026 — Your Team Repository

This is your team's working repository. It contains three **baseline projects** to extend
and the **reference documents** that define the scenario, the platform API, and the known
bugs. You are extending an existing, half-finished trading system — not starting from
scratch.

## Start here

1. **[`docs/student-opening-brief.md`](docs/student-opening-brief.md)** — the brief: the
   situation, the two challenges, the market, the day, and how you're judged.
2. **[`docs/stakeholder-interview-records.md`](docs/stakeholder-interview-records.md)** —
   the requirement-gathering corpus (interviews, a transcript, notes, an email, a chat
   thread). Signal and noise are woven together — reconciling it is the core exercise.
3. **[`docs/bug-tracker-export.csv`](docs/bug-tracker-export.csv)** — 20 known issues (7
   are must-fix, **unflagged** — triage is part of the task).

## Baseline projects (extend these)

- **`baseline-bot/`** — Python trading bot (trades NOVA + ELXR; has known bugs). See its
  `HANDOVER.md` and `AGENTS.md`.
- **`baseline-dashboard/`** — JavaScript monitoring dashboard (minimal; has known bugs).
- **`baseline-mcp-server/`** — read-only MCP server + client adapter (consume it for the
  conversational panel).

Each baseline ships its own `HANDOVER.md` (what's rough) and `AGENTS.md` (context for your
AI assistant).

## Reference documents (`docs/`)

- **`exchange-api-reference.md`** — the platform API contract (REST, SSE, auth, error
  codes, rate limits, MCP read tools). If old code comments disagree, this wins.
- **`llm-api-reference.md`** — the organizer-run LLM service (Anthropic Messages shape;
  same `X-API-Key` as the exchange).
- **`mcp-server-reference.md`** — the read-only MCP server guide (you consume it).
- **`james-wee-strategy-notes.md`** — the quant's transcribed notes (page 3 = the ATR
  regime-detection formula).
- **`repo-handover-notes.md`** — the outgoing team's handovers (also split into each
  baseline's `HANDOVER.md`).
- **`AGENTS.md`** — the spec-context template for your AI assistant (each baseline already
  ships its own filled-in copy).

## Platform facts to internalise

- Money and prices are **integer cents** on the wire; a $42.50 limit is `4250`.
- The real-time feed is **SSE** (`price`, `trade`, `book_update`, `session`,
  `announcement`) with `Last-Event-ID` replay; there is **no candles endpoint** —
  accumulate price history from the feed.
- Orders are **limit-only**, long-only; `side` is lowercase.
- Everything user-facing is **SGT**. Resolve instruments via `GET /instruments` — never
  hardcode names.

Good luck — build something you'd be proud to put in front of a trading desk.
