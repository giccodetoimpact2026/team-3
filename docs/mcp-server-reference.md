# GIC Hackathon — MCP Read Server Guide
### Student-facing | Hackathon platform

> You are given a **working read-only MCP server** (the `baseline-mcp-server` repo)
> that exposes your team's portfolio and P&L to a conversational panel (requirement
> **M4**). You **run it yourself** and consume it — the protocol plumbing is already
> written, so you don't build it, but it's your service to host (default `:8090`,
> alongside your bot and dashboard). It reports state; it never places orders. If
> anything here disagrees with old code comments, **this document wins.**

---

## Two ways to call it (dual surface)

The server runs on **one port (default `8090`)** and offers two surfaces:

| Surface | Use it from | Endpoint |
|---------|-------------|----------|
| **REST facade** | the **browser dashboard** (browsers can't speak raw MCP) | `POST /tools/{name}` |
| **MCP (streamable HTTP)** | an **MCP-native client** (e.g. Copilot) | `/mcp` |

Most teams use the **REST facade** from the panel — that's what the provided
`mcp-adapter.example.js` calls. The `/mcp` endpoint is there if your client speaks
MCP directly.

## Auth

The server is configured with **your team's `api_key`** (in its `config.yaml`) and
calls the exchange on your behalf — so you **do not** send an `X-API-Key` when you
call `/tools/{name}`. Keep your `config.yaml` private.

> **Running it in Docker?** Every `config.yaml` value is overridable by an
> environment variable, which wins over the file — so you can containerise the
> server without committing your key, and point it at the exchange by whatever
> name it answers to on your network:
>
> | Variable | Overrides |
> |---|---|
> | `MCP_PORT` | server port (default `8090`) |
> | `MCP_BASE_URL` | what the dashboard adapter targets |
> | `MCP_EXCHANGE_BASE_URL` | the exchange URL — **the shipped default is a placeholder host that will not resolve on your machine**; set this to `https://api.codetoimpact.sg` (or the exchange's service name inside Docker) |
> | `MCP_EXCHANGE_API_KEY` | your team's `api_key` |
>
> If tool calls come back `401`, the key is wrong or unset. If they hang or fail
> to connect, it's the exchange URL.

---

## Read tools

Each tool returns a small JSON object plus a `source` (the backing exchange
endpoint) and an `as_of` **SGT** timestamp — so your panel can **cite** where an
answer came from (an M4 responsible-AI requirement). Money is in **dollars**.

| Tool | Returns |
|------|---------|
| `get_portfolio` | cash, total value, realised / unrealised / session P&L |
| `get_positions` | per-symbol quantity, average entry price, unrealised P&L |
| `get_pnl` | aggregate + per-instrument P&L |
| `get_session_summary` | total P&L, trade count, best / worst instrument |

> **All instruments are always present.** A symbol you haven't traded comes back
> as a **zeroed** entry, not omitted — so your table never renders `undefined`.

### Response contract

```json
{
  "source": "GET /portfolio",
  "as_of": "2026-06-21 14:32:05.120 SGT",
  "data": {
    "aggregate": { "realized_pnl": -1560.50, "unrealized_pnl": 320.00, "total_pnl": -1240.50 },
    "by_instrument": {
      "NOVA": { "realized_pnl": 0.00,     "unrealized_pnl": 320.00, "total_pnl": 320.00 },
      "ELXR": { "realized_pnl": -1560.50, "unrealized_pnl": 0.00,   "total_pnl": -1560.50 }
    }
  }
}
```

(That's `get_pnl`'s shape — `by_instrument` values are objects, not bare numbers, and
`session_pnl` lives on `get_portfolio`, not here.)

Render the answer **with the `source` citation and a confidence indicator** — that's
what earns the M4 responsible-AI credit.

> **Tool output ≠ the raw exchange shape.** These tools return money as **dollars**
> (floats, e.g. `-1240.50`), not the exchange's integer **cents**, and rename a few
> fields for the panel — e.g. `get_positions` returns `avg_entry_price` where
> `GET /portfolio` has `avg_cost`, and omits `market_value`. If a dashboard reads both
> `GET /portfolio` (cents) and an MCP tool (dollars), keep the units and field names straight.

---

## Quickstart

### curl

```bash
curl -s -X POST http://localhost:8090/tools/get_pnl | jq
curl -s -X POST http://localhost:8090/tools/get_portfolio | jq
curl -s http://localhost:8090/health
```

### From the dashboard (the provided adapter)

```javascript
// adapter/mcp-adapter.example.js — copy callTool() for the other tools.
async function callTool(name, args = {}) {
  const res = await fetch(`${MCP_BASE_URL}/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`MCP tool ${name} failed: ${res.status}`);
  return res.json(); // -> { source, as_of, data }
}

const pnl = await callTool("get_pnl");
renderAnswer(pnl.data, { citation: pnl.source, asOf: pnl.as_of, confidence: "high" });
```

The conversational panel (M4) wraps an LLM around these calls: the model decides
*which* tool to invoke from the user's question, the adapter runs it, and the panel
renders the answer with the citation.

---

## Control tools (inert — bonus)

`pause_instrument`, `resume_instrument`, and `kill_switch` exist but return a
not-wired notice:

```json
{ "status": "not_implemented", "note": "Control channel to bot not wired. See M4 bonus / B-list." }
```

Making them act on the bot needs a channel from the server to your bot plus
bot-side handling (graceful pause/shutdown) — a **stretch goal**, not a
requirement. The M3 kill switch does **not** depend on these; it may use any
direct dashboard→bot channel.

---

## Run it (you host this)

The MCP server is **your service to run** — stand it up next to your bot and
dashboard, and point your M4 panel at it. Extending it is a Challenge-2 bonus:

```bash
cd baseline-mcp-server
pip install -r requirements.txt
# Edit config.yaml: exchange.base_url + your team api_key.
python server.py            # serves on :8090 — this is the base URL your panel calls
```

Extend it as a **bonus**: add a new read tool, wire the control tools, or stand up
a second (e.g. research/news) MCP server.
