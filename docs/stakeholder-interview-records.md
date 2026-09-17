# GIC Trading Hackathon — Stakeholder Records
### Student-facing | Requirement-gathering artifacts

> These are the raw records from the requirement-gathering sessions: interview notes,
> a team meeting transcript, a post-meeting note, an email submission, and an informal
> chat thread. **They do not agree with each other, and not everything in them is
> correct.** Some asks are genuine and important; some are personal preference, scope
> inflation, or a simple misunderstanding. One of the notes was written in a
> hurry and doesn't perfectly reflect what was said. Reconciling all of this — and
> deciding what to build — is part of the exercise. Be ready to defend your reading in
> the Q&A.

---

# PART 1 — INTERVIEWS

---

## Interview note — CIO, Raymond Chew
*In-person, executive floor, ~40 min. Notes by BA.*

Raymond opened by calling the current dashboard "a nightlight, not a dashboard — a
green dot and a number, while we run positions across five instruments." He wants the
full picture: per-instrument P&L, live, at a glance. Asked for a **portfolio heat map**
— colour telling him instantly which instruments are winning and losing — and "a
Bloomberg-like feel."

He wants **real charts** with the bot's trades marked on them, and he's been asking for
this "for six months." Strongly wants to be able to *ask the system what it's doing*:
"When the bot sells DRFT at 14:32, I want to know why — plain English. 'Bot sold DRFT'
tells me nothing." Described wanting **commentary on every trade**.

