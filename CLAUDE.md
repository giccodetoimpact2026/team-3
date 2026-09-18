# AI development record

This increment was implemented with Codex, using the local API reference, stakeholder
records and strategy notes. No project-specific Claude configuration is installed.
The user prioritized getting the bot running. AI assisted with implementation and
mock-based tests. Human review of orders, sizing, recovery and shutdown remains required;
this document does not assert that review has occurred.

Run instructions and checks are in baseline-bot/README.md. Never commit the team key.
No application LLM feature has been implemented and no organizer LLM credits were used.
Future scored AI features must use the organizer-provided LLM service.

## Dashboard increment (P4/P5)

Implemented with Claude Code from `team-execution-plan.md`, the exchange API reference,
the MCP server guide and the stakeholder records (Yuhui's feed facts, Gerald's export
specification). AI wrote the dashboard modules and their Node unit tests; the author
reviewed the SSE/rate-limit design, the stale-value rules and the CSV cost-basis logic
and verified the page in a browser against the sandbox exchange with the MCP server
running, including forced feed and MCP failures. No LLM feature is part of the dashboard
yet; the conversational panel remains open work and must use the organizer LLM service.

## Heartbeat, order journal and lifecycle hardening

Implemented with Claude Code from `team-execution-plan.md` (P1 H2 heartbeat and
feed-stall watchdog, P4 H2 order events, P3 H2 config validation) and the stakeholder
records (note 7: heartbeat-based health; note 8: bot-internal state over a direct
bot→dashboard channel, not MCP). AI wrote the heartbeat module, the order journal and
burst throttle in the order manager, the control routes, the dashboard view and
history changes and their tests. Bot and dashboard unit suites pass; a bot smoke run
against the sandbox exchange exercised `/status`, `/orders` and `/halt`. Human review
of the halt ordering and the watchdog thresholds remains required.

## Instrument risk model

Implemented with Claude Code (Fable 5.1) from the stakeholder records (Priya's stops,
drawdown and DRFT sizing; Clarissa's and Farrukh's correlation concern; Adeline's
regime request), James's notes (pages 1 to 4), the opening brief's instrument table,
the exchange API reference (portfolio fields, `book_update`, rate tiers) and the
existing bot code. AI wrote `docs/risk-model-design.md` first, then the modules and
tests test-first (`risk/stats.py`, `risk/profiles.py`, `risk/model.py`,
`risk/exits.py`, the OrderManager hooks, config validation), and calibrated the
defaults from two read-only samples of the sandbox feed (per-tick and 10-second-bar
volatility, spreads, depth) that are recorded in the design doc. Bot unit suite:
219 tests pass; lint clean. A read-only smoke run built the model from the shipped
config against the live sandbox feed and portfolio. No orders were placed by AI during
this work and no organizer LLM credits were used. Human review required: the stop
floors and regime bands, the drawdown action (`block_buys` chosen over `halt`), and
whether granted starting positions should be subject to the cost-basis stop.
