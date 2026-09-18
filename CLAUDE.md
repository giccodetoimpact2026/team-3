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

## Crash-buy strategy increment

Implemented with Claude (Cowork) from the team's request to buy aggressively into a
sharp sell-off, using `team-11-analysis.md` (the 13:42 CRVX crash and V-recovery) and
James Wee's page-2 falling-knife warning as inputs. `baseline-bot/strategies/crash_buy.py`
opens an "episode" when price falls `crash_drop_pct` off the lookback high, ladders in
with escalating tranches (confirmation wait, tranche cap, abandon threshold), and sells
once `recovery_fraction` of the drop is reclaimed. It places no stop of its own and
relies on `risk.exits`. Registered as `type: crash_buy` in `main.py` and as the
`crash_buy` regime family (the only family whose SHOCK multiplier is 1.0). It is NOT
assigned to any instrument in `config.yaml` — a commented CRVX block shows the starting
parameters. 26 unit tests in `tests/test_crash_buy.py`; verified only on synthetic
paths, never against the sandbox. Human review of parameters, the news-engine
interaction (a bearish bias suppresses buys) and position caps is required before it
trades live.
