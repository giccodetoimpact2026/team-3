# Implementation specification — current increment

Target user: the trader running the team's intraday bot. Immediate priority is bot
startup and reliable NOVA/ELXR operation, per the team's instruction.

Implemented: BUG-001 shutdown cancellation, BUG-011 SSE reconnect/replay, BUG-012
ELXR lookback 15, and the bot portion of BUG-019 (missing position = zero). Supporting
work includes environment credentials, runtime symbol validation, per-symbol evaluation
throttling, no pending-order stacking, unknown submission reconciliation, session-history
reset, observation mode and sandbox-only trading unless live is explicitly enabled.

Acceptance: local strategy/runtime tests and lint pass; an authenticated sandbox check
and trading smoke test remain pending a locally configured team key. No profitability
claim is made. SIGKILL or network loss can prevent shutdown cancellation; failures must
be visible. Shutdown cancels all resting team orders; use one order-writing bot.

Deferred from this increment: dashboard mandatory fixes, kill-switch UI, heartbeat,
ATR routing, additional instruments, stop-losses, portfolio caps and AI/MCP panel.
These are unfinished, not removed from the overall hackathon scope. DRFT remains off.
Emergency halt should be immediate; parameter changes need two-step confirmation.

Architecture: Python strategy instances consume public exchange SSE; authenticated REST
reads gate limit-only, integer-cent, long-only orders. The existing dashboard and MCP
server remain separate processes and are unchanged in this increment.
