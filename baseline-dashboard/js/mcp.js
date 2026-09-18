// Read-only MCP server client (REST facade, see mcp-server-reference.md).
// Every tool answers { source, as_of, data }; money is in DOLLARS (floats),
// unlike the exchange's integer cents. No API key: the MCP server holds it.
const MCP = {
  callTool: function (name, args) {
    return $.ajax({
      url: CONFIG.MCP_BASE_URL + "/tools/" + name,
      method: "POST",
      contentType: "application/json",
      dataType: "json",
      data: JSON.stringify(args || {}),
      timeout: 5000,
    });
  },
  getPnl: function () {
    return this.callTool("get_pnl");
  },
};
