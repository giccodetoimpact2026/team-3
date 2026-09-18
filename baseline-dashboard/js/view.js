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

  return { positionRows, FeedCursor, escapeHtml };
});
