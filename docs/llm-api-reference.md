# GIC Hackathon — LLM Service API Reference
### Student-facing | Hackathon platform

> The organizer-run **LLM service** gives your bot and dashboard access to
> Anthropic models without you holding a raw provider key. It speaks the
> **Anthropic Messages API** shape, so most Anthropic client libraries work by
> changing only the base URL and key. If anything here disagrees with old code
> comments in the baseline, **this document wins.** The live base URL is
> `https://llm.codetoimpact.sg`; the service is **available from registration**, so you
> can try it as soon as you have your team key.

---

## Authentication

Use the **same secret `api_key` you use for the exchange** (the `X-API-Key` from
your team's provisioning bundle). There is no separate LLM key.

```
X-API-Key: <your exchange api_key>
```

- Not `Authorization`, not your `team_id` — only the secret `api_key`
  authenticates.
- A missing or unknown key → `401 UNAUTHORIZED`.

Your calls are metered against **your team's credit budget** (see
[Credits](#your-credit-budget)).

---

## Base URL

| Channel | URL |
|---------|-----|
| LLM service (REST) | `https://llm.codetoimpact.sg` |

The organizers run this service — you do not host it. The exchange
(`https://api.codetoimpact.sg`) and the MCP read server (`:8090`, which you do run)
are separate services; this is a third one on its own host.

---

## Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/v1/messages` | Send a prompt; get a completion (Anthropic Messages shape) |
| `GET`  | `/health` | Liveness check (public): `{ "status": "ok", "mock_mode": <bool> }` |

`POST /v1/messages` takes and returns the standard Anthropic Messages body
(`model`, `max_tokens`, `system`, `messages`, `tools`, … in; `content` blocks,
`stop_reason`, `usage` out). Tool/function-calling is passed through unchanged.

---

## Choosing a model

Name a model by its short **alias** (or its full ID). Anything else → `400
UNKNOWN_MODEL`.

| Alias | Full ID | Good for |
|-------|---------|----------|
| `fable`  | `claude-fable-5`            | Cheapest, fastest — quick experiments |
| `haiku`  | `claude-haiku-4-5-20251001` | Low-latency, high-volume bot loops |
| `sonnet` | `claude-sonnet-5`           | **Default** — balanced reasoning |
| `opus`   | `claude-opus-5`             | Hardest reasoning (uses the most credit) |

Omit `model` and you get `sonnet`. **Larger models cost more credit per call** —
pick the smallest one that does the job, especially inside a bot loop.

---

## Quickstart

### curl

```bash
curl -s https://llm.codetoimpact.sg/v1/messages \
  -H "X-API-Key: $API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "model": "sonnet",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "In one sentence, what is a limit order?"}]
  }'
```

### Anthropic SDK (recommended — it already sends `x-api-key`)

Point the official SDK at the service; your exchange key is the API key. **Use
non-streaming calls** — `stream: true` (and the SDK's `.stream()` / streaming
helpers) is rejected with `400 STREAMING_UNSUPPORTED`; call `messages.create(...)`
and read the full response.

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="<your exchange api_key>",
    base_url="https://llm.codetoimpact.sg",   # the LLM service, not api.anthropic.com
)

msg = client.messages.create(
    model="sonnet",                     # or "claude-sonnet-5"
    max_tokens=256,
    messages=[{"role": "user", "content": "Summarise NOVA's outlook in one line."}],
)
print(msg.content[0].text)
```

```javascript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  apiKey: process.env.API_KEY,          // your exchange api_key
  baseURL: "https://llm.codetoimpact.sg",
});

const msg = await client.messages.create({
  model: "sonnet",
  max_tokens: 256,
  messages: [{ role: "user", content: "Say hi." }],
});
console.log(msg.content[0].text);
```

---

## Tool calling (for the conversational panel)

Pass a `tools` array; the reply may be a `tool_use` block asking you to run a
tool. Run it, then send the result back as a `tool_result` in the next user turn,
and you get the final text answer.

```jsonc
// 1) You send: prompt + tools
{ "model": "sonnet", "max_tokens": 512,
  "messages": [{ "role": "user", "content": "How's my portfolio?" }],
  "tools": [{ "name": "get_portfolio", "description": "read the team portfolio",
              "input_schema": { "type": "object" } }] }

