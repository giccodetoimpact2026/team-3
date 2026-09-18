# AI development record

This increment was implemented with Codex, using the local API reference, stakeholder
records and strategy notes. No project-specific Claude configuration is installed.
The user prioritized getting the bot running. AI assisted with implementation and
mock-based tests. Human review of orders, sizing, recovery and shutdown remains required;
this document does not assert that review has occurred.

Run instructions and checks are in baseline-bot/README.md. Never commit the team key.
No application LLM feature has been implemented and no organizer LLM credits were used.
Future scored AI features must use the organizer-provided LLM service.

## Strategy classifier increment

Codex implemented and tested `baseline-bot/strategies/regime.py` from the team's
strategy-optimisation playbook. The module uses ATR-like price volatility,
directional efficiency and a pre-shock volatility baseline; it returns explanatory
metrics and a typed transition instead of placing orders. The implementation does
not yet route strategies or claim predictive profitability. Human review remains
required before its output changes live position sizing or order decisions.

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
