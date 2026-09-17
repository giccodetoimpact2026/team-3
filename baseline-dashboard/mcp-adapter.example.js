// mcp-adapter.example.js — the M4 starting point (provided with the MCP server).
//
// The conversational panel calls the read-only MCP server's REST facade
// (browsers can't speak raw MCP). Every tool returns { source, as_of, data } —
// render the answer WITH the `source` citation and a confidence indicator.
//
// Copy callTool() for the other tools: get_portfolio, get_positions, get_pnl,
// get_session_summary.

async function callTool(name, args = {}) {
  const res = await fetch(`${CONFIG.MCP_BASE_URL}/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`MCP tool ${name} failed: ${res.status}`);
  return res.json(); // -> { source, as_of, data }
}

// Example:
//   const pnl = await callTool("get_pnl");
//   renderAnswer(pnl.data, { citation: pnl.source, asOf: pnl.as_of, confidence: "high" });
