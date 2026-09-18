// Pure view-model helpers, DOM-free so they can be unit-tested in Node (see
// tests/view.test.js) and loaded as browser globals. Depends on format.js.
(function (root, factory) {
  const api = factory(
    typeof module !== "undefined" && module.exports ? require("./format.js") : root
  );
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.positionRows = api.positionRows; // browser globals
    root.FeedCursor = api.FeedCursor;
    root.escapeHtml = api.escapeHtml;
    root.botStatusView = api.botStatusView;
    root.haltResultText = api.haltResultText;
  }
})(typeof self !== "undefined" ? self : this, function (fmt) {
  // One row per instrument in the given order. An instrument absent from the
  // portfolio (not traded yet) renders as quantity 0 and dashes for prices,
  // never "undefined" (BUG-019, must-fix #6).
  function positionRows(symbols, portfolio) {
    var bySymbol = {};
    ((portfolio && portfolio.positions) || []).forEach(function (p) {
      bySymbol[p.symbol] = p;
    });
    return symbols.map(function (symbol) {
      var p = bySymbol[symbol] || {};
      return {
        symbol: symbol,
        quantity: String(p.quantity === undefined || p.quantity === null ? 0 : p.quantity),
        avg_cost: fmt.formatCurrencyOrDash(p.avg_cost),
        market_value: fmt.formatCurrencyOrDash(p.market_value),
        unrealized_pnl: fmt.formatCurrencyOrDash(p.unrealized_pnl),
      };
    });
  }

  // Tracks the SSE cursor and the trading-window id.
  //  - Snapshot frames carry no id and must not move the cursor.
  //  - On reconnect the exchange replays from Last-Event-ID; already-seen ids
  //    are rejected so a replay has no duplicate effects (must-fix #2, F4).
  //  - Ids restart at 1 after an exchange restart, so "seen" is a bounded set,
  //    not a monotonic high-water mark.
  function FeedCursor(capacity) {
    this.capacity = capacity || 2000;
    this.lastId = null;
    this.sessionId = null;
    this._seen = [];
    this._seenSet = {};
  }
  FeedCursor.prototype.accept = function (id) {
    if (id === undefined || id === null || id === "") return true; // snapshot
    id = String(id);
    if (this._seenSet[id]) return false;
    this._seenSet[id] = true;
    this._seen.push(id);
    if (this._seen.length > this.capacity) delete this._seenSet[this._seen.shift()];
    this.lastId = id;
    return true;
  };
  FeedCursor.prototype.sessionChanged = function (id) {
    if (!id) return false;
    if (this.sessionId === null) {
      this.sessionId = id;
      return false;
    }
    if (id === this.sessionId) return false;
    this.sessionId = id;
    return true;
  };

  // Every string interpolated into innerHTML goes through this. Symbols, order
  // ids and messages come from the exchange and must not be trusted as markup.
  function escapeHtml(v) {
    if (v === undefined || v === null) return "";
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Bot state pill and kill-switch button, from GET /bot/status (or null when
  // the bot did not answer). Health is heartbeat-based, not a REST-ping dot
  // (stakeholder note 7): the bot beats every ~5 s and reports the age of its
  // last beat on its own clock, so a hung trading process still answers HTTP
  // but its heartbeat age climbs. `canHalt` is false once the bot is already
  // stopping or gone, so the button cannot fire at nothing.
  //   RUNNING, heartbeat fresh   -> green, button enabled
  //   RUNNING, heartbeat > 15 s  -> red DEAD, button still enabled (cancel what you can)
  //   QUIESCING                  -> amber, cancelling in progress
  //   STOPPED                    -> red (halted)
  //   null / no answer           -> grey OFFLINE (process not running, or proxy down)
  // `detail` is the feed line shown as the pill's tooltip: feed state, age, pauses.
  var HEARTBEAT_DEAD_AFTER_S = 15;
  function botStatusView(status) {
    var state = status && status.state ? String(status.state).toUpperCase() : null;
    var detail = botDetail(status);
    if (state === "RUNNING") {
      var age = status.heartbeat_age_s;
      var dead = typeof age === "number" && age > HEARTBEAT_DEAD_AFTER_S;
      if (dead) return { label: "BOT DEAD", cls: "dead", canHalt: true, detail: "no heartbeat for " + Math.round(age) + "s · " + detail };
      var paused = status.orders && status.orders.paused;
      var feed = String(status.feed_state || "").toUpperCase();
      if (paused) return { label: "BOT PAUSED", cls: "quiescing", canHalt: true, detail: detail };
      if (feed === "DEAD" || feed === "DEGRADED") return { label: "BOT RUNNING · FEED " + feed, cls: "degraded", canHalt: true, detail: detail };
      return { label: "BOT RUNNING", cls: "running", canHalt: true, detail: detail };
    }
    if (state === "QUIESCING") return { label: "BOT HALTING…", cls: "quiescing", canHalt: false, detail: detail };
    if (state === "STOPPED") return { label: "BOT HALTED", cls: "stopped", canHalt: false, detail: detail };
    return { label: "BOT OFFLINE", cls: "offline", canHalt: false, detail: "no answer from the bot control endpoint" };
  }

  function botDetail(status) {
    if (!status) return "";
    var parts = [];
    if (status.feed_state) parts.push("feed " + String(status.feed_state).toLowerCase() + (typeof status.feed_age_s === "number" ? " (" + status.feed_age_s + "s since last event)" : ""));
    if (typeof status.heartbeat_age_s === "number") parts.push("heartbeat " + status.heartbeat_age_s + "s ago");
    var o = status.orders || {};
    if (typeof o.active_orders === "number") parts.push(o.active_orders + " resting, " + (o.unknown_orders || 0) + " unknown");
    if (o.paused) parts.push("paused: " + (o.pause_reason || "order-rate anomaly") + " (" + (o.pause_remaining_s || 0) + "s left)");
    return parts.join(" · ");
  }

  // One-line outcome of POST /bot/halt for the operator.
  function haltResultText(resp) {
    var n = resp && resp.unresolved !== undefined ? Number(resp.unresolved) : 0;
    if (n > 0) return "Halted. " + n + " order" + (n === 1 ? "" : "s") + " could not be confirmed cancelled; check the exchange.";
    return "Halted. All open orders cancelled.";
  }

  return { positionRows, FeedCursor, escapeHtml, botStatusView, haltResultText, HEARTBEAT_DEAD_AFTER_S };
});
