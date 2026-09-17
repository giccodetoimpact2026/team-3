"""Baseline MCP read server (dual surface).

Ships two ways to reach the same read tools:
  1. a genuine **streamable-HTTP MCP** endpoint at `/mcp` (for MCP-native clients
     like Copilot), and
  2. a thin **REST facade** `POST /tools/{name}` -> `{source, as_of, data}` — what
     the browser dashboard's `mcp-adapter.example.js` calls, since browsers don't
     speak raw MCP.

Read tools source from the exchange REST API directly (standalone of any bot).
Control tools are inert stubs (bonus for students to wire). See HANDOVER.md.
"""
import os

import yaml
from fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import tools
from exchange_client import ExchangeClient


def load_config() -> dict:
    """config.yaml, with environment overrides.

    The file is the normal way to configure this server. Environment variables
    win over it so the same image can run in Docker (where the exchange is a
    service name, not `localhost`) without editing a tracked file — and so your
    `api_key` need not be committed. Unset variables change nothing.
    """
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)

    for env, section, key in (
        ("MCP_PORT", "server", "port"),
        ("MCP_BASE_URL", "server", "mcp_base_url"),
        ("MCP_EXCHANGE_BASE_URL", "exchange", "base_url"),
        ("MCP_EXCHANGE_API_KEY", "exchange", "api_key"),
    ):
        value = os.environ.get(env)
        if value:
            config.setdefault(section, {})[key] = value
    return config


config = load_config()
client = ExchangeClient(config["exchange"]["base_url"], config["exchange"]["api_key"])

mcp = FastMCP("baseline-mcp-server")


# ---------------------------------------------------------------------------
# Read tools (fully working) — MCP surface
# ---------------------------------------------------------------------------
@mcp.tool
def get_portfolio() -> dict:
    """Cash, total value, and realised / unrealised / session P&L for your team."""
    return tools.get_portfolio(client)


@mcp.tool
def get_positions() -> dict:
    """Per-symbol quantity, average entry price, and unrealised P&L (all instruments)."""
    return tools.get_positions(client)


@mcp.tool
def get_pnl() -> dict:
    """Aggregate and per-instrument P&L, SGT-stamped."""
    return tools.get_pnl(client)


@mcp.tool
def get_session_summary() -> dict:
    """Total P&L, trade count, and best / worst instrument."""
    return tools.get_session_summary(client)


# ---------------------------------------------------------------------------
# Control tools (inert stubs — bonus to activate)
# ---------------------------------------------------------------------------
@mcp.tool
def pause_instrument(symbol: str) -> dict:
    """BONUS: pause the bot on a symbol. Baseline returns a not-wired notice."""
    return tools.NOT_WIRED


@mcp.tool
def resume_instrument(symbol: str) -> dict:
    """BONUS: resume the bot on a symbol. Baseline returns a not-wired notice."""
    return tools.NOT_WIRED


@mcp.tool
def kill_switch() -> dict:
    """BONUS: stop the bot and cancel open orders. Baseline returns a not-wired notice."""
    return tools.NOT_WIRED


# ---------------------------------------------------------------------------
# REST facade (browser dashboard) — same tools over plain HTTP
# ---------------------------------------------------------------------------
@mcp.custom_route("/tools/{name}", methods=["POST"])
async def call_tool(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    if name in tools.READ_TOOLS:
        try:
            return JSONResponse(tools.READ_TOOLS[name](client))
        except Exception as e:  # exchange unreachable / bad response
            return JSONResponse({"error": f"tool {name} failed: {e}"}, status_code=502)
    if name in tools.CONTROL_TOOLS:
        return JSONResponse(tools.NOT_WIRED)
    return JSONResponse({"error": f"unknown tool: {name}"}, status_code=404)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# Streamable-HTTP ASGI app (MCP at /mcp) + the custom routes above.
app = mcp.http_app()
# Permissive CORS — the browser panel calls the REST facade directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(config["server"]["port"]))
