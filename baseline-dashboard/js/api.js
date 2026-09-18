// Thin exchange REST wrapper (jQuery). Auth is X-API-Key on every call.
// Read-only: the dashboard displays state, it never places orders.
const ExchangeAPI = {
  // GET /portfolio -> cash, positions, P&L (integer cents). Unwraps the
  // {status, data} envelope.
  getPortfolio: function () {
    return $.ajax({
      url: CONFIG.API_BASE_URL + "/portfolio",
      method: "GET",
      dataType: "json",
      headers: { "X-API-Key": CONFIG.API_KEY },
    }).then(function (resp) {
      return resp && resp.data ? resp.data : resp;
    });
  },

  // GET /instruments -> [{symbol, name, sector, ...}]. Call once at startup
  // (and on a session change); names can change between days.
  getInstruments: function () {
    return $.ajax({
      url: CONFIG.API_BASE_URL + "/instruments",
      method: "GET",
      dataType: "json",
      headers: { "X-API-Key": CONFIG.API_KEY },
    }).then(function (resp) {
      return resp && resp.data ? resp.data : resp;
    });
  },

  // GET /orders -> every order of ours in the current window (list of order
  // objects: id, symbol, side, price, quantity, remaining_qty, status,
  // created_at, updated_at, ...).
  getOrders: function () {
    return $.ajax({
      url: CONFIG.API_BASE_URL + "/orders",
      method: "GET",
      dataType: "json",
      headers: { "X-API-Key": CONFIG.API_KEY },
    }).then(function (resp) {
      return resp && resp.data ? resp.data : resp;
    });
  },
};

// The bot's local control endpoint, reached through the /bot proxy in
// server.js. This is the one place the dashboard sends a command to the bot,
// and the direct bot -> dashboard channel for bot-internal state (heartbeat,
// feed health, order events), which the MCP server does not carry.
const BotAPI = {
  // GET /status -> {state, session_id, session_open, unresolved, heartbeat_*,
  // feed_*, orders: {...}}. Fails when the bot process is not up.
  getStatus: function () {
    return $.ajax({
      url: CONFIG.BOT_BASE_URL + "/status",
      method: "GET",
      dataType: "json",
      timeout: 4000,
    });
  },

  // GET /orders?since=N -> {seq, events: [...]}: every submit, fill, cancel
  // and reconciliation the bot performed after journal entry N. This is how
  // the lifecycle table learns about REJECTED submits and UNKNOWN orders,
  // which never appear in the exchange's GET /orders, and about our own fills
  // (the public SSE trade event carries no order id).
  getOrderEvents: function (since) {
    return $.ajax({
      url: CONFIG.BOT_BASE_URL + "/orders?since=" + encodeURIComponent(since || 0),
      method: "GET",
      dataType: "json",
      timeout: 4000,
    });
  },

  // POST /halt -> emergency stop: the bot blocks new intents, cancels every
  // open order and stops. Same path as Ctrl-C on the bot. No confirmation
  // step by design (backlog: "one click").
  halt: function () {
    return $.ajax({
      url: CONFIG.BOT_BASE_URL + "/halt",
      method: "POST",
      dataType: "json",
      timeout: 15000, // cancelling many orders can take a few seconds
    });
  },
};

// The parameter-sweep backtest is a batch artifact, not live exchange data: the
// bot's run_backtest.py writes results.json, a copy of which is committed at
// data/backtest.json and served as a same-origin static file (no key, no proxy).
const BacktestAPI = {
  fetch: function () {
    return $.getJSON("data/backtest.json");
  },
};
