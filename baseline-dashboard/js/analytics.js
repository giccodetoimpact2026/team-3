// Persona analytics, all pure and DOM-free (tests/analytics.test.js):
//   heatTiles        Raymond's portfolio heat map (colour + number per instrument)
//   fillRateRows     Adeline's per-instrument fill-rate table
//   exposureRows     Priya / Clarissa: position against its cap, share of book
//   netExposure      Farrukh: net long exposure as a share of total value
//   evaluateAlerts   Priya's alerts: drawdown, over-cap, instrument drop, bot health
//   performanceRows  Farrukh's trading performance breakdown per instrument
//   sessionSummary   Linda / Winston: end-of-session one-liner
//   openOrderRows    Traders / risk: what is resting on the book right now
// Money from the exchange is integer cents; MCP P&L is dollars (floats).
(function (root, factory) {
  const api = factory(
    typeof module !== "undefined" && module.exports ? require("./format.js") : root,
    typeof module !== "undefined" && module.exports ? require("./trades.js") : root.Trades
  );
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.Analytics = api;
  }
})(typeof self !== "undefined" ? self : this, function (fmt, Trades) {
  var DASH = "—";

  function heatTiles(byInstrument, symbols) {
    var by = byInstrument || {};
    var max = 0;
    (symbols || []).forEach(function (s) {
      var v = by[s] && by[s].total_pnl;
      if (typeof v === "number" && Math.abs(v) > max) max = Math.abs(v);
    });
    return (symbols || []).map(function (s) {
      var v = by[s] && by[s].total_pnl;
      if (typeof v !== "number") return { symbol: s, label: DASH, cls: "flat", intensity: 0 };
      return { symbol: s, label: fmt.formatDollars(v), cls: v > 0 ? "pos" : v < 0 ? "neg" : "flat", intensity: max ? Math.abs(v) / max : 0 };
    });
  }

  function fillRateRows(orders, history, symbols) {
    var per = {};
    (symbols || []).forEach(function (s) { per[s] = { symbol: s, sent: 0, filled: 0, partial: 0, rejected: 0 }; });
    var everPartial = history ? history.reached("PARTIALLY_FILLED") : {};
    var seen = {};
    (orders || []).forEach(function (o) {
      var r = per[o.symbol];
      if (!r) return;
      seen[o.id] = true;
      r.sent++;
      var s = Trades.normalizeStatus(o.status);
      if (s === "FILLED") r.filled++;
      else if (s === "PARTIALLY_FILLED" || everPartial[o.id]) r.partial++;
      if (s === "REJECTED") r.rejected++;
    });
    // Rejected submits never become exchange orders; the bot journal has them.
    (history ? history.entries() : []).forEach(function (e) {
      if (e.status !== "REJECTED" || seen[e.orderId] || !per[e.symbol]) return;
      seen[e.orderId] = true;
      per[e.symbol].sent++;
      per[e.symbol].rejected++;
    });
    return (symbols || []).map(function (s) {
      var r = per[s];
      r.fillRate = r.sent ? Math.round((r.filled / r.sent) * 100) + "%" : DASH;
      return r;
    });
  }

  function exposureRows(portfolio, caps, symbols) {
    var p = portfolio || {};
    var total = p.total_value || 0;
    var pos = {};
    (p.positions || []).forEach(function (x) { pos[x.symbol] = x; });
    return (symbols || []).map(function (s) {
      var x = pos[s] || {};
      var qty = x.quantity || 0;
      var mv = x.market_value || 0;
      var cap = caps && typeof caps[s] === "number" ? caps[s] : null;
      return {
        symbol: s,
        quantity: qty,
        cap: cap,
        pctOfCap: cap ? Math.round((qty / cap) * 100) : cap === 0 ? null : null,
        overCap: cap !== null && cap > 0 && qty > cap,
        marketValue: mv,
        share: total ? Math.round((mv / total) * 1000) / 10 : 0,
      };
    });
  }

  function netExposure(portfolio) {
    var p = portfolio || {};
    var long = 0;
    (p.positions || []).forEach(function (x) { long += x.market_value || 0; });
    var total = p.total_value || 0;
    return { longValue: long, totalValue: total, pct: total ? Math.round((long / total) * 100) : 0 };
  }

  var DEFAULT_THRESHOLDS = { drawdownPct: 15, instrumentDropPct: 10, heartbeatDeadS: 15 };

  function evaluateAlerts(ctx) {
    ctx = ctx || {};
    var th = Object.assign({}, DEFAULT_THRESHOLDS, ctx.thresholds || {});
    var alerts = [];
    var p = ctx.portfolio;

    if (p && ctx.startingValue > 0 && typeof p.total_value === "number") {
      var dd = ((ctx.startingValue - p.total_value) / ctx.startingValue) * 100;
      if (dd >= th.drawdownPct) {
        alerts.push({ level: "critical", code: "DRAWDOWN", message: "Portfolio drawdown " + dd.toFixed(1) + "% of starting capital (limit " + th.drawdownPct + "%)" });
      }
    }
    if (p && ctx.caps) {
      (p.positions || []).forEach(function (x) {
        var cap = ctx.caps[x.symbol];
        if (typeof cap === "number" && cap > 0 && (x.quantity || 0) > cap) {
          alerts.push({ level: "warning", code: "OVER_CAP", message: x.symbol + " position " + x.quantity + " exceeds its cap of " + cap });
        }
      });
    }
    if (ctx.startPrices && ctx.lastPrices) {
      Object.keys(ctx.startPrices).forEach(function (s) {
        var start = ctx.startPrices[s], last = ctx.lastPrices[s];
        if (!(start > 0) || !(last > 0)) return;
        var drop = ((start - last) / start) * 100;
        if (drop >= th.instrumentDropPct) {
          alerts.push({ level: "warning", code: "INSTRUMENT_DROP", message: s + " down " + drop.toFixed(1) + "% from its session start price" });
        }
      });
    }
    if ("bot" in ctx) {
      var b = ctx.bot;
      if (b === null) {
        alerts.push({ level: "warning", code: "BOT_OFFLINE", message: "Bot is offline (no status from the control endpoint)" });
      } else if (b) {
        var dead = b.feed_state === "DEAD" || (typeof b.heartbeat_age_s === "number" && b.heartbeat_age_s > th.heartbeatDeadS);
        if (dead) {
          alerts.push({ level: "critical", code: "BOT_DEAD", message: "Bot is not alive: feed " + (b.feed_state || "?") + (typeof b.heartbeat_age_s === "number" ? ", last heartbeat " + b.heartbeat_age_s + "s ago" : "") });
        } else if (b.orders && b.orders.paused) {
          alerts.push({ level: "warning", code: "BOT_PAUSED", message: "Bot paused new orders: " + (b.orders.pause_reason || "anomaly") });
        }
      }
    }
    return alerts;
  }

  function chronological(trades) {
    return (trades || []).slice().sort(function (a, b) {
      return a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
    });
  }

  // Realized P&L per sell fill against the volume-weighted basis at that time
  // (same walk as the CSV export), aggregated into a per-instrument breakdown.
  function performanceRows(trades, positions, symbols) {
    var basis = Trades.startingBasis(positions, trades);
    var per = {};
    (symbols || []).forEach(function (s) { per[s] = { fills: 0, wins: 0, losses: 0, sumWin: 0, sumLoss: 0 }; });
    chronological(trades).forEach(function (t) {
      var r = per[t.symbol];
      var b = basis[t.symbol] || (basis[t.symbol] = { quantity: 0, avg_cost: 0 });
      if (r) r.fills++;
      if (String(t.side).toLowerCase() === "buy") {
        var q = b.quantity + t.quantity;
        b.avg_cost = q > 0 ? Math.round((b.quantity * b.avg_cost + t.quantity * t.price) / q) : 0;
        b.quantity = q;
        return;
      }
      var pnl = (t.price - b.avg_cost) * t.quantity;
      b.quantity -= t.quantity;
      if (b.quantity <= 0) { b.quantity = 0; b.avg_cost = 0; }
      if (!r) return;
      if (pnl > 0) { r.wins++; r.sumWin += pnl; }
      else if (pnl < 0) { r.losses++; r.sumLoss += pnl; }
    });
    return (symbols || []).map(function (s) {
      var r = per[s];
      var decided = r.wins + r.losses;
      return {
        symbol: s,
        fills: r.fills,
        wins: r.wins,
        losses: r.losses,
        winRate: decided ? Math.round((r.wins / decided) * 100) + "%" : DASH,
        avgWin: r.wins ? Math.round(r.sumWin / r.wins) : null,
        avgLoss: r.losses ? Math.round(r.sumLoss / r.losses) : null,
        profitFactor: r.sumLoss < 0 ? (r.sumWin / -r.sumLoss).toFixed(2) : r.sumWin > 0 ? "∞" : DASH,
        realized: r.sumWin + r.sumLoss,
      };
    });
  }

  // Resting orders only (OPEN -> ACKNOWLEDGED, PENDING, PARTIALLY_FILLED),
  // newest first, with how much has filled and how long they have been working.
  var RESTING = { ACKNOWLEDGED: true, PENDING: true, PARTIALLY_FILLED: true };
  function openOrderRows(orders, nowMs) {
    return (orders || [])
      .map(function (o) { return { o: o, status: Trades.normalizeStatus(o.status) }; })
      .filter(function (x) { return RESTING[x.status]; })
      .sort(function (a, b) { return a.o.created_at < b.o.created_at ? 1 : a.o.created_at > b.o.created_at ? -1 : 0; })
      .map(function (x) {
        var o = x.o;
        var remaining = o.remaining_qty === undefined || o.remaining_qty === null ? o.quantity : o.remaining_qty;
        var created = Date.parse(o.created_at);
        return {
          id: o.id,
          symbol: o.symbol,
          side: String(o.side || "").toUpperCase(),
          quantity: o.quantity,
          filled: Math.max(0, (o.quantity || 0) - remaining),
          remaining: remaining,
          price: o.price,
          status: x.status,
          ageS: isNaN(created) ? null : Math.max(0, Math.round((nowMs - created) / 1000)),
        };
      });
  }

  function sessionSummary(ctx) {
    ctx = ctx || {};
    var p = ctx.portfolio || {};
    var by = ctx.pnlByInstrument || {};
    var best = null, worst = null;
    Object.keys(by).forEach(function (s) {
      var v = by[s] && by[s].total_pnl;
      if (typeof v !== "number") return;
      if (best === null || v > by[best].total_pnl) best = s;
      if (worst === null || v < by[worst].total_pnl) worst = s;
    });
    return {
      totalPnl: (p.realized_pnl || 0) + (p.unrealized_pnl || 0),
      fills: (p.trades || []).length,
      best: best,
      worst: worst,
      alerts: (ctx.alerts || []).length,
    };
  }

  // One line per component for the system-health strip (Winston: "one screen
  // tells me if the system is healthy"). States: ok | warn | bad | unknown.
  function age(now, t) { return t ? Math.max(0, Math.round((now - t) / 1000)) : null; }
  function healthItems(ctx, now) {
    ctx = ctx || {};
    now = now === undefined ? Date.now() : now;
    var items = [];

    var b = ctx.bot;
    if (b === null) items.push({ id: "bot", label: "Bot", state: "bad", detail: "offline: no answer from the control endpoint" });
    else if (!b) items.push({ id: "bot", label: "Bot", state: "unknown", detail: "not polled yet" });
    else {
      var st = String(b.state || "").toUpperCase();
      var dead = b.feed_state === "DEAD" || (typeof b.heartbeat_age_s === "number" && b.heartbeat_age_s > 15);
      if (st === "RUNNING" && dead) items.push({ id: "bot", label: "Bot", state: "bad", detail: "running but not alive: feed " + (b.feed_state || "?") + (typeof b.heartbeat_age_s === "number" ? ", heartbeat " + b.heartbeat_age_s + "s ago" : "") });
      else if (st === "RUNNING" && b.orders && b.orders.paused) items.push({ id: "bot", label: "Bot", state: "warn", detail: "paused: " + (b.orders.pause_reason || "anomaly") });
      else if (st === "RUNNING") items.push({ id: "bot", label: "Bot", state: "ok", detail: "running" + (typeof b.heartbeat_age_s === "number" ? ", heartbeat " + b.heartbeat_age_s + "s ago" : "") + (b.feed_state ? ", feed " + String(b.feed_state).toLowerCase() : "") });
      else if (st === "STOPPED" || st === "QUIESCING") items.push({ id: "bot", label: "Bot", state: "warn", detail: st === "STOPPED" ? "halted" : "halting" });
      else items.push({ id: "bot", label: "Bot", state: "unknown", detail: st || "unknown state" });
    }

    var evAge = age(now, ctx.lastEventAt);
    if (ctx.feedOnline === false) items.push({ id: "feed", label: "Market feed", state: "bad", detail: "SSE disconnected, reconnecting" });
    else if (ctx.feedOnline === undefined) items.push({ id: "feed", label: "Market feed", state: "unknown", detail: "connecting" });
    else if (evAge !== null && evAge > 20) items.push({ id: "feed", label: "Market feed", state: "warn", detail: "connected but no events for " + evAge + "s" });
    else items.push({ id: "feed", label: "Market feed", state: "ok", detail: evAge === null ? "connected" : "last event " + evAge + "s ago" });

    var exAge = age(now, ctx.exchangeOkAt);
    if (ctx.exchangeError === 429) items.push({ id: "exchange", label: "Exchange", state: "warn", detail: "rate limited (429), showing data from " + (exAge === null ? "?" : exAge + "s ago") });
    else if (ctx.exchangeError) items.push({ id: "exchange", label: "Exchange", state: "bad", detail: "last read failed (" + ctx.exchangeError + ")" + (exAge !== null ? ", data " + exAge + "s old" : "") });
    else if (exAge === null) items.push({ id: "exchange", label: "Exchange", state: "unknown", detail: "no read yet" });
    else if (exAge > 30) items.push({ id: "exchange", label: "Exchange", state: "warn", detail: "last successful read " + exAge + "s ago" });
    else items.push({ id: "exchange", label: "Exchange", state: "ok", detail: "last read " + exAge + "s ago" });

    if (ctx.mcpFailedAt && ctx.mcpFailedAt >= (ctx.mcpOkAt || 0)) items.push({ id: "mcp", label: "MCP server", state: "bad", detail: "unreachable" + (ctx.mcpOkAt ? ", last answer " + age(now, ctx.mcpOkAt) + "s ago" : "") });
    else if (ctx.mcpOkAt) items.push({ id: "mcp", label: "MCP server", state: "ok", detail: "answered " + age(now, ctx.mcpOkAt) + "s ago" });
    else items.push({ id: "mcp", label: "MCP server", state: "unknown", detail: "not called yet" });

    var l = ctx.llm || {};
    items.push({ id: "llm", label: "LLM service", state: l.state || "unknown", detail: l.detail || "not called yet" });
    return items;
  }

  return { healthItems: healthItems, heatTiles: heatTiles, fillRateRows: fillRateRows, exposureRows: exposureRows, netExposure: netExposure, evaluateAlerts: evaluateAlerts, performanceRows: performanceRows, sessionSummary: sessionSummary, openOrderRows: openOrderRows, DEFAULT_THRESHOLDS: DEFAULT_THRESHOLDS };
});
