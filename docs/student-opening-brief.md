# GIC Trading Hackathon — Team Brief
### Handed to you at registration on the day, together with the baseline repos

Welcome. Over the next day you'll take a real, messy, half-finished trading system
and make it meaningfully better. You are not building from scratch — you are
inheriting a live codebase, a pile of stakeholder opinions (some right, some wrong,
some conflicting), and a set of known bugs. Your job is to make good engineering and
product decisions under realistic conditions and be able to defend them.

---

## 1. The situation

GIC's Systematic Trading desk has run an automated trading **bot** and a monitoring
**dashboard** for 18 months in a continuously simulated, competitive internal market
— real order-matching mechanics, GIC bots trading alongside external counterparties.

The team that built it has shrunk: the lead bot developer left abruptly, the frontend
developer is on sabbatical with features half-finished, and the quant left three
months ago leaving partial, handwritten strategy notes. The desk has been asking for
improvements for months; nobody has had the bandwidth. That's where you come in.

You inherit:
- a **Python trading bot** (trades two of five instruments; has known bugs),
- a **JavaScript dashboard** (minimal; has known bugs),
- a **read-only MCP server** with a working client adapter (use it — see §6),
- the quant's **strategy notes** (partial scan; page 3 matters),
- a **bug tracker** (20 items — 18 open + 2 guardrails; 7 of them must-fix — the tracker lists the
  symptoms, not their severities or how to fix them),
- **platform API documentation**.

---

## 2. What you're being asked to do

**Two challenges.**

**Challenge 1 — Alpha generation.** Make the bot trade well on the simulated
exchange. This is measured live across **three 30-minute trading windows** — each
starts from fresh capital, and the final leaderboard is the **average of your
placing** in the three windows (1st = 10 points … 10th = 1, 11th and below = 0; ties by total P&L)
— and in your presentation.

Four things we're looking for, in order:
1. **Get in the door** — your bot participates in the **long-only, limit-order-only**
   exchange.
2. **Stay in the room** — your bot navigates the volatility of the market.
3. **Win, sustainably** — demonstrate a sound strategy through practical insights:
   price trends, fundamental and sector analysis, statistical analysis, and
   AI-assisted strategy generation.
4. **Compete** — three 30-minute trading windows.

**Challenge 2 — Analytics: from insights to interface.** Build a commercial-grade
analytics and control experience. We're looking for three baseline capabilities:
1. an intuitive, commercial-grade analytics dashboard;
2. an AI conversational control panel;
3. advanced tool integration via MCP.

Cutting across both: **how you build matters as much as what you build** — clean,
modular, version-controlled code; sensible tests; reproducible setup; CI; and
thoughtful, reviewed use of AI in your development workflow. Be ready to explain
which requirements you judged genuine, what you prioritised, and what you descoped
and why.

> You have GitHub Copilot. Use it well — spec-driven, with your own review of what it
> produces. We care about judgement and oversight, not raw output volume.

---

## 3. The market

Five instruments. **Discover them at runtime via `GET /instruments` — do not
hardcode names.**

| Ticker | Company | Sector | Personality |
|--------|---------|--------|-------------|
| NOVA | Nova Energy | Oil & Gas | Trending, event-sensitive (the bot trades this today, on momentum) |
| AXON | Axon Freight | Logistics & Shipping | Correlated with NOVA — freight follows energy costs |
| CRVX | Curvex Semi | Semiconductors | Growth-like momentum |
| DRFT | Driftware Defense | Defense Systems | Event-driven, **thin liquidity** — your own orders can move the price |
| ELXR | Elixar Labs | Pharma | Defensive, range-bound (the bot trades this today, on mean-reversion) |

These instruments form a **supply-chain web**: a single market announcement can ripple
across several of them, some up, some down. Recognising those relationships is part of
the challenge.

The exchange also runs **15 resident bots** (market makers, trend followers, random
traders) on the same API. Their orders look just like yours in the data feed.

---

## 4. The day

**It all happens in one day.** You receive this package and the three baseline repos
at registration. The **exchange** read-only endpoints (`/instruments`, `/portfolio`,
`/orderbook`, `/health`) and the **LLM service** are available from then, so you can
read the market and try the AI service while you plan. **Order submission** opens when
the exchange goes into sandbox mode as coding starts. Your **MCP read server**
is in your baseline — you run it yourself. Use the time before coding starts to read
the stakeholder records and triage the bug tracker; nothing before the first window
trades or is scored.

**The day:**
Refer to the Web UI for the latest program flow

Each window is scored **mark-to-market at its closing price** — you don't have to
unwind to be counted — and every window is its own world: cash, positions, orders,
fills and prices all belong to the window they happened in, so the next one opens
at the starting balance, with the window's starting positions (identical for every
team — possibly none), nothing resting, and prices back at their
starting values (see the API reference, "Trading windows"). Outside the live
windows the exchange runs in sandbox mode, so you can always test.

During live windows, the exchange broadcasts **market event announcements** over the
SSE feed. Your bot will receive them if you've wired up the right handler. What you
do with them is up to you.

You will also receive **messages from GIC stakeholders** through the day — by chat and
email. Treat them as real business communications. Some will be urgent, some will
conflict with each other, and some will ask for more than is sensible in your
timeline. Deciding what to act on is part of the exercise.

---

## 5. What exists, what doesn't

**Exists:** the bot, the dashboard, the MCP read server + adapter, the quant's notes,
the bug tracker, the API docs.

**Does not exist:** a complete spec (you derive it from the stakeholder records — see
§7), a **comprehensive** test suite (starter tests ship with the baselines — extend
them), a candles/OHLCV endpoint (you accumulate price history from the live
feed yourself), and resolved design decisions (several conflicts are left for you).

