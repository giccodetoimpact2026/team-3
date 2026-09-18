// mcp-adapter.example.js — one read-tool call end-to-end for the dashboard.
//
// Browsers can't speak raw MCP, so the panel calls the server's REST facade
// (POST /tools/{name}). Every tool returns { source, as_of, data } — render the
// answer WITH the `source` citation and a confidence indicator (M4 responsible-AI).
//
// Copy callTool() for the other tools: get_portfolio, get_positions, get_pnl,
// get_session_summary.

const MCP_BASE_URL = "http://localhost:8090"; // matches config.yaml

async function callTool(name, args = {}) {
  const res = await fetch(`${MCP_BASE_URL}/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`MCP tool ${name} failed: ${res.status}`);
  return res.json(); // -> { source, as_of, data }
}

// Example usage:
//   const pnl = await callTool("get_pnl");
//   renderAnswer(pnl.data, { citation: pnl.source, asOf: pnl.as_of, confidence: "high" });

export { callTool, MCP_BASE_URL };
