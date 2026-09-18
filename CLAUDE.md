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