Also wants per-instrument performance comparison ("which are we good at, which are we
losing on") — and wants it risk-adjusted: "give me a Sharpe per instrument, not just raw
P&L." On strategy he waved it off — "that's Derek's territory, I trust him."
Asked what to keep from the old system, he said: "The green dot. Keep it. Make it
bigger."

---

## Interview — Deputy CIO, Priya Sundaram
*Video call, ~35 min. Near-verbatim.*

**BA:** Where do you want to start?
**Priya:** With the fact that this system ran eighteen months with no stop-losses and I
only found out last month. Per-instrument stop-losses, and I want them **visible in the
dashboard** — not buried in a config file. If I can't see the risk parameters, they
might as well not exist.
**BA:** And changing them?
**Priya:** Two-step confirmation. One click to propose, one to confirm. Non-negotiable
for any parameter change.
**BA:** The emergency stop too?
**Priya:** No — the emergency halt should be immediate. I agree with Derek on that. The
two-step is for changing a limit or a threshold, not for stopping everything in a
crisis.
**BA:** Anything else?
**Priya:** An **audit log** — every parameter change and manual action, timestamped,
attributed, immutable. And **alerts**: flag me if portfolio drawdown passes 15% of
starting capital, if a single instrument drops too far, or if the bot goes quiet when
it shouldn't be. And DRFT — size it far smaller than NOVA. It's too thin. Maybe a fifth
of what you'd run on NOVA, roughly. And on reporting — I don't just want Sharpe, I want
our **information ratio**. I want to know we're beating the benchmark, not just making money.

---

## Standing meeting note — Senior Trader (strategy), Derek Tan
*Trading floor, ~15 min, standing. Notes by BA.*

Derek wants the bot trading more than two names. NOVA momentum, but with a wide lookback
— "it moves slow and far." **AXON pairs off NOVA** — energy costs drive freight, so use
NOVA as a leading signal: NOVA breaks a new high, go long AXON. **CRVX** momentum
(growth behaviour). **ELXR and AXON** are the stable, liquid names — good for
**market-making**, but "don't just uncomment James's module, it had an inventory bug:
tighten the ask when you're long, tighten the bid when you're short."

**DRFT: leave it alone.** "Too illiquid. The bot moves its own price. That's not
trading, that's self-harm." Not forbidden — just not until it's understood.

On reliability: the bot crashed twice last month on network disconnects. "It should
never give up. Keep trying — back off so you don't hammer the API, but keep trying."

On the dashboard he was dismissive: "One number is enough for me. I watch the screen."

---

## Post-meeting note on Derek's session
*Prepared by a junior team member, circulated to the group.*

> Met Derek (Head Trader). He wants the bot extended to the other instruments —
> market-making on the liquid ones, momentum on NOVA/CRVX, pairs on AXON. Also wants
> reconnect logic.
>
> On DRFT: **Derek said it's fine to trade DRFT at normal size** — didn't flag any
> special concerns.

---

## Interview note — Senior Trader (execution), Adeline Loh
*60 min, Adeline came with printed notes. Notes by BA.*

Adeline's biggest issue: the **ELXR mean-reversion is broken**. "It's using a 5-sample
lookback to define the mean. ELXR's natural cycle is closer to 15. The bot is buying
dips that aren't dips — it's buying into a downtrend and calling it reversion. James's
notes say 15. Someone put 5 in the code." She says it's been in the bug tracker for two
months.

Wants **candlestick** charts (not line), session-scoped, 1m/5m/15m selectable,
switchable per instrument — with a **VWAP overlay** ("my primary execution-quality
metric") and **per-instrument fill-rate** tracking in a table (orders sent, fully
filled, partially filled, rejected, fill rate %). "If we're getting lots of partial
fills on NOVA, our sizing is too big for the book."

Also wants **regime detection**: "When NOVA goes sideways — low volatility, no trend —
momentum bleeds money slowly. The bot should detect that and switch or cut size. James
had an ATR-based prototype in his notes. Never implemented — Alex said it was too
complex."

---

## Interview note — Senior Trader (risk), Clarissa Ng
*Formal, 90 min, brought a binder. Notes by BA.*

Clarissa's headline: "No kill switch. That is the single most dangerous omission. If the
bot enters a loss spiral there's no way to stop it without SSH-ing into the server."
She wants **one button, always visible, immediate, no confirmation dialog** — "when I
need it I need it now." Agrees with Derek that the emergency halt takes no confirm.

Also wants **per-instrument pause/resume** ("sometimes NOVA's fine and ELXR is the
problem"), and **order-rate anomaly detection** that auto-pauses and alerts if the bot
suddenly fires far more orders than normal — "that's what nearly happened in March; the
strategy looped and placed dozens of orders in seconds before anyone noticed."

On DRFT she went further than Derek: "It should be **hard-blocked at the system level**.
If the config has DRFT enabled, the system should refuse to start. I don't trust whoever
runs this next to remember the guidance." Also wants correlation monitoring — "NOVA and
AXON move together; if we're long both we've doubled a position."

---

## Whiteboard session note — Head of Tech, Aaron Phua
*Tech hub, ran ~2 hours. Notes by BA.*

Aaron's minimum bar (after some negotiation on scope): the bot must not crash on network
errors — proper exception handling, retry with backoff, graceful shutdown. The dashboard
must take market data over the **live feed, not REST polling** — "if I see
`setInterval(fetch, 30000)` I will physically wince." Bot and dashboard are **separate
processes**; "if both crash together, they were coupled wrong." The dashboard is a
display and control layer — **all logic lives in the bot**; the dashboard sends commands
and the bot validates and executes.

His stated "production minimum" also included container orchestration, a proper ORM, a
monitoring sidecar, and 70% unit-test coverage — "nothing I'd deploy has less." He
acknowledged the one-day timeline when pressed.

Strategy code should be modular: a `BaseStrategy` with subclasses, selected by a router
— "not a giant if-else in `main.py`."

---

## Written submission — Enterprise Solution Architect, Yuhui Chen
*Emailed; Yuhui declined a long meeting and sent precise notes.*

> Facts your teams will need that aren't in the old code:
> 1. Use `GET /instruments` at startup — Alex hardcoded two names. Don't.
> 2. The exchange real-time feed is **SSE (Server-Sent Events)** — not WebSocket, not
>    polling. Subscribe to `price`, `trade`, `book_update`, `session`, `announcement`.
>    On reconnect send `Last-Event-ID`; the exchange replays what you missed.
> 3. Rate limits are **per team, per rolling 30 s**, in three tiers: 100 orders, 600
>    order-book reads, 300 of everything else (cancels included) — burstable, so spend
>    them as fast as you like, then wait for the oldest to age out.
> 4. **No candles endpoint exists.** Accumulate price history from `price` events.
> 5. Auth header is `X-API-Key` on every **authenticated** call — not `X-Team-Token`,
>    not `Authorization`. The SSE feed and the public read endpoints take no key.
> 6. Orders: `side` lowercase `"buy"`/`"sell"`; every order is a limit order — there is no `type` field.
> 7. Health should be **heartbeat-based** (emit every ~5s, treat >15s as dead) — not a
>    REST-ping dot.
> 8. Your **portfolio / P&L** state for the dashboard now comes through the provided
>    read-only MCP server — it reads the **exchange**, not your bot. Bot-internal
>    state (heartbeat liveness, strategy/pause) uses a direct bot→dashboard channel,
>    not MCP.
>
> These are technical facts, not opinions. Please reproduce them accurately.

---

## Interview note — Engineering Lead, Marcus Lim
*~30 min. Notes by BA.*

Marcus is focused on how teams build, not what. "I'd rather see four features done
cleanly — with tests, version control, and a CI check — than ten half-built ones." Wants
meaningful git history, **unit tests on the core logic** (strategy signals, P&L maths,
the announcement parsing), and a **lint+test CI** on every push. Wants a
**reproducible setup** — pinned dependencies, one command to run — and clean
**environment separation**: "treat the sandbox as your dev environment and the live
session as prod. Don't test against prod."

On AI tooling: "You all have Copilot. Use it spec-first and **review what it gives you**
— I want to see that a human owned the decisions. Document where you leaned on it."

He also pushed for things that aren't realistic today — GIC's internal
infrastructure-as-code framework, full end-to-end test coverage, and a specific CI
vendor we use internally — and conceded those are aspirational for a one-day build.

---

## Interview note — Investment Analyst, Sophia Tan
*~40 min. Notes by BA.*

Sophia wants the analytics to be genuinely predictive, not just descriptive: a
**forward-looking view** — a short-horizon move probability per instrument, or a
parameter-tuning suggestion — surfaced **with a confidence band**, "clearly labelled as
a model estimate, never as fact."

On the market events: "When an announcement fires, I don't want a keyword match on the
named ticker. I want the system to **reason across the supply chain** — sanctions on
freight hit AXON directly but ripple to NOVA and CRVX for different reasons. That's an
LLM job." She's keen on **LLM-assisted research** for the signal.

Strong on **responsible AI**: "If the assistant gives me a number, I need to know where
it came from and how confident it is. Citations and confidence, always."

Her ambition ran ahead of the timeline at points — she described wanting a full
multi-factor forecasting model with a months-long backtest and walk-forward validation
before she'd "trust" it.

---

## Interview note — Trade Operations, Linda Hartono
*Operations floor, 60 min, formal. Notes by BA.*

Linda wants **trade lifecycle visibility** — every order's journey, not just its
outcome: submitted, acknowledged, filled, partially filled, cancelled, rejected. Wants
an **immutable audit log** of human actions (timestamp, action, who).

Most emphatic point: **all timestamps in SGT**, millisecond precision. "UTC has caused
reconciliation confusion twice. If a CSV comes to me in UTC I send it back." Wants a
**trade log + CSV export** to a fixed schema, plus an end-of-session summary (total P&L,
total trades, best/worst instrument, alerts triggered).

She also confirmed the architecture question she'd raised for months: bot and dashboard
must be separate processes — if the dashboard dies, the bot keeps running.

---

## Email submission — Trade Operations, Gerald Sim
*Gerald declined a live interview and sent this.*

> Treat these as technical specs, not suggestions.
>
> **CSV column order (fixed):**
> `TradeID | Timestamp_SGT | Instrument | Direction | Quantity | LimitPrice | FillPrice | FillQuantity | RemainingQty | Status | PnLImpact | CumulativePnL`
>
> **Status codes (use exactly):** `PENDING | ACKNOWLEDGED | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED`
>
> **Timestamp format:** `YYYY-MM-DD HH:MM:SS.mmm SGT` — millisecond precision, timezone explicit.
>
> **Running totals** (always visible on the dashboard, not just in the export): buy
> orders, sell orders, partially filled, rejected — this session.
>
> All numeric fields use a period `.` as the decimal separator. I've been burned by
> comma decimals before.
>
> — Gerald

---

## Interview note — Portfolio Manager, Farrukh Tashkentov
*~25 min, recently rotated from the PM team. Notes by BA.*

Farrukh's first question was "where's the rebalancing to target weights?" — he was
thinking of a systematic portfolio that always holds some of each name and rebalances.
Once it was clear this is an **active intraday trader** (it may hold zero of an
instrument for long stretches), he adjusted usefully.

He'd want, per instrument: total P&L, number of trades, win rate, average profit on
winners, average loss on losers, profit factor — "a trading performance breakdown." And
a **correlation view** — "a simple matrix, colour-coded; NOVA and AXON moving together
is risk-relevant even if this isn't a portfolio in the classic sense." Also the bot's
net directional exposure: "if everything's falling and we're long across the board,
that's a problem I want to see."

---

## Meeting note — Deputy COO, Winston Goh
*Boardroom, ~25 min. Notes by Committee Lead.*

Winston's framing: "Three things. The system works. It doesn't crash. Traders can see
what's happening and stop it. Do those three well and you've added more value than the
last eighteen months." Relieved to hear students, not contractors, were building it.

On instruments: prioritise NOVA and ELXR (already running) for stability; AXON and CRVX
are enhancements; DRFT is at the team's discretion. "Don't spend two days adding DRFT and
breaking NOVA."

One personal ask: "A summary view. After the session I want one screen — did the bot make
money, how much, which instruments, was it stable. If I can't get that in 30 seconds, the
dashboard failed."

---

# PART 2 — CROSS-FUNCTIONAL ALIGNMENT

## Zoom transcript — pre-hackathon alignment
*~55 min. Present: Derek (Snr Trader, strategy), Adeline (Snr Trader, execution),
Clarissa (Snr Trader, risk), Aaron (Head of Tech), Yuhui (Architect), Marcus (Eng Lead),
Sophia (Investment Analyst), Linda (Trade Ops), Winston (Deputy COO). Lightly edited.*

**[00:02] Winston:** One sentence each — what does success look like?
**Derek:** Bot trades the names with the right strategies, doesn't crash, makes money.
**Adeline:** I can see VWAP, fill rates, and candlestick charts per instrument, live, on one screen.
**Clarissa:** The kill switch works. One button, immediate. Everything else is secondary.
**Linda:** Every trade logged in SGT and exportable cleanly.
**Aaron:** Bot and dashboard separate, live feed not polling, real error handling.
**Yuhui:** Dynamic instruments, correct formats, heartbeat. Technical hygiene.
**Marcus:** Clean code, tests, CI, reproducible. Four things done well beats ten done badly.
**Sophia:** A predictive view with confidence, and the announcement signal reasoned, not keyword-matched.
**Winston:** One screen tells me if the system is healthy. That's all I personally need.

**[00:11] Derek:** Can we settle DRFT? I'm hearing different things.
**Clarissa:** It's genuinely dangerous — the book's so thin the bot trades against itself. I want it hard-blocked.
**Derek:** That's why I said leave it alone. Not that it's forbidden.
**Aaron:** Document it clearly: "if you trade DRFT, your order size moves the price — plan accordingly."
**Winston:** Can we agree on that? Tradeable, with informed risk management?
**Clarissa / Derek:** *(together)* Fine.

**[00:24] Adeline:** The ELXR lookback bug — I've raised it three times. Who tells the teams?
**Aaron:** It's in the tracker.
**Adeline:** The tracker has twenty items. Are we expecting them to triage all of it?
**Winston:** Which are must-fix?
**Aaron:** Seven. The thirty-second polling, the reconnect, the shutdown, the stale-data-on-reconnect, the lookback, the missing-instrument crash, and the UTC timestamps. The rest are nice-to-have.

**[00:41] Sophia:** On the AI side — I don't want a black box. If the assistant tells someone a number, it has to cite where it got it and show confidence.
**Marcus:** Same principle in the code — I want to see they reviewed what the AI wrote, not just merged it.

**[00:50] Derek:** Last thing — James's notes. Page 3 is the regime formula. Don't annotate it. If they can't extract the idea, they can't implement it.
**Winston:** Agreed. Notes go in as-is.

---

# PART 3 — INFORMAL DISCUSSION

## Chat thread — junior team
*Reconstructed from a group chat after the formal sessions. Participants: Ethan and
Brandon (Junior Traders), Farrukh (PM).*

> **Ethan:** anyone else feel like we got 50 conflicting requirements lol
> **Brandon:** yeah Derek said the dashboard doesn't matter and Clarissa said the kill switch is the only thing that matters
> **Ethan:** also make it not look like Windows XP. dark mode. real-time — the 30 second refresh is maddening. and it should work on mobile, I check stuff at lunch
> **Brandon:** honestly market orders are simpler, can't we just use those
> **Ethan:** I think there's something in DRFT tho. it spikes on volume then drops. if we sized it tiny, like 5%?
> **Brandon:** or train an ML model on the tick archive and let it predict DRFT. that's what the big funds do right
> **Farrukh:** that feels like exactly the kind of call we'd have to justify in the presentation. if we can explain why, probably fine
> **Brandon:** also if it crashes during trading can't we just restart the script
> **Ethan:** my friend's crypto bot dashboard has a "bot mood" thing, tells you what mood the bot's in. kinda fun. could we add that
> **Farrukh:** let's just be able to justify everything. that's the move
> **Ethan:** justify everything. agreed

---

*Stakeholder Records | Student-facing | Reconcile these yourselves — and defend your reading.*