---

## 6. The MCP server (read-only) — use it

You're given a working **read-only MCP server** (in your baseline) to **run
yourself**, exposing tools that report the system's state (portfolio, positions, P&L,
session summary), each returning the source endpoint and an SGT timestamp. A small
**client adapter** shows one tool call end-to-end. Your conversational control panel
should consume these. Wiring up
*control* tools (pause/kill via MCP) so the bot acts on them is a stretch, not a
requirement.

---

## 7. Where the requirements come from

There is **no clean specification.** You derive it the way you would on a real desk —
from the people who use the system. We've collected the requirement-gathering material
for you in **`stakeholder-interview-records.md`**: interview notes, a cross-functional
meeting transcript, a post-meeting note, an email submission, and an informal chat
thread.

Read it critically. **The stakeholders do not agree, and not everything they say is
right.** Some asks are genuine and load-bearing; some are personal preference, scope
inflation, or a misunderstanding of what the system even is. One of the notes was
written quickly and doesn't perfectly reflect what was said —
cross-reference before you trust a summary. Separating the signal from the noise, and
justifying your calls, is a graded part of the exercise.

Who you heard from:

| Role | Format | Their focus |
|------|--------|-------------|
| CIO | Interview note | Observability, charts, explainability |
| Deputy CIO | Interview (near-verbatim) | Risk controls, audit, alerts |
| Senior Trader (strategy) | Standing-meeting note + a post-meeting note | Strategy across instruments, reliability |
| Senior Trader (execution) | Interview note | Charts, VWAP, fill rates, a strategy bug, regime detection |
| Senior Trader (risk) | Interview note | Kill switch, anomaly detection, DRFT |
| Head of Tech | Whiteboard-session note | Architecture, live feed vs polling, process isolation |
| Enterprise Solution Architect | Written submission | Precise API / SSE / MCP / format facts |
| Engineering Lead | Interview note | Tests, CI, reproducibility, AI-assisted dev discipline |
| Investment Analyst | Interview note | Predictive model, LLM-assisted signal, responsible AI |
| Trade Operations | Interview note + email | SGT timestamps, export schema, audit |
| Portfolio Manager | Interview note | Performance breakdown, correlation |
| Junior Traders | Chat thread | UX wishes and a lot of half-formed ideas |
| Deputy COO | Meeting note | "Works, doesn't crash, can be stopped"; an exec summary |

---

## 8. Decisions you'll have to defend

No one resolved these. Make a call and be ready to justify it:
1. Kill-switch UX — the risk desk is explicit it must be **immediate, no confirmation dialog**; your call is how to place and guard that one button without slowing it down.
2. DRFT — trade it or not? If yes, at what size and with what safeguards?
3. Strategy lookback — last N prices, or last N seconds? (Feed timing is irregular.)
4. Conversational panel — how deep is genuinely useful vs. narrating everything?
5. Executive vs. trader view — which loads by default, and where's the line?
6. Mobile — worth your time today?

---

## 9. How you'll be judged

You'll present to GIC traders, engineers and leadership. Judges are assessing, across
both challenges:

**How we'll assess**

1. **Impact:** find the requirement based on interviews, resolve conflicting
   requirements, fix bugs and prioritize features to be implemented.
2. **Your AI:** the predictive model, the conversational panel, MCP integration and
   responsible AI practice.
3. **Thoughtful engineering:** clean code, version control, tests, CI, reproducible
   setup, thoughtful deployment and use of technologies, AI-assisted development.
4. **Creativity:** once baselines are covered, sky is the limit!

**Scoring materials**

1. **Demo and presentation:** at most 10 mins presentation followed by 5 mins Q&A by
   judges, include the following:
   a. **Delivery:** How feature is derived, refined, tracked, prioritized, and implemented?
   b. **Trading:** What is your strategy? How is your strategy special? What's the strength and weakness of your strategy? Which requirement was prioritized and why?
   c. **Solution architecture:** Context, component, deployment diagrams, architecture choices?
   d. **AI:** How did you build with AI? How did you build AI into your solution? Which model is used for what use-cases, and why?
   e. **SDLC practice:** Team process and CI/CD, AI-assisted development, Deployment
   f. **Reflection:** What did you do well? what could have been done better? what did you enjoy from this event?
2. **Trading P&L**
3. **Implementation scan by Agent:** Agent-based assessment of your implementation.
   Generate a set of judging documents and submit them as a **single zip named
   `<YourTeamName>.zip`** (your team name exactly as registered), containing **exactly
   these four files at the root — no source code, nothing else**:
   a. `README.md` — team name, members, what works vs what's aspirational, and your prioritisation
   b. `SPEC.md` — requirement interpretation, target user, and descoping decisions
   c. `CLAUDE.md` — your Claude / AI configuration
   d. `presentation.html` — a self-contained demo presentation (no external assets)
   Keep the filenames exact (case-sensitive)
4. **Mentor-based assessment** of your implementation and team process
5. **AI usage:** Usage pattern of LLM service used for feature implementation. Only AI
   features utilizing the provided LLM service (`https://llm.codetoimpact.sg`) are
   considered for scoring

The balance between the trading and the analytics work is not something to optimise by
guessing — build both well.

---

## 10. Reference materials (shared drive)

- **`stakeholder-interview-records.md`** — the interviews, transcript, notes, email, and chat thread (§7)
- Exchange / Platform **API Reference**
- **Bug tracker** export (20 items — 18 open + 2 guardrails; 7 must-fix)
- Quant's **strategy notes** scan (page 3 has the regime-detection formula)
- Repo **handover notes** (bot, dashboard, MCP server)
- The baseline repos and the MCP client adapter

Good luck. Build something you'd be proud to put in front of a trading desk.
