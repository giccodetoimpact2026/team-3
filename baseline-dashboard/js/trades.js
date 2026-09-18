// Order lifecycle, session counters, cost basis and CSV export (P4/P5). Pure
// and DOM-free; unit-tested in tests/trades.test.js. Depends on format.js.
//
// Vocabulary: the exchange reports PENDING/OPEN/PARTIALLY_FILLED/FILLED/
// CANCELLED/REJECTED. Gerald's export spec uses ACKNOWLEDGED where the
// exchange says OPEN; every other code is identical.
(function (root, factory) {
  const api = factory(
    typeof module !== "undefined" && module.exports ? require("./format.js") : root
  );
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.Trades = api;
  }
})(typeof self !== "undefined" ? self : this, function (fmt) {
  var CSV_COLUMNS = [
    "TradeID", "Timestamp_SGT", "Instrument", "Direction", "Quantity", "LimitPrice",
    "FillPrice", "FillQuantity", "RemainingQty", "Status", "PnLImpact", "CumulativePnL",
  ];
  var CSV_HEADER = CSV_COLUMNS.join(",");

  function normalizeStatus(s) {
    s = String(s || "").toUpperCase();
    return s === "OPEN" ? "ACKNOWLEDGED" : s;
  }

  function byTime(a, b) {
    return a.at < b.at ? -1 : a.at > b.at ? 1 : 0;
  }

  // Records every status transition the dashboard actually observed, keyed by
  // trading-window id so a new session never inherits old rows. Storage is
  // injected (localStorage in the browser) and every access is guarded: it
  // may be unavailable or throw in private mode.
  function LifecycleHistory(sessionId, storage) {
    this.key = "lifecycle:" + (sessionId || "");
    this.storage = storage;
    this.last = {}; // orderId -> last normalized status
    this.list = []; // {orderId, status, at}
    this._load();
  }
  LifecycleHistory.prototype._load = function () {
    try {
      var raw = this.storage && this.storage.getItem(this.key);
      if (!raw) return;
      var saved = JSON.parse(raw);
      var self = this;
      (saved || []).forEach(function (e) {
        self.list.push(e);
        self.last[e.orderId] = e.status;
      });
    } catch (_) { /* unreadable storage: start empty */ }
  };
  LifecycleHistory.prototype._save = function () {
    try { this.storage && this.storage.setItem(this.key, JSON.stringify(this.list)); } catch (_) { /* full or blocked */ }
  };
  LifecycleHistory.prototype.observe = function (orders) {
    var self = this;
    var changed = false;
    (orders || []).forEach(function (o) {
      var status = normalizeStatus(o.status);
      if (self.last[o.id] === status) return;
      self.last[o.id] = status;
      self.list.push({ orderId: o.id, status: status, at: o.updated_at || o.created_at || "", remaining: o.remaining_qty });
      changed = true;
    });
    if (changed) {
      this.list.sort(byTime);
      this._save();
    }
    return changed;
  };
  LifecycleHistory.prototype.entries = function () {
    return this.list.slice();
  };
  LifecycleHistory.prototype.reached = function (status) {
    var ids = {};
    this.list.forEach(function (e) { if (e.status === status) ids[e.orderId] = true; });
    return ids;
  };

  // Session totals Gerald wants always visible: buy orders, sell orders,
  // orders that were ever partially filled, rejected orders.
  function counters(orders, history) {
    var c = { buy: 0, sell: 0, partial: 0, rejected: 0 };
    var partial = history ? history.reached("PARTIALLY_FILLED") : {};
    (orders || []).forEach(function (o) {
      if (String(o.side).toLowerCase() === "buy") c.buy++;
      else if (String(o.side).toLowerCase() === "sell") c.sell++;
      var s = normalizeStatus(o.status);
      if (s === "REJECTED") c.rejected++;
      if (s === "PARTIALLY_FILLED") partial[o.id] = true;
    });
    c.partial = Object.keys(partial).length;
    return c;
  }

  function chronological(trades) {
    return (trades || []).slice().sort(function (a, b) {
      return a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
    });
  }

  // The exchange only reports the *current* volume-weighted cost basis. To book
  // realized P&L per sell fill we need the basis at the time of each fill, so
  // walk the session's fills backwards from the current position: undoing a
  // buy lowers quantity and un-averages the price; undoing a sell only restores
  // quantity (a sell never changes the average). Result: basis at session open.
  function startingBasis(positions, trades) {
    var basis = {};
    (positions || []).forEach(function (p) {
      basis[p.symbol] = { quantity: p.quantity || 0, avg_cost: p.avg_cost || 0 };
    });
    chronological(trades).reverse().forEach(function (t) {
      var b = basis[t.symbol] || (basis[t.symbol] = { quantity: 0, avg_cost: 0 });
      if (String(t.side).toLowerCase() === "buy") {
        var prevQty = b.quantity - t.quantity;
        if (prevQty <= 0) { b.quantity = 0; b.avg_cost = 0; return; }
        b.avg_cost = Math.round((b.quantity * b.avg_cost - t.quantity * t.price) / prevQty);
        b.quantity = prevQty;
      } else {
        b.quantity += t.quantity;
      }
    });
    return basis;
  }

  function cents(n) { return fmt.centsToDecimal(n); }

  // One row per observed lifecycle step, chronological:
  //   ACKNOWLEDGED at created_at (unless the order was rejected outright),
  //   one row per fill (PARTIALLY_FILLED until the last one, then FILLED),
  //   CANCELLED / REJECTED at updated_at.
  // Sell fills book (fill price − basis) × qty; buys book 0 and move the basis.
  function csvRows(orders, trades, positions) {
    var basis = startingBasis(positions, trades);
    var fillsByOrder = {};
    chronological(trades).forEach(function (t) {
      (fillsByOrder[t.order_id] = fillsByOrder[t.order_id] || []).push(t);
    });
    var events = []; // {at, seq, row}
    var seq = 0;
    (orders || []).forEach(function (o) {
      var status = normalizeStatus(o.status);
      var side = String(o.side).toUpperCase();
      var base = { Instrument: o.symbol, Direction: side, Quantity: String(o.quantity), LimitPrice: cents(o.price) };
      function push(at, row) { events.push({ at: at, seq: seq++, row: Object.assign({}, base, row) }); }
      if (status === "REJECTED") {
        push(o.updated_at || o.created_at, { TradeID: o.id, FillPrice: "", FillQuantity: "", RemainingQty: String(o.quantity), Status: "REJECTED", _pnl: 0 });
        return;
      }
      push(o.created_at, { TradeID: o.id, FillPrice: "", FillQuantity: "", RemainingQty: String(o.quantity), Status: "ACKNOWLEDGED", _pnl: 0 });
      var remaining = o.quantity;
      (fillsByOrder[o.id] || []).forEach(function (t) {
        remaining -= t.quantity;
        push(t.timestamp, {
          TradeID: t.trade_id, FillPrice: cents(t.price), FillQuantity: String(t.quantity),
          RemainingQty: String(Math.max(remaining, 0)), Status: remaining > 0 ? "PARTIALLY_FILLED" : "FILLED",
          _fill: t,
        });
      });
      if (status === "CANCELLED") {
        push(o.updated_at || o.created_at, { TradeID: o.id, FillPrice: "", FillQuantity: "", RemainingQty: String(o.remaining_qty), Status: "CANCELLED", _pnl: 0 });
      }
    });
    events.sort(function (a, b) { return byTime(a, b) || a.seq - b.seq; });

    var cumulative = 0;
    return events.map(function (e) {
      var r = e.row;
      var pnl = 0;
      if (r._fill) {
        var t = r._fill;
        var b = basis[t.symbol] || (basis[t.symbol] = { quantity: 0, avg_cost: 0 });
        if (String(t.side).toLowerCase() === "buy") {
          var q = b.quantity + t.quantity;
          b.avg_cost = q > 0 ? Math.round((b.quantity * b.avg_cost + t.quantity * t.price) / q) : 0;
          b.quantity = q;
        } else {
          pnl = (t.price - b.avg_cost) * t.quantity;
          b.quantity -= t.quantity;
          if (b.quantity <= 0) { b.quantity = 0; b.avg_cost = 0; }
        }
      }
      cumulative += pnl;
      var out = {};
      CSV_COLUMNS.forEach(function (c) { out[c] = r[c] !== undefined ? r[c] : ""; });
      out.Timestamp_SGT = fmt.formatSgt(e.at);
      out.PnLImpact = cents(pnl);
      out.CumulativePnL = cents(cumulative);
      return out;
    });
  }

  // Per-instrument P&L straight from GET /portfolio, in the same shape as the
  // MCP get_pnl tool (dollars, aggregate + by_instrument) so the table renders
  // identically when the MCP server is down. `source` says where it came from.
  function pnlFromPortfolio(portfolio, symbols, now) {
    var p = portfolio || {};
    var by = {};
    (p.positions || []).forEach(function (pos) {
      var r = (pos.realized_pnl || 0) / 100, u = (pos.unrealized_pnl || 0) / 100;
      by[pos.symbol] = { realized_pnl: r, unrealized_pnl: u, total_pnl: r + u };
    });
    (symbols || []).forEach(function (s) {
      if (!by[s]) by[s] = { realized_pnl: 0, unrealized_pnl: 0, total_pnl: 0 };
    });
    var r = (p.realized_pnl || 0) / 100, u = (p.unrealized_pnl || 0) / 100;
    return {
      source: "GET /portfolio (direct; MCP unavailable)",
      as_of: fmt.formatSgt(now || new Date()),
      data: { aggregate: { realized_pnl: r, unrealized_pnl: u, total_pnl: r + u }, by_instrument: by },
    };
  }

  function csvField(v) {
    v = v === undefined || v === null ? "" : String(v);
    return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }

  function toCsv(rows) {
    var lines = [CSV_HEADER];
    (rows || []).forEach(function (r) {
      lines.push(CSV_COLUMNS.map(function (c) { return csvField(r[c]); }).join(","));
    });
    return lines.join("\r\n") + "\r\n";
  }

  return { CSV_HEADER: CSV_HEADER, CSV_COLUMNS: CSV_COLUMNS, normalizeStatus, LifecycleHistory, counters, startingBasis, csvRows, toCsv, pnlFromPortfolio };
});