// 2) Reply — stop_reason "tool_use":
{ "stop_reason": "tool_use",
  "content": [{ "type": "tool_use", "id": "toolu_…", "name": "get_portfolio", "input": {} }] }

// 3) You run the tool, then send its output back:
{ "model": "sonnet", "max_tokens": 512,
  "messages": [
    { "role": "user", "content": "How's my portfolio?" },
    { "role": "assistant", "content": [{ "type": "tool_use", "id": "toolu_…", "name": "get_portfolio", "input": {} }] },
    { "role": "user", "content": [{ "type": "tool_result", "tool_use_id": "toolu_…", "content": "{\"source\":\"GET /portfolio\", \"as_of\":\"… SGT\", \"data\":{\"cash\":10000000.00, \"total_value\":10000000.00}}" }] }
  ],
  "tools": [ /* same tools */ ] }

// 4) Final reply — stop_reason "end_turn", plain text.
```

The MCP read server (`:8090`) is a convenient source of tools to wire here — see
the exchange API reference.

---

## Single-shot trading signal (for your bot)

For an `announcement`-driven signal, send a one-shot prompt (no tools) and ask
for a JSON verdict. Parse the returned text as JSON.

```bash
curl -s https://llm.codetoimpact.sg/v1/messages \
  -H "X-API-Key: $API_KEY" -H "content-type: application/json" \
  -d '{
    "model": "haiku",
    "max_tokens": 200,
    "system": "You are a trading signal. Given an ANNOUNCEMENT, reply with ONLY a JSON verdict: {action, symbol, confidence, rationale}.",
    "messages": [{"role": "user", "content": "ANNOUNCEMENT: Major oil discovery near Nova Energy fields."}]
  }'
# → content[0].text is a JSON string like:
#   {"action":"BUY","symbol":"NOVA","confidence":0.62,"rationale":"…"}
```

---

## Your credit budget

Every team starts with a **fixed credit grant in USD — about `$25`** (the exact
figure is confirmed in your team bundle). Each call deducts what it costs
(bigger models and longer outputs cost more), and the response's `usage` block
tells you the tokens you spent:

```json
"usage": { "input_tokens": 42, "output_tokens": 128 }
```

> **Your AI usage is assessed — and only through this service.** Only AI features
> that call the provided LLM service (`https://llm.codetoimpact.sg`) are considered
> for scoring. Work routed through your own provider key is invisible to us and
> won't count, so build your AI features against this service.

- When your balance runs out, calls return **`402 INSUFFICIENT_CREDIT`.**
- You can **ask an organizer to top up** — every top-up is **recorded**, and it
  may carry a **scoring penalty**, so budget deliberately. Prefer smaller models
  in loops, cap `max_tokens`, and don't call the LLM on every tick if a cheaper
  heuristic will do.

---

## Rate limits

There's a **per-team rate limit.** Over it, calls return **`429 RATE_LIMITED`**
with a `Retry-After` hint. Every team's bot tends to fire at the **same moments**
(right after each market announcement), so add a little random jitter and
exponential backoff rather than hammering on `429`.

---

## Errors

Standard HTTP status codes. **Service-generated** errors carry a machine-readable
`code` in `error.code`; **provider passthrough** errors (the `4xx`/`5xx` row) are
forwarded verbatim from the model provider, so they carry the provider's native
`error.type` instead of `error.code` — handle both shapes.

| Status | `code` | Meaning |
|--------|--------|---------|
| `400` | `UNKNOWN_MODEL` | model alias/ID isn't one of the four above |
| `400` | `STREAMING_UNSUPPORTED` | `stream: true` isn't supported — request a non-streaming completion |
| `401` | `UNAUTHORIZED` | missing / wrong `X-API-Key` |
| `402` | `INSUFFICIENT_CREDIT` | your credit is exhausted — ask an organizer |
| `429` | `RATE_LIMITED` | slow down; honour `Retry-After` |
| `4xx` / `5xx` | — | passed through from the model provider (native `error.type`) |

