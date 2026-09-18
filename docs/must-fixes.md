# Seven agreed must-fixes

Source: [stakeholder-interview-records.md](https://github.com/giccodetoimpact2026/welcome/blob/master/stakeholder-interview-records.md), alignment meeting **00:24**. ACs below are proposed completion checks derived from the pre-read. Mark each ☐ → ☑ once verified; mark a fix done when all its ACs pass.

| Done | Fix | Where | Acceptance criteria (ACs) |
|---|---|---|---|
| ☐ | Replace the 30-second refresh approach with live market updates | Dashboard | ☐ **1.** Incoming SSE price events update the dashboard without waiting for a 30-second timer. ☐ **2.** Account values match the latest successful MCP result and display its update time; the supported refresh mechanism is confirmed against the supplied reference. |
| ☐ | Recover from feed disconnections | Bot; dashboard where applicable | ☐ **1.** After a simulated disconnect, the feed reconnects with backoff and sends the last received event ID in `Last-Event-ID`. ☐ **2.** Missed events are replayed and live updates resume; replay does not double-count previously processed events. |
| ☐ | Finish graceful shutdown | Bot | ☐ **1.** Ctrl-C or SIGTERM blocks new order submissions before cancellation begins. ☐ **2.** Open-order cancellations are attempted and their results are recorded; unresolved orders or failures are reported before exit. |
| ☐ | Remove misleading stale values on reconnect | Dashboard | ☐ **1.** On reconnect, old values are cleared or explicitly marked stale before refresh completes. ☐ **2.** The dashboard shows unavailable/reconnecting until fresh data arrives; failed refreshes do not present old values as current. |
| ☐ | Change ELXR mean-reversion lookback from 5 to **15 samples** | Bot strategy configuration | ☐ **1.** ELXR mean reversion uses exactly the latest 15 price samples to calculate its rolling mean. ☐ **2.** Fewer than 15 samples produce no mean-reversion trade signal; a known 15-sample sequence produces the expected mean and signal. |
| ☐ | Handle instruments absent from the portfolio response | Bot and dashboard | ☐ **1.** Every discovered instrument is represented, even when absent from the portfolio response. ☐ **2.** Missing position quantities default to zero without crashes or undefined cells; missing prices display as unavailable, not zero. |
| ☐ | Replace UTC display/export timestamps with SGT | Dashboard and exports | ☐ **1.** Dashboard timestamps represent the correct Singapore local time for a known UTC timestamp. ☐ **2.** Export timestamps use `YYYY-MM-DD HH:MM:SS.mmm SGT`, including milliseconds and the explicit timezone label. |

The 30-second issue is described as P&L polling in the handover, but the listed SSE event types do not include portfolio updates. Confirm the intended account refresh mechanism in the supplied API/MCP reference.

Additional work belongs in the [feature backlog](feature-backlog.md).
