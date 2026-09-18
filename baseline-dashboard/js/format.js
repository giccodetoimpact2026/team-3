// Pure formatting helpers, kept in their own file so they can be unit-tested in
// Node (see tests/) and loaded as browser globals. No DOM, no jQuery.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.formatCurrency = api.formatCurrency; // browser globals
    root.formatCurrencyOrDash = api.formatCurrencyOrDash;
    root.formatSgt = api.formatSgt;
    root.centsToDecimal = api.centsToDecimal;
    root.formatDollars = api.formatDollars;
  }
})(typeof self !== "undefined" ? self : this, function () {
  // Money is INTEGER CENTS on the exchange wire; render as dollars ($1,510.00).
  function formatCurrency(cents) {
    const dollars = (cents || 0) / 100;
    const sign = dollars < 0 ? "-" : "";
    const abs = Math.abs(dollars).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return sign + "$" + abs;
  }

  // Same as formatCurrency, but a missing value is shown as a dash rather than
  // $0.00, so "no data" is distinguishable from "zero".
  function formatCurrencyOrDash(cents) {
    return cents === undefined || cents === null ? "\u2014" : formatCurrency(cents);
  }

  // Anything shown to a user or exported must be SGT (UTC+8) with millisecond
  // precision and an explicit label: YYYY-MM-DD HH:MM:SS.mmm SGT. Computed by
  // offset rather than toLocaleString so the output is byte-stable across
  // browsers and Node ICU builds.
  var SGT_OFFSET_MS = 8 * 60 * 60 * 1000;
  function pad(n, width) {
    var s = String(n);
    while (s.length < width) s = "0" + s;
    return s;
  }
  function formatSgt(input) {
    var d = input instanceof Date ? input : new Date(input);
    var t = new Date(d.getTime() + SGT_OFFSET_MS);
    return (
      t.getUTCFullYear() + "-" + pad(t.getUTCMonth() + 1, 2) + "-" + pad(t.getUTCDate(), 2) +
      " " + pad(t.getUTCHours(), 2) + ":" + pad(t.getUTCMinutes(), 2) + ":" + pad(t.getUTCSeconds(), 2) +
      "." + pad(t.getUTCMilliseconds(), 3) + " SGT"
    );
  }

  // MCP tools return dollars as floats, not cents (see mcp-server-reference).
  function formatDollars(dollars) {
    return formatCurrency(Math.round((dollars || 0) * 100));
  }

  // Export form: plain number, two decimals, period separator, no grouping,
  // no currency sign (BUG-016 guardrail). 13628 -> "136.28".
  function centsToDecimal(cents) {
    var n = Math.round(cents || 0);
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    var whole = Math.floor(abs / 100);
    var frac = abs % 100;
    return sign + whole + "." + (frac < 10 ? "0" : "") + frac;
  }

  return { formatCurrency, formatCurrencyOrDash, formatSgt, formatDollars, centsToDecimal };
});
