# AI Development Record

## Tools Used

| Tool | Purpose | Scope |
|------|---------|-------|
| **GitHub Copilot** | Inline code completion | Used throughout for boilerplate, test scaffolding, and pattern completion |
| **OpenAI Codex** | Bot implementation (initial increment) | NOVA/ELXR strategies, SSE reconnect, session lifecycle, order manager, shutdown. Driven from local API reference, stakeholder records, and strategy notes |
| **Claude Code** | Dashboard implementation (P4/P5 increment) | SSE feed integration, stale-value handling, per-instrument P&L, order lifecycle table, CSV export, kill switch UI, and all 42 Node unit tests |

## AI-Assisted Development Practice

### Spec-driven, human-reviewed

Every AI-assisted change followed this workflow:
1. **Spec first** — the intended behaviour was defined before generation (from must-fixes, feature backlog, or stakeholder requirements)
2. **Generate** — AI produced implementation and tests from the spec
3. **Review** — human reviewed the generated code for correctness, security, and alignment with the spec
4. **Verify** — tests run locally and in CI; dashboard features verified in a browser against the sandbox exchange

### What AI wrote vs what humans reviewed

| Component | AI Wrote | Human Reviewed |
|-----------|----------|---------------|
| Bot strategies (NOVA momentum, ELXR mean-reversion) | Implementation and mock-based tests | Order sizing, entry/exit logic, edge cases |
| SSE feed (reconnect, backoff, replay) | Reconnect loop, exponential backoff, jitter, Last-Event-ID | Backoff parameters, dedup logic, thread safety |
| Order manager | Idempotency keys, pending tracking, one-active-per-symbol | Exposure calculation, race conditions, cancel-all path |
| Graceful shutdown | SIGINT/SIGTERM handlers, lifecycle state machine | Order cancellation completeness, unknown-order handling |
| Dashboard SSE | EventSource connection, event parsing, DOM updates | Rate-limit compliance (300 reads/30s), stale-value clearing |
| Dashboard P&L | MCP get_pnl integration, portfolio fallback, source labels | Cost-basis calculation, MCP failure handling |
| CSV export | Gerald's fixed schema, SGT formatting, period decimals | Column order, status code mapping, reconciliation accuracy |
| Kill switch UI | HALT BOT button, fetch to bot control endpoint, state pill | One-click UX (no confirmation), failure display |
| Unit tests (bot) | 15+ pytest tests with mocked exchange | Assertion correctness, edge case coverage |
| Unit tests (dashboard) | 42 Node tests (format, view, trades) | Test isolation, assertion accuracy |

### What AI did NOT do

- **No application LLM feature was implemented** — no organizer LLM credits were used
- **No production trading decisions** — AI assisted with code, not with live order parameters
- **No unreviewed merges** — all AI-generated code was reviewed before merging to main
- **No secrets in code** — team API key is injected at runtime via environment variable

## Project-Level Configuration

No project-specific Claude configuration (.claude/ directory, MCP tool routing, or custom commands) was installed beyond the root CLAUDE.md development record. The AI tools were used in their default configurations with manual prompting.

### CLAUDE.md (root)

The root CLAUDE.md served as a development log, not a configuration file. It recorded:
- Which AI tool was used for each increment
- What the human reviewed vs what was AI-generated
- That the team key must never be committed
- That future AI features must use the organizer LLM service (llm.codetoimpact.sg)

## Future AI Features

The following AI features are architecturally prepared but not yet implemented:

| Feature | Readiness | Dependency |
|---------|-----------|------------|
| MCP conversational panel | MCP read server running, REST facade available, dashboard panel placeholder | Organizer LLM service (llm.codetoimpact.sg) |
| Announcement impact analysis | SSE announcement events parsed and rendered | Organizer LLM for supply-chain reasoning |
| Trade explainability | Order journal with strategy/signal metadata | LLM to generate natural-language explanations |

All future AI features must:
- Use the organizer-provided LLM service (`https://llm.codetoimpact.sg`)
- Cite sources and timestamps for factual claims
- Label estimates with uncertainty and method
- Disclose stale or missing data
- Never present unsupported confidence percentages
